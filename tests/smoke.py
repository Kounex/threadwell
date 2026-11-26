"""Exercise invitation ordering, thread promotion and search with a disposable DB."""
import os
import shutil
import subprocess
from pathlib import Path
import sys
import tempfile
import time
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
    def claim(host):
        return call("poll",host=host,rounds=1).get("post")

    pid=call("open",id=str(uuid.uuid4()),project="General",starter="codex",host="test",title="Choose a parser",body="Compare error handling")
    invited=claim("test")
    assert invited["id"]==pid and claim("test") is None
    call("reply",id=pid,lease=invited["lease"],body="The tokenizer exposes offsets.")
    call("ask",id=pid,body="Verify one more case")
    second=claim("test")
    try:
        call("reply",id=pid,lease=invited["lease"],body="old reply")
        raise AssertionError("Old lease accepted")
    except ValueError:
        pass
    call("close",id=pid,body="Keep the existing parser.",force=True)
    for op in ["reply","failed","ask","close"]:
        try:
            call(op,id=pid,lease=second["lease"],body="late")
            raise AssertionError("Closed post accepted "+op)
        except ValueError:
            pass
    call("move",id=pid,project="Parsing")
    post=call("show",id=pid)
    assert post["decision"]=="Keep the existing parser." and len(post["replies"])==5
    assert [r["body"] for r in post["replies"][-2:]]==["Closed before claude answered.","Closed without the participants reviewing the decision."]
    assert post["phase"]=="done" and post["project"]=="Parsing"
    with store.connect() as db:
        args=dict(q=["tokenizer"],project=["Parsing"],status=["done"])
        assert index(db,args)["posts"][0]["id"]==pid
        args["project"]=["General"]
        assert not index(db,args)["posts"]
    # Models travel with messages and decisions.
    mid=call("open",id=str(uuid.uuid4()),project="General",starter="claude",host="test",title="Pick a cache",body="Compare",model="claude-opus-5-5")
    lease=claim("test")["lease"]
    call("reply",id=mid,lease=lease,body="Use LRU",model="gpt-5.6-terra")
    try:
        call("close",id=mid,body="LRU")
        raise AssertionError("closed without the participants reviewing the decision")
    except ValueError as exc:
        assert "propose" in str(exc)
    call("propose",id=mid,body="Decision: LRU, because ...")
    review=claim("test")
    assert review["agent"]=="codex" and review["replies"][-1]["kind"]=="proposal"
    call("reply",id=mid,lease=review["lease"],body="Agree",model="gpt-5.6-terra")
    call("close",id=mid,body="LRU",model="claude-opus-5-5",effort="high")
    post=call("show",id=mid)
    assert [r["model"] for r in post["replies"]]==["claude-opus-5-5","gpt-5.6-terra",None,"gpt-5.6-terra"] and post["decision_model"]=="claude-opus-5-5" and post["decision_effort"]=="high"

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
    call("propose",id=mid,body="Switch to ARC?")  # after a rework request, the new choice is reviewed again
    peer=claim("test")
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
    call("rework",id=mid,body="Ask codex, then stop")
    run=call("claimrework",host="test")
    call("ask",id=mid,body="Mid-rework question")
    call("reworked",id=mid,lease=run["rework_lease"],body="Gave up")
    post=call("show",id=mid)
    assert post["phase"]=="done" and not post["pending"] and post["agent"] is None and "cancelled" in post["replies"][-1]["body"]
    assert claim("test") is None
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
        db.execute("update hosts set seen=seen-? where name='quiet'",(store.HEARTBEAT,))
    call("poll",host="quiet")
    with store.connect() as db:
        assert db.execute("select seen from hosts where name='quiet'").fetchone()[0]>=int(time.time())-2
    pid=call("open",id=str(uuid.uuid4()),project="General",starter="claude",host="quiet",title="Poll",body="b")
    call("rework",id=mid,body="Poll picks this up")  # mid lives on host "test"
    work=call("poll",host="quiet",rounds=1)
    assert work["post"]["id"]==pid and "rework" not in work
    both=call("poll",host="test",rounds=1)
    assert both["rework"]["id"]==mid and "post" not in both
    call("reworked",id=mid,lease=both["rework"]["rework_lease"],body="done")
    # A participant that stops pulsing loses its turn with a note saying so (here it was the only one).
    with store.connect() as db:
        db.execute("update posts set started=started-1300 where id=?",(pid,))
    call("poll",host="quiet")
    post=call("show",id=pid)
    assert post["phase"]=="failed" and "stopped responding" in post["replies"][-1]["body"] and not post["pending"]
    # `show` takes no write lock, so a starter's `wait` never blocks the workers.
    with store.connect() as writer:
        writer.execute("begin immediate")
        assert call("show",id=pid)["id"]==pid
        writer.rollback()


    # Multi-agent rounds: every participant gets its own turn, one at a time per host, in rotating order.
    assert call("claim",host="test") is None  # workers older than rounds get no work
    early=str(uuid.uuid4())
    call("open",id=early,project="General",starter="claude",host="oldworker",title="Old",body="b",participants=["codex"])
    assert "post" not in call("poll",host="oldworker"), "a worker without rounds was handed a turn"
    assert claim("oldworker")["id"]==early
    tid=str(uuid.uuid4())
    call("open",id=tid,project="General",starter="antigravity",host="trio",title="Three-way",body="Undecided: A or B?",
         participants=["claude","codex","antigravity","claude"])
    post=call("show",id=tid)
    assert post["participants"]==["claude","codex"] and sorted(post["pending"])==["claude","codex"]
    first=claim("trio")
    assert first["agent"]==post["pending"][0] and claim("trio") is None
    other=call("open",id=str(uuid.uuid4()),project="General",starter="codex",host="trio",title="Queued behind",body="b",
               participants=["claude"])
    assert claim("trio") is None, "a second participant started on the same host"
    for op in ("ask","close"):
        try:
            call(op,id=tid,body="too early")
            raise AssertionError(op+" accepted while a round runs")
        except ValueError:
            pass
    call("reply",id=tid,lease=first["lease"],body="A, because ...",model="m1")
    post=call("show",id=tid)
    assert post["phase"]=="queued" and post["pending"]==[a for a in ["claude","codex"] if a!=first["agent"]]
    second=claim("trio")
    assert second["id"]==tid and second["agent"]==post["pending"][0] and second["round_seq"]==post["replies"][0]["seq"]
    call("failed",id=tid,lease=second["lease"],body=second["agent"]+" isn't signed in")
    post=call("show",id=tid)
    assert post["phase"]=="answered" and not post["pending"] and post["replies"][-2]["author"]==first["agent"]
    assert post["round"]=={"answered":[first["agent"]],"skipped":[second["agent"]],"pending":[]}, post["round"]
    queued=claim("trio")  # the older round on this host goes next
    assert queued["id"]==other and queued["agent"]=="claude"
    call("reply",id=other,lease=queued["lease"],body="ok")
    # A follow-up to one agent only; the opener's own turn order rotates per round.
    call("ask",id=tid,body="Retry",to=[second["agent"]])
    retry=claim("trio")
    assert retry["agent"]==second["agent"] and retry["round_seq"]==call("show",id=tid)["replies"][-1]["seq"]
    call("reply",id=tid,lease=retry["lease"],body="B")
    try:
        call("ask",id=tid,body="x",to=["kimi"])
        raise AssertionError("ask to a non-participant accepted")
    except ValueError:
        pass
    call("ask",id=tid,body="Final positions?")
    turn=claim("trio")
    assert claim("trio") is None
    call("close",id=tid,body="A",force=True)
    # Closing needs a reviewed proposal as the latest round: not one nobody reviewed, not one followed by a question.
    rid=str(uuid.uuid4())
    call("open",id=rid,project="General",starter="claude",host="review",title="Review",body="q",participants=["codex","antigravity"])
    def turns(outcome):
        while (turn:=claim("review")):
            call(outcome,id=rid,lease=turn["lease"],body=outcome)
    turns("reply")
    call("propose",id=rid,body="Plan A")
    turns("failed")
    def refused():
        try:
            call("close",id=rid,body="A")
            raise AssertionError("closed an unreviewed decision")
        except ValueError as exc:
            assert "propose" in str(exc)
    refused()
    call("propose",id=rid,body="Retry: review Plan A above",to=["codex"])  # retry one skipped reviewer
    retry=claim("review")
    assert retry["agent"]=="codex" and claim("review") is None
    call("reply",id=rid,lease=retry["lease"],body="Object: B is safer")
    call("ask",id=rid,body="Would B break the importer?")
    turns("reply")
    refused()
    call("propose",id=rid,body="Plan B")
    turns("reply")
    call("close",id=rid,body="B")
    with store.connect() as db:
        rows={p["id"]:p for p in index(db,{})["posts"]}
        assert rows[rid]["pending"]==[]
    post=call("show",id=tid)
    assert post["phase"]=="done" and not post["pending"] and "Closed before" in post["replies"][-2]["body"]
    try:
        call("reply",id=tid,lease=turn["lease"],body="late")
        raise AssertionError("late reply accepted after a forced close")
    except ValueError:
        pass
    # The first speaker differs across threads (seeded by the post id) and across rounds.
    firsts={store.round_order(str(uuid.uuid4()),["a","b","c"],0)[0] for _ in range(60)}
    assert firsts=={"a","b","c"}
    assert store.round_order(tid,["a","b"],0)!=store.round_order(tid,["a","b"],1)
    # A participant that stops responding is skipped and the round moves on to the next agent.
    sid=str(uuid.uuid4())
    call("open",id=sid,project="General",starter="claude",host="slow",title="Sweep",body="b",participants=["codex","antigravity"])
    stuck=claim("slow")
    with store.connect() as db:
        db.execute("update posts set started=started-1300 where id=?",(sid,))
    nxt=claim("slow")
    post=call("show",id=sid)
    assert nxt["id"]==sid and nxt["agent"]!=stuck["agent"] and "stopped responding" in post["replies"][-1]["body"]
    assert not call("pulse",host="slow",id=sid,lease=stuck["lease"]) and call("pulse",host="slow",id=sid,lease=nxt["lease"])
    try:
        call("reply",id=sid,lease=stuck["lease"],body="swept")
        raise AssertionError("swept lease accepted")
    except ValueError:
        pass
    # Legacy two-agent threads keep working: no stored participants means the starter's old peer.
    lid=str(uuid.uuid4())
    call("open",id=lid,project="General",starter="claude",host="legacy",title="Old",body="b")
    with store.connect() as db:
        db.execute("update posts set participants=null,pending=null,round_seq=null where id=?",(lid,))
    old=claim("legacy")
    assert old["agent"]=="codex" and old["participants"]==["codex"]
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

