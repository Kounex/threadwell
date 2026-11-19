"""Shared storage. Mutations are available exclusively on trusted local stdio."""
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
 lease TEXT, started INTEGER);
CREATE TABLE IF NOT EXISTS replies(
 seq INTEGER PRIMARY KEY, post TEXT REFERENCES posts(id), author TEXT, body TEXT, stamp INTEGER);
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


def thread(db, pid):
    row = db.execute("select * from posts where id=?", (pid,)).fetchone()
    if row is None:
        raise ValueError("Post not found")
    result = dict(row)
    result["replies"] = [dict(r) for r in db.execute(
        "select author,body,stamp from replies where post=? order by seq", (pid,))]
    return result


def field(msg, key, limit=64000):
    value = msg.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"Invalid {key} (maximum {limit} characters)")
    return value.strip()


def project(db, name):
    if not re.fullmatch(r"[\w][\w .-]{0,79}", name):
        raise ValueError("Use a short project name with letters, spaces, digits, dots or hyphens")
    name = "General" if name.casefold() == "general" else name
    existing = db.execute("select name from projects where name=? collate nocase", (name,)).fetchone()
    if existing:
        return existing[0]
    db.execute("insert into projects values(?)", (name,))
    return name


def dispatch(db, msg):
    op = msg["op"]
    now = int(time.time())
    db.execute("begin immediate")
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
        db.execute("insert into replies(post,author,body,stamp) values(?,?,?,?)",
                   (pid, starter, field(msg, "body"), now))
        return pid
    if op == "authready":
        db.execute("update posts set phase='queued',updated=? where host=? and phase='signin' and starter='claude'",
                   (now, field(msg, "host", 80)))
        return True
    if op in {"claim", "pulse"}:
        host = field(msg, "host", 80)
        db.execute("insert or replace into hosts values(?,?)", (host, now))
        db.execute("update posts set phase='failed',lease=null where phase='running' and started<?", (now-960,))
        if op == "pulse":
            return bool(db.execute("select 1 from posts where id=? and lease=? and phase='running'",
                                   (msg["id"], msg["lease"])).fetchone())
        row = db.execute("select id from posts where host=? and phase='queued' order by updated,id limit 1", (host,)).fetchone()
        if not row:
            waiting = db.execute("select 1 from posts where host=? and phase='signin' limit 1", (host,)).fetchone()
            return {"waiting_for": "codex"} if waiting else None
        db.execute("update posts set phase='running',lease=?,started=? where id=?",
                   (uuid.uuid4().hex, now, row[0]))
        return thread(db, row[0])
    pid = msg["id"]
    post = thread(db, pid)
    if op == "show":
        post.pop("lease")
        return post
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
        db.execute("update posts set phase='done',updated=?,lease=null,decision=? where id=?",
                   (now, field(msg, "body"), pid))
        return pid
    else:
        raise ValueError("Unknown command")
    db.execute("insert into replies(post,author,body,stamp) values(?,?,?,?)", (pid, author, field(msg, "body"), now))
    db.execute("update posts set updated=?,phase=?,lease=null where id=?", (now, phase, pid))
    return pid


if __name__ == "__main__":
    with connect() as connection:
        result = dispatch(connection, json.load(sys.stdin))
    print(json.dumps(result))
