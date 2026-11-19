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
print("Passed lifecycle, stale reply, promotion and scoped search checks.")