# A board created before multi-agent rounds migrates in place (round_seq once arrived as a TEXT column).
with tempfile.TemporaryDirectory() as tmp:
    import sqlite3
    store.DB=tmp+"/old.sqlite"
    old=sqlite3.connect(store.DB)
    old.executescript("""CREATE TABLE projects(name TEXT PRIMARY KEY); INSERT INTO projects VALUES('General');
      CREATE TABLE posts(id TEXT PRIMARY KEY, project TEXT NOT NULL REFERENCES projects(name), title TEXT NOT NULL,
        starter TEXT NOT NULL, host TEXT NOT NULL, phase TEXT NOT NULL, updated INTEGER NOT NULL,
        decision TEXT NOT NULL DEFAULT '', lease TEXT, started INTEGER, round_seq TEXT);
      CREATE TABLE replies(seq INTEGER PRIMARY KEY, post TEXT REFERENCES posts(id), author TEXT, body TEXT, stamp INTEGER);
      INSERT INTO posts(id,project,title,starter,host,phase,updated) VALUES('00000000-0000-4000-8000-000000000001','General','Waiting','claude','signer','signin',1);
      INSERT INTO replies(post,author,body,stamp) VALUES('00000000-0000-4000-8000-000000000001','claude','q',1);""")
    old.close()
    store.initialize()
    def call(op, **fields):
        with store.connect() as db:
            return store.dispatch(db,dict(op=op,**fields))
    for n in range(12):  # enough rows that text and numeric order of seq differ
        call("open",id=str(uuid.uuid4()),project="General",starter="claude",host="h"+str(n),title="t",body="b",participants=["codex"])
    mid=str(uuid.uuid4())
    call("open",id=mid,project="General",starter="claude",host="old",title="Migrated",body="b",participants=["codex","antigravity"])
    turn=call("poll",host="old",rounds=1)["post"]
    call("reply",id=mid,lease=turn["lease"],body="first")
    post=call("show",id=mid)
    assert isinstance(post["round_seq"],int) and post["round"]["answered"]==[turn["agent"]] and post["phase"]=="queued"
    legacy=call("show",id="00000000-0000-4000-8000-000000000001")
    assert legacy["round"]["answered"]==[] and legacy["round_seq"]==legacy["replies"][0]["seq"]
    waiting=call("poll",host="signer",rounds=1)["post"]  # a pre-rounds Codex sign-in wait is a normal turn now
    assert waiting["agent"]=="codex" and waiting["participants"]==["codex"]
    call("failed",id=waiting["id"],lease=waiting["lease"],body="still not signed in")
    assert call("show",id=waiting["id"])["phase"]=="failed"  # the starter's question is not an answer

