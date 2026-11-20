"""Exercise invitation ordering, thread promotion and search with a disposable DB."""
import os
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
    call("close",id=mid,body="LRU",model="claude-opus-5-5")
    post=call("show",id=mid)
    assert [r["model"] for r in post["replies"]]==["claude-opus-5-5","gpt-5.6-terra"] and post["decision_model"]=="claude-opus-5-5"

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
print("Passed lifecycle, stale reply, promotion, scoped search, model and rework checks.")
