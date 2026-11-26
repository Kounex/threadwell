"""Shared storage. Mutations arrive on trusted local stdio, except the owner's rework request from the board."""
import json
import os
import re
import sqlite3
import sys
import time
import uuid

DB = os.environ.get("THREADWELL_DB", "/data/threads.sqlite3")
# Before multi-agent threads every post had exactly one peer: the other of these two.
LEGACY_PEER = {"codex": "claude", "claude": "codex"}
AGENT = re.compile(r"[a-z][a-z0-9-]{1,30}")
SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(name TEXT PRIMARY KEY);
INSERT OR IGNORE INTO projects VALUES('General');
CREATE TABLE IF NOT EXISTS posts(
 id TEXT PRIMARY KEY, project TEXT NOT NULL REFERENCES projects(name),
 title TEXT NOT NULL, starter TEXT NOT NULL, host TEXT NOT NULL,
 phase TEXT NOT NULL, updated INTEGER NOT NULL, decision TEXT NOT NULL DEFAULT '',
 lease TEXT, started INTEGER, decision_model TEXT, decision_effort TEXT,
 rework TEXT, rework_lease TEXT, rework_seen INTEGER,
 participants TEXT, pending TEXT, agent TEXT, round_seq INTEGER);
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
                 ("posts", "rework_seen"), ("posts", "participants"), ("posts", "pending"), ("posts", "agent"),
                 ("posts", "round_seq INTEGER"), ("replies", "model"), ("replies", "kind"), ("replies", "effort"))
        for table, column in added:
            name, _, kind = column.partition(" ")
            if name not in {r[1] for r in db.execute(f"pragma table_info({table})")}:
                db.execute(f"alter table {table} add column {name} {kind or 'TEXT'}")
        # Before rounds, a thread could wait in 'signin' for Codex; now that is a skipped turn, so let it run.
        db.execute("update posts set phase='queued' where phase='signin'")
        # Open threads from before rounds: the round is the starter's latest question.
        db.execute("""update posts set round_seq=(select max(seq) from replies where post=posts.id and author=posts.starter)
                      where round_seq is null and phase!='done'""")


def thread(db, pid):
    row = db.execute("select * from posts where id=?", (pid,)).fetchone()
    if row is None:
        raise ValueError("Post not found")
    result = dict(row)
    # 0.1.11 added round_seq as TEXT on existing boards, so stored values may be strings.
    result["round_seq"] = int(result["round_seq"]) if result["round_seq"] is not None else None
    result["replies"] = [dict(r) for r in db.execute(
        "select seq,author,body,stamp,model,effort,kind from replies where post=? order by seq", (pid,))]
    result["participants"] = participants(result)
    result["pending"] = (result["pending"] or "").split(",") if result["pending"] else []
    # The current (or last) round: who answered, whose turn was skipped, who is still due.
    this_round = [r for r in result["replies"] if r["seq"] > (result["round_seq"] or 0)]
    result["round"] = {"answered": [r["author"] for r in this_round
                                    if r["author"] not in ("launcher", "owner", result["starter"])],
                       "skipped": [r["kind"][8:] for r in this_round if (r["kind"] or "").startswith("skipped:")],
                       "pending": result["pending"]}
    return result


def participants(post):
    """Agents invited to every round: stored since multi-agent threads, otherwise the starter's legacy peer."""
    if post["participants"]:
        return post["participants"].split(",")
    return [LEGACY_PEER[post["starter"]]] if post["starter"] in LEGACY_PEER else []


def agents(value, field_name):
    """A list of agent names: lowercase identifiers, duplicates dropped, order kept."""
    if not isinstance(value, list) or not value or len(value) > 8 or \
            not all(isinstance(a, str) and AGENT.fullmatch(a) for a in value):
        raise ValueError(f"Invalid {field_name}: give one or more agent names")
    return list(dict.fromkeys(value))


def round_order(pid, names, rounds):
    """Who answers first rotates per thread and per round, so no agent always speaks before the others have."""
    shift = (int(pid.replace("-", "")[:8], 16) + rounds) % len(names)
    return names[shift:] + names[:shift]


def start_round(db, pid, seq, queue, now):
    db.execute("update posts set phase='queued',pending=?,agent=null,lease=null,round_seq=?,updated=? where id=?",
               (",".join(queue), seq, now, pid))


