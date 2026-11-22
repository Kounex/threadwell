"""Shared storage. Mutations arrive on trusted local stdio, except the owner's rework request from the board."""
import json
import os
import re
import sqlite3
import sys
import time
import uuid

DB = os.environ.get("THREADWELL_DB", "/data/threads.sqlite3")
PROVIDERS = {"codex": "claude", "claude": "codex"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(name TEXT PRIMARY KEY);
INSERT OR IGNORE INTO projects VALUES('General');
CREATE TABLE IF NOT EXISTS posts(
 id TEXT PRIMARY KEY, project TEXT NOT NULL REFERENCES projects(name),
 title TEXT NOT NULL, starter TEXT NOT NULL, host TEXT NOT NULL,
 phase TEXT NOT NULL, updated INTEGER NOT NULL, decision TEXT NOT NULL DEFAULT '',
 lease TEXT, started INTEGER, decision_model TEXT, decision_effort TEXT,
 rework TEXT, rework_lease TEXT, rework_seen INTEGER);
CREATE TABLE IF NOT EXISTS replies(
 seq INTEGER PRIMARY KEY, post TEXT REFERENCES posts(id), author TEXT, body TEXT, stamp INTEGER, model TEXT, kind TEXT, effort TEXT);
CREATE INDEX IF NOT EXISTS reply_post ON replies(post,seq);
CREATE TABLE IF NOT EXISTS hosts(name TEXT PRIMARY KEY, seen INTEGER);
"""


def connect():
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("pragma foreign_keys=on")
    return db


def initialize():
    with connect() as db:
        db.execute("pragma journal_mode=wal")
        db.executescript(SCHEMA)
        # Columns added after the first release; CREATE TABLE IF NOT EXISTS leaves existing tables alone.
        added = (("posts", "decision_model"), ("posts", "decision_effort"), ("posts", "rework"), ("posts", "rework_lease"),
                 ("posts", "rework_seen"), ("replies", "model"), ("replies", "kind"), ("replies", "effort"))
        for table, column in added:
            if column not in {r[1] for r in db.execute(f"pragma table_info({table})")}:
                db.execute(f"alter table {table} add column {column} TEXT")


def thread(db, pid):
    row = db.execute("select * from posts where id=?", (pid,)).fetchone()
    if row is None:
        raise ValueError("Post not found")
    result = dict(row)
    result["replies"] = [dict(r) for r in db.execute(
        "select author,body,stamp,model,effort,kind from replies where post=? order by seq", (pid,))]
    return result


def field(msg, key, limit=64000):
    value = msg.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"Invalid {key} (maximum {limit} characters)")
    return value.strip()


def model(msg, key="model", pattern=r"[\w.:/@\[\]-]{1,80}"):
    """Optional model identifier (or effort level) reported by the CLI that wrote a message."""
    value = msg.get(key)
    if not isinstance(value, str) or not re.fullmatch(pattern, value.strip()):
        return None
    return value.strip()


def effort(msg):
    return model(msg, "effort", r"[a-z]{1,16}")


def finish_rework(db, pid, now, note):
    """End a rework run. Without a fresh close the thread returns to its previous decision."""
    closed = db.execute("select phase='done' from posts where id=?", (pid,)).fetchone()[0]
    if note and not closed:
        db.execute("insert into replies(post,author,body,stamp) values(?,?,?,?)", (pid, "launcher", note[:64000], now))
    db.execute("""update posts set rework=null,rework_lease=null,rework_seen=null,phase='done',lease=null,updated=?
                  where id=?""", (now, pid))


def project_key(name):
    """Lookup key: case and runs of spaces, hyphens and underscores don't matter ("Open Tech Quest" = "open-tech-quest")."""
    return re.sub(r"[\s_-]+", "-", name.strip().casefold())


def aliases(db, name):
    """Every stored spelling of a project, the one holding the most posts first."""
    key = project_key(name)
    rows = db.execute("select name,(select count(*) from posts where project=name) from projects order by rowid").fetchall()
    return [n for n, _ in sorted((r for r in rows if project_key(r[0]) == key), key=lambda r: -r[1])]


def project(db, name):
    if not re.fullmatch(r"[\w][\w .-]{0,79}", name):
        raise ValueError("Use a short project name with letters, spaces, digits, dots or hyphens")
    name = "General" if project_key(name) == "general" else name
    existing = aliases(db, name)
    if existing:
        return existing[0]
    db.execute("insert into projects values(?)", (name,))
    return name


HEARTBEAT = 30  # seconds between host "seen" writes; the board shows a launcher online for 100 s


def heartbeat(db, host, now):
    db.execute("""insert into hosts values(?,?) on conflict(name) do update set seen=excluded.seen
                  where seen<?""", (host, now, now-HEARTBEAT))


def sweep(db, now):
    """Fail invitations whose worker stopped pulsing, with a note that says so."""
    for pid, host in db.execute("select id,host from posts where phase='running' and started<?", (now-960,)).fetchall():
        db.execute("insert into replies(post,author,body,stamp) values(?,?,?,?)", (pid, "launcher",
                   f"The participant on {host} stopped responding, so this invitation was marked failed. "
                   "Ask again to retry.", now))
        db.execute("update posts set phase='failed',lease=null,updated=? where id=?", (now, pid))
    for (stale,) in db.execute("select id from posts where rework='running' and rework_seen<?", (now-150,)).fetchall():
        finish_rework(db, stale, now, "The rework run stopped responding. The previous decision stands.")


def poll_due(db, host, now):
    """Read-only check of whether a poll has anything to write: work, a sweep or a heartbeat."""
    return db.execute("""select
        exists(select 1 from posts where host=? and (phase in ('queued','signin') or rework='queued'))
        or exists(select 1 from posts where (phase='running' and started<?) or (rework='running' and rework_seen<?))
        or not exists(select 1 from hosts where name=? and seen>=?)""",
        (host, now-960, now-150, host, now-HEARTBEAT)).fetchone()[0]


def claim_rework(db, host, now):
    if db.execute("select 1 from posts where host=? and rework='running'", (host,)).fetchone():
        return None
    row = db.execute("select id from posts where host=? and rework='queued' order by updated,id limit 1", (host,)).fetchone()
    if not row:
        return None
    db.execute("update posts set rework='running',rework_lease=?,rework_seen=? where id=?", (uuid.uuid4().hex, now, row[0]))
    return thread(db, row[0])


def claim(db, host, now):
    row = db.execute("select id from posts where host=? and phase='queued' order by updated,id limit 1", (host,)).fetchone()
    if not row:
        waiting = db.execute("select 1 from posts where host=? and phase='signin' limit 1", (host,)).fetchone()
        return {"waiting_for": "codex"} if waiting else None
    db.execute("update posts set phase='running',lease=?,started=? where id=?", (uuid.uuid4().hex, now, row[0]))
    return thread(db, row[0])


READS = {"show", "poll"}


def dispatch(db, msg):
    op = msg["op"]
    now = int(time.time())
    # Reads take no write lock: `wait` shows every few seconds and idle polls run all day.
    db.execute("begin" if op in READS else "begin immediate")
    if op == "poll":
        # One call per worker cycle: a rework run and an invitation, each claimed independently.
        host = field(msg, "host", 80)
        if not poll_due(db, host, now):
            return {}
        db.commit()
        db.execute("begin immediate")
        heartbeat(db, host, now)
        sweep(db, now)
        result = {"rework": claim_rework(db, host, now)}
        post = claim(db, host, now)
        if post and post.get("waiting_for"):
            result["waiting_for"] = post["waiting_for"]
        elif post:
            result["post"] = post
        return {k: v for k, v in result.items() if v}
    if op == "project":
        return project(db, field(msg, "project", 80))
    if op == "open":
        pid = str(uuid.UUID(msg["id"]))
        starter = msg["starter"]
        if starter not in PROVIDERS:
            raise ValueError("Unknown provider")
        name = project(db, field(msg, "project", 80))
        db.execute("insert into posts(id,project,title,starter,host,phase,updated) values(?,?,?,?,?,'queued',?)",
                   (pid, name, field(msg, "title", 200), starter, field(msg, "host", 80), now))
        db.execute("insert into replies(post,author,body,stamp,model,effort) values(?,?,?,?,?,?)",
                   (pid, starter, field(msg, "body"), now, model(msg), effort(msg)))
        return pid
    if op == "authready":
        db.execute("update posts set phase='queued',updated=? where host=? and phase='signin' and starter='claude'",
                   (now, field(msg, "host", 80)))
        return True
    if op == "claimrework":
        # Kept for workers older than `poll`.
        sweep(db, now)
        return claim_rework(db, field(msg, "host", 80), now)
    if op in {"reworkpulse", "reworked"}:
        current = db.execute("select 1 from posts where id=? and rework='running' and rework_lease=?",
                             (msg["id"], msg["lease"])).fetchone()
        if op == "reworkpulse" or not current:
            if current:
                db.execute("update posts set rework_seen=? where id=?", (now, msg["id"]))
            return bool(current)
        finish_rework(db, msg["id"], now, msg.get("body") or None)
        return True
    if op in {"claim", "pulse"}:
        # `claim` is kept for workers older than `poll`.
        host = field(msg, "host", 80)
        heartbeat(db, host, now)
        sweep(db, now)
        if op == "pulse":
            return bool(db.execute("select 1 from posts where id=? and lease=? and phase='running'",
                                   (msg["id"], msg["lease"])).fetchone())
        return claim(db, host, now)
    pid = msg["id"]
    post = thread(db, pid)
    if op == "show":
        post.pop("lease")
        post.pop("rework_lease")
        return post
    if op == "rework":
        if post["phase"] != "done" or post["rework"]:
            raise ValueError("A rework can only be requested on a closed thread")
        body = field(msg, "body", 8000)
        kept = [r["body"] for r in post["replies"] if r["kind"] == "decision"]
        if post["decision"] and kept[-1:] != [post["decision"]]:
            # Keep the decision being questioned in the thread; a new close replaces posts.decision.
            db.execute("insert into replies(post,author,body,stamp,model,effort,kind) values(?,?,?,?,?,?,'decision')",
                       (pid, post["starter"], post["decision"], post["updated"], post["decision_model"], post["decision_effort"]))
        db.execute("insert into replies(post,author,body,stamp,kind) values(?,'owner',?,?,'rework')", (pid, body, now))
        db.execute("update posts set phase='answered',rework='queued',updated=? where id=?", (now, pid))
        return pid
    if op == "move":
        db.execute("update posts set project=? where id=?", (project(db, field(msg, "project", 80)), pid))
        return pid
    if post["phase"] == "done":
        raise ValueError("This post is closed")
    if op in {"reply", "failed", "signin"}:
        if not post["lease"] or msg.get("lease") != post["lease"]:
            raise ValueError("Invitation expired")
        author = PROVIDERS[post["starter"]] if op == "reply" else "launcher"
        phase = {"reply": "answered", "failed": "failed", "signin": "signin"}[op]
    elif op == "ask":
        if post["phase"] not in {"answered", "failed"}:
            raise ValueError("An invitation is already pending")
        author, phase = post["starter"], "queued"
    elif op == "close":
        db.execute("update posts set phase='done',updated=?,lease=null,decision=?,decision_model=?,decision_effort=? where id=?",
                   (now, field(msg, "body"), model(msg), effort(msg), pid))
        return pid
    else:
        raise ValueError("Unknown command")
    agent = author != "launcher"
    db.execute("insert into replies(post,author,body,stamp,model,effort) values(?,?,?,?,?,?)",
               (pid, author, field(msg, "body"), now, model(msg) if agent else None, effort(msg) if agent else None))
    db.execute("update posts set updated=?,phase=?,lease=null where id=?", (now, phase, pid))
    return pid


if __name__ == "__main__":
    request = json.load(sys.stdin)
    if request.get("op") == "rework":
        sys.exit("Rework requests come from the owner on the board")
    with connect() as connection:
        result = dispatch(connection, request)
    print(json.dumps(result))
