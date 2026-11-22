"""Exercise invitation ordering, thread promotion and search with a disposable DB."""
import os
import shutil
import subprocess
from pathlib import Path
import sys
import tempfile
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"board"))
os.environ["THREADWELL_PREVIEW"]="1"

with tempfile.TemporaryDirectory() as tmp:
    os.environ["THREADWELL_DB"]=tmp+"/smoke.sqlite"
    import store
    from server import index

    store.initialize()
    def call(op, **fields):
        with store.connect() as db:
            return store.dispatch(db,dict(op=op,**fields))

    pid=call("open",id=str(uuid.uuid4()),project="General",starter="codex",host="test",title="Choose a parser",body="Compare error handling")
    invited=call("claim",host="test")
    assert invited["id"]==pid and call("claim",host="test") is None
    call("reply",id=pid,lease=invited["lease"],body="The tokenizer exposes offsets.")
    call("ask",id=pid,body="Verify one more case")
    second=call("claim",host="test")
    try:
        call("reply",id=pid,lease=invited["lease"],body="old reply")
        raise AssertionError("Old lease accepted")
    except ValueError:
        pass
    call("close",id=pid,body="Keep the existing parser.")
    for op in ["reply","failed","ask","close"]:
        try:
            call(op,id=pid,lease=second["lease"],body="late")
            raise AssertionError("Closed post accepted "+op)
        except ValueError:
            pass
    call("move",id=pid,project="Parsing")
    post=call("show",id=pid)
    assert post["decision"]=="Keep the existing parser." and len(post["replies"])==3
    assert post["phase"]=="done" and post["project"]=="Parsing"
    with store.connect() as db:
        args=dict(q=["tokenizer"],project=["Parsing"],status=["done"])
        assert index(db,args)["posts"][0]["id"]==pid
        args["project"]=["General"]
        assert not index(db,args)["posts"]
    # Models travel with messages and decisions.
    mid=call("open",id=str(uuid.uuid4()),project="General",starter="claude",host="test",title="Pick a cache",body="Compare",model="claude-opus-5-5")
    lease=call("claim",host="test")["lease"]
    call("reply",id=mid,lease=lease,body="Use LRU",model="gpt-5.6-terra")
    call("close",id=mid,body="LRU",model="claude-opus-5-5",effort="high")
    post=call("show",id=mid)
    assert [r["model"] for r in post["replies"]]==["claude-opus-5-5","gpt-5.6-terra"] and post["decision_model"]=="claude-opus-5-5" and post["decision_effort"]=="high"

    # Owner rework: only on closed threads, once at a time, keeps the old decision in the thread.
    try:
        call("rework",id=call("open",id=str(uuid.uuid4()),project="General",starter="codex",host="other",title="t",body="b"),body="Redo")
        raise AssertionError("Rework accepted on an open thread")
    except ValueError:
        pass
    call("rework",id=mid,body="LRU thrashes on scans; reconsider")
    try:
        call("rework",id=mid,body="again")
        raise AssertionError("Second rework accepted while one is queued")
    except ValueError:
        pass
    post=call("show",id=mid)
    assert post["phase"]=="answered" and post["rework"]=="queued" and "rework_lease" not in post
    assert [r["kind"] for r in post["replies"][-2:]]==["decision","rework"] and post["replies"][-2]["body"]=="LRU"
    assert call("claimrework",host="elsewhere") is None
    run=call("claimrework",host="test")
    assert run["id"]==mid and call("claimrework",host="test") is None
    assert not call("reworkpulse",id=mid,lease="wrong") and call("reworkpulse",id=mid,lease=run["rework_lease"])
    call("ask",id=mid,body="Would ARC fit?")
    peer=call("claim",host="test")
    call("reply",id=peer["id"],lease=peer["lease"],body="ARC fits",model="gpt-5.6-terra")
    call("close",id=mid,body="Switch to ARC",model="claude-opus-5-5")
    assert call("reworked",id=mid,lease=run["rework_lease"],body="ignored after close")
    post=call("show",id=mid)
    assert post["phase"]=="done" and post["rework"] is None and post["decision"]=="Switch to ARC"
    assert post["replies"][-1]["author"]=="codex"

    # A run that ends without closing restores the previous decision with a visible note.
    call("rework",id=mid,body="Actually measure first")
    run=call("claimrework",host="test")
    call("reworked",id=mid,lease=run["rework_lease"],body="Ended without a decision")
    post=call("show",id=mid)
    assert post["phase"]=="done" and post["decision"]=="Switch to ARC" and post["replies"][-1]["author"]=="launcher"
    call("rework",id=mid,body="Measure again")
    run=call("claimrework",host="test")
    call("reworked",id=mid,lease=run["rework_lease"],body="Still nothing")
    kinds=[r["body"] for r in call("show",id=mid)["replies"] if r["kind"]=="decision"]
    assert kinds==["LRU","Switch to ARC"], kinds

    # A run that stops pulsing is swept.
    call("rework",id=mid,body="Try once more")
    run=call("claimrework",host="test")
    with store.connect() as db:
        db.execute("update posts set rework_seen=rework_seen-200 where id=?",(mid,))
    assert call("claimrework",host="test") is None
    post=call("show",id=mid)
    assert post["phase"]=="done" and post["rework"] is None and "stopped responding" in post["replies"][-1]["body"]
    assert not call("reworkpulse",id=mid,lease=run["rework_lease"])
    # `poll`: one call per worker cycle. Idle polls write nothing once the heartbeat is fresh.
    def writes():
        with store.connect() as db:
            db.execute("pragma wal_checkpoint(truncate)")
        return os.path.getsize(os.environ["THREADWELL_DB"]+"-wal")
    assert call("poll",host="quiet")=={}
    with store.connect() as db:
        assert db.execute("select seen from hosts where name='quiet'").fetchone()
    before=writes()
    for _ in range(5):
        assert call("poll",host="quiet")=={}
    assert writes()==before, "idle poll wrote to the database"
    with store.connect() as db:
        db.execute("update hosts set seen=seen-store.HEARTBEAT-1 where name='quiet'".replace("store.HEARTBEAT",str(store.HEARTBEAT)))
    call("poll",host="quiet")
    with store.connect() as db:
        assert db.execute("select seen from hosts where name='quiet'").fetchone()[0]>=int(__import__("time").time())-2
    pid=call("open",id=str(uuid.uuid4()),project="General",starter="claude",host="quiet",title="Poll",body="b")
    call("rework",id=mid,body="Poll picks this up")  # mid lives on host "test"
    work=call("poll",host="quiet")
    assert work["post"]["id"]==pid and "rework" not in work
    both=call("poll",host="test")
    assert both["rework"]["id"]==mid and "post" not in both
    call("reworked",id=mid,lease=both["rework"]["rework_lease"],body="done")
    # A participant that stops pulsing is failed with a note saying so.
    with store.connect() as db:
        db.execute("update posts set started=started-1000 where id=?",(pid,))
    call("poll",host="quiet")
    post=call("show",id=pid)
    assert post["phase"]=="failed" and "stopped responding" in post["replies"][-1]["body"]
    # `show` takes no write lock, so a starter's `wait` never blocks the workers.
    with store.connect() as writer:
        writer.execute("begin immediate")
        assert call("show",id=pid)["id"]==pid
        writer.rollback()

    # Project names: case and space/hyphen/underscore don't matter; the populated spelling wins; empty ones are hidden.
    # Legacy data: both spellings stored, only one holds a post.
    oid=call("open",id=str(uuid.uuid4()),project="General",starter="codex",host="test",title="Backups",body="b")
    with store.connect() as db:
        db.executemany("insert into projects values(?)",[("open-tech-quest",),("Open Tech Quest",)])
        db.execute("update posts set project='Open Tech Quest' where id=?",(oid,))
    nid=call("open",id=str(uuid.uuid4()),project="open_tech  quest",starter="codex",host="test",title="Restore",body="b")
    assert call("show",id=nid)["project"]=="Open Tech Quest"
    assert call("project",project="GENERAL")=="General"
    with store.connect() as db:
        board=index(db,dict(project=["open-tech-quest"]))
        assert board["project"]=="Open Tech Quest" and {p["id"] for p in board["posts"]}=={oid,nid}
        names=[p["name"] for p in index(db,{})["projects"]]
        assert names[0]=="General" and "open-tech-quest" not in names and "Open Tech Quest" in names
    call("project",project="Empty One")
    with store.connect() as db:
        assert "Empty One" not in [p["name"] for p in index(db,{})["projects"]]

node=shutil.which("node")
if node:
    subprocess.run([node,str(Path(__file__).with_name("markdown.test.js"))],check=True)
else:
    print("node not found: skipped the markdown renderer checks.")
print("Passed lifecycle, stale reply, promotion, scoped search, model, rework, poll and project checks.")