def advance(db, pid, now):
    """The current agent is done (answered or failed): queue the next one, or end the round."""
    post = db.execute("select pending,agent,round_seq,starter from posts where id=?", (pid,)).fetchone()
    queue = [a for a in (post["pending"] or "").split(",") if a and a != post["agent"]]
    if queue:
        phase = "queued"
    else:
        answered = db.execute("""select 1 from replies where post=? and seq>? and author not in ('launcher','owner',?)
                                 limit 1""", (pid, int(post["round_seq"] or 0), post["starter"])).fetchone()
        phase = "answered" if answered else "failed"
    db.execute("update posts set phase=?,pending=?,agent=null,lease=null,started=null,updated=? where id=?",
               (phase, ",".join(queue) or None, now, pid))


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
    closed, pending = db.execute("select phase='done',pending from posts where id=?", (pid,)).fetchone()
    if note and not closed:
        db.execute("insert into replies(post,author,body,stamp) values(?,?,?,?)", (pid, "launcher", note[:64000], now))
    if pending:
        db.execute("insert into replies(post,author,body,stamp) values(?,?,?,?)", (pid, "launcher",
                   f"The rework run ended before {pending.replace(',', ', ')} answered; those turns were cancelled.", now))
    db.execute("""update posts set rework=null,rework_lease=null,rework_seen=null,phase='done',lease=null,
                  pending=null,agent=null,started=null,updated=? where id=?""", (now, pid))


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


PARTICIPANT_SWEEP = 1200  # a participant turn still running after 20 min (launcher limit: 15) is skipped
HEARTBEAT = 30  # a poll refreshes "seen" once it is this old; the board shows a launcher online for 100 s


def heartbeat(db, host, now):
    db.execute("""insert into hosts values(?,?) on conflict(name) do update set seen=excluded.seen
                  where seen<=?""", (host, now, now-HEARTBEAT))


def sweep(db, now):
    """Skip participants whose worker stopped pulsing, with a note that says so."""
    for pid, host, agent in db.execute("select id,host,agent from posts where phase='running' and started<?",
                                       (now-PARTICIPANT_SWEEP,)).fetchall():
        db.execute("insert into replies(post,author,body,stamp,kind) values(?,?,?,?,?)", (pid, "launcher",
                   f"{agent or 'The participant'} on {host} stopped responding, so its turn was skipped. "
                   f"Ask again to retry{' (--to ' + agent + ')' if agent else ''}.", now, f"skipped:{agent or ''}"))
        advance(db, pid, now)
    for (stale,) in db.execute("select id from posts where rework='running' and rework_seen<?", (now-150,)).fetchall():
        finish_rework(db, stale, now, "The rework run stopped responding. The previous decision stands.")


def poll_due(db, host, now):
    """Read-only check of whether a poll has anything to write: work, a sweep or a heartbeat."""
    return db.execute("""select
        exists(select 1 from posts where host=? and (phase='queued' or rework='queued'))
        or exists(select 1 from posts where (phase='running' and started<?) or (rework='running' and rework_seen<?))
        or not exists(select 1 from hosts where name=? and seen>?)""",
        (host, now-PARTICIPANT_SWEEP, now-150, host, now-HEARTBEAT)).fetchone()[0]


def claim_rework(db, host, now):
    if db.execute("select 1 from posts where host=? and rework='running'", (host,)).fetchone():
        return None
    row = db.execute("select id from posts where host=? and rework='queued' order by updated,id limit 1", (host,)).fetchone()
    if not row:
        return None
    db.execute("update posts set rework='running',rework_lease=?,rework_seen=? where id=?", (uuid.uuid4().hex, now, row[0]))
    return thread(db, row[0])