# The launcher's commands, end to end through a local transport (argument parsing included).
with tempfile.TemporaryDirectory() as tmp:
    import json
    repo=Path(__file__).resolve().parents[1]
    env={k:v for k,v in os.environ.items() if k not in ("CLAUDE_CODE_SESSION_ID","CODEX_THREAD_ID")}
    env.update(THREADWELL_DB=tmp+"/cli.sqlite",THREADWELL_STATE=tmp+"/state",THREADWELL_CONFIG=tmp+"/config.json")
    Path(tmp+"/config.json").write_text(json.dumps({"host":"cli","url":"http://board","workspace":tmp,"agents":["codex","claude"],
        "transport":[sys.executable,str(repo/"board/store.py")]}))
    store.DB=env["THREADWELL_DB"]
    store.initialize()
    def cli(*args, body="text", ok=True):
        run=subprocess.run([sys.executable,str(repo/"bin/threadwell"),*args],input=body,text=True,capture_output=True,env=env,cwd=tmp)
        assert (run.returncode==0)==ok, (args, run.stdout, run.stderr)
        return run.stdout.strip() or run.stderr.strip()
    pid=cli("open","--as","codex","--project","General","--title","CLI").rsplit("/",1)[1]
    assert call("show",id=pid)["participants"]==["claude"]
    assert "still running" in cli("ask",pid,ok=False)
    def answer_turn():
        turn=claim("cli")
        call("reply",id=pid,lease=turn["lease"],body="answer")
    answer_turn()
    cli("ask",pid,"--to","claude","--model","gpt-x","--effort","high")
    answer_turn()
    assert "propose" in cli("close",pid,ok=False)
    cli("propose",pid,"--model","gpt-x")
    answer_turn()
    cli("close",pid,"--model","gpt-x")
    post=json.loads(cli("show",pid))
    assert post["phase"]=="done" and [r["kind"] for r in post["replies"]]==[None,None,None,None,"proposal",None]

node=shutil.which("node")
if node:
    subprocess.run([node,str(Path(__file__).with_name("markdown.test.js"))],check=True)
else:
    print("node not found: skipped the markdown renderer checks.")
print("Passed lifecycle, multi-agent round, stale reply, promotion, scoped search, model, rework, poll and project checks.")