def claim(db, host, now):
    """The next queued post on this host, leased to the first agent still due in its round."""
    if db.execute("select 1 from posts where host=? and phase='running'", (host,)).fetchone():
        return None  # one participant at a time per host, even with a duplicate worker
    # Oldest round first, so a round already under way finishes before a later thread starts.
    row = db.execute("""select * from posts where host=? and phase='queued'
                        order by cast(coalesce(round_seq,0) as integer),updated,id limit 1""", (host,)).fetchone()
    if not row:
        return None
    queue = (row["pending"] or "").split(",") if row["pending"] else participants(row)
    if not queue:
        db.execute("update posts set phase='failed',updated=? where id=?", (now, row["id"]))
        return None
    db.execute("update posts set phase='running',pending=?,agent=?,lease=?,started=? where id=?",
               (",".join(queue), queue[0], uuid.uuid4().hex, now, row["id"]))
    return thread(db, row["id"])


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
        # Workers from before rounds also poll, but would run the wrong agent under this one's name.
        result = {"rework": claim_rework(db, host, now), "post": claim(db, host, now) if msg.get("rounds") else None}
        return {k: v for k, v in result.items() if v}
    if op == "project":
        return project(db, field(msg, "project", 80))
    if op == "open":
        pid = str(uuid.UUID(msg["id"]))
        starter = msg["starter"]
        if not isinstance(starter, str) or not AGENT.fullmatch(starter):
            raise ValueError("Unknown provider")
        invited = [a for a in agents(msg.get("participants") or [LEGACY_PEER.get(starter, "")], "participants")
                   if a != starter]
        if not invited:
            raise ValueError("Invite at least one agent other than the starter")
        name = project(db, field(msg, "project", 80))
        db.execute("""insert into posts(id,project,title,starter,host,phase,updated,participants)
                      values(?,?,?,?,?,'queued',?,?)""",
                   (pid, name, field(msg, "title", 200), starter, field(msg, "host", 80), now, ",".join(invited)))
        seq = db.execute("insert into replies(post,author,body,stamp,model,effort) values(?,?,?,?,?,?)",
                         (pid, starter, field(msg, "body"), now, model(msg), effort(msg))).lastrowid
        start_round(db, pid, seq, round_order(pid, invited, 0), now)
        return pid
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
        host = field(msg, "host", 80)
        heartbeat(db, host, now)
        sweep(db, now)
        if op == "claim":
            # Workers older than multi-agent threads would invite the wrong agent; they get no work until updated.
            return None
        return bool(db.execute("select 1 from posts where id=? and lease=? and phase='running'",
                               (msg["id"], msg["lease"])).fetchone())
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
    if op in {"reply", "failed"}:
        if not post["lease"] or msg.get("lease") != post["lease"]:
            raise ValueError("Invitation expired")
        current = post["agent"] or LEGACY_PEER.get(post["starter"])
        author = current if op == "reply" else "launcher"
        agent = author != "launcher"
        # A failed turn is a launcher note tagged with the agent it skipped.
        db.execute("insert into replies(post,author,body,stamp,model,effort,kind) values(?,?,?,?,?,?,?)",
                   (pid, author, field(msg, "body"), now, model(msg) if agent else None, effort(msg) if agent else None,
                    None if agent else f"skipped:{current}"))
        advance(db, pid, now)
        return pid
    if op in {"ask", "propose"}:
        # `propose` is the starter's intended decision, put to every participant for review before it can close.
        if post["phase"] not in {"answered", "failed"}:
            raise ValueError("A round is still running; wait for it to finish")
        invited = post["participants"]
        queue = [a for a in agents(msg["to"], "to") if a in invited] if msg.get("to") else invited
        if not queue:
            raise ValueError("None of those agents takes part in this thread: " + ", ".join(invited))
        rounds = db.execute("select count(*) from replies where post=? and author=? and (kind is null or kind='proposal')",
                            (pid, post["starter"])).fetchone()[0]
        seq = db.execute("insert into replies(post,author,body,stamp,model,effort,kind) values(?,?,?,?,?,?,?)",
                         (pid, post["starter"], field(msg, "body"), now, model(msg), effort(msg),
                          "proposal" if op == "propose" else None)).lastrowid
        start_round(db, pid, seq, round_order(pid, queue, rounds), now)
        return pid
    if op == "close":
        if post["phase"] in {"queued", "running"}:
            if not msg.get("force"):
                raise ValueError("A round is still running (waiting for " + ", ".join(post["pending"]) +
                                 "); wait for it, or close with --force to record who never answered")
            db.execute("insert into replies(post,author,body,stamp) values(?,?,?,?)", (pid, "launcher",
                       "Closed before " + ", ".join(post["pending"]) + " answered.", now))
        # The participants review the starter's choice before it becomes the decision: the latest round must be
        # a proposal (not a question, and not superseded by an owner's rework request), and someone reviewed it.
        rounds = [r for r in post["replies"] if r["kind"] == "rework" or
                  (r["author"] == post["starter"] and r["kind"] in (None, "proposal"))]
        latest = rounds[-1] if rounds else {}
        reviewed = latest.get("kind") == "proposal" and any(
            r["seq"] > latest["seq"] and r["author"] not in ("launcher", "owner", post["starter"]) for r in post["replies"])
        if post["participants"] and not reviewed:
            if not msg.get("force"):
                raise ValueError("Post your intended decision with `threadwell propose` first, so the participants "
                                 "can review it; or close with --force to record that they didn't")
            db.execute("insert into replies(post,author,body,stamp) values(?,?,?,?)", (pid, "launcher",
                       "Closed without the participants reviewing the decision.", now))
        db.execute("""update posts set phase='done',updated=?,lease=null,pending=null,agent=null,decision=?,
                      decision_model=?,decision_effort=? where id=?""",
                   (now, field(msg, "body"), model(msg), effort(msg), pid))
        return pid
    raise ValueError("Unknown command")


if __name__ == "__main__":
    request = json.load(sys.stdin)
    if request.get("op") == "rework":
        sys.exit("Rework requests come from the owner on the board")
    with connect() as connection:
        result = dispatch(connection, request)
    print(json.dumps(result))
