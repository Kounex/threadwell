"""Board backed by SQLite and Cloudflare Access. Reads are open to the Access policy;
the only write is the owner's rework request, which queues a run for the post's host worker."""
import json
import logging
import mimetypes
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from store import connect, dispatch, initialize, thread

STATIC = Path(__file__).parent / "static"
LOCAL = os.environ.get("THREADWELL_PREVIEW") == "1"
if not LOCAL:
    import jwt
    ISSUER = os.environ["ACCESS_ISSUER"].rstrip("/")
    AUD = os.environ["ACCESS_AUD"]
    KEYS = jwt.PyJWKClient(ISSUER + "/cdn-cgi/access/certs", timeout=4)
# Access identities (email claim) allowed to request reworks. Unset disables the write path.
OWNERS = {e.strip().lower() for e in os.environ.get("ACCESS_OWNER", "").split(",") if e.strip()}


def index(db, args):
    term = args.get("q", [""])[0].strip()[:300]
    project = args.get("project", [""])[0]
    status = args.get("status", [""])[0]
    offset = max(0, int(args.get("offset", ["0"])[0]))
    filters, params = [], []
    if project:
        filters.append("project=?")
        params.append(project)
    if term:
        filters.append("""(instr(lower(project),lower(?)) or instr(lower(title),lower(?))
          or instr(lower(decision),lower(?)) or exists(select 1 from replies
          where post=posts.id and instr(lower(body),lower(?))))""")
        params.extend([term]*4)
    if status in ("active", "done"):
        filters.append("phase='done'" if status == "done" else "phase!='done'")
    where = " where " + " and ".join(filters) if filters else ""
    rows = db.execute("select id,project,title,starter,phase,host,updated,rework from posts" + where +
                      " order by (phase='done'),updated desc,rowid desc limit 51 offset ?", [*params, offset]).fetchall()
    return {"posts": [dict(r) for r in rows[:50]], "more": len(rows)>50,
            "projects": [dict(r) for r in db.execute("""select name,count(id) as count from projects
              left join posts on project=name group by name order by (name='General') desc,name collate nocase""")],
            "hosts": {r["name"]: time.time()-r["seen"] < 100 for r in db.execute("select * from hosts")}}


class Board(BaseHTTPRequestHandler):
    def send(self, code, data, kind):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(data)

    def identity(self):
        """Verified Access claims, or None. Preview mode trusts loopback."""
        if LOCAL:
            return {"email": "preview"}
        token = self.headers.get("Cf-Access-Jwt-Assertion", "")
        try:
            key = KEYS.get_signing_key_from_jwt(token).key
            return jwt.decode(token, key, algorithms=["RS256"], issuer=ISSUER, audience=AUD,
                              options={"require":["exp","iat","aud","iss"]})
        except Exception:
            return None

    def do_POST(self):
        path = urlsplit(self.path).path
        parts = path.split("/")
        refuse = lambda code, text: self.send(code, json.dumps({"error": text}).encode(), "application/json")
        if len(parts) != 5 or parts[:3] != ["", "api", "post"] or parts[4] != "rework":
            return refuse(404, "Not found")
        claims = self.identity()
        if claims is None:
            return refuse(403, "Owner sign-in required. Reload the page to sign in again.")
        if not LOCAL and str(claims.get("email", "")).lower() not in OWNERS:
            return refuse(403, "Rework requests are limited to the board owner (ACCESS_OWNER).")
        # Ambient Access cookies make this CSRF-relevant: same-origin JSON only, fail closed.
        scheme = "http" if LOCAL else "https"
        if self.headers.get("Origin") != f"{scheme}://{self.headers.get('Host', '')}" or \
                self.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin" or \
                self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return refuse(403, f"Cross-site request refused (Origin {self.headers.get('Origin')!r}, Host {self.headers.get('Host')!r}).")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 40000:
                raise ValueError("Message too long")
            body = json.loads(self.rfile.read(length)).get("body")
            with connect() as db:
                dispatch(db, {"op": "rework", "id": parts[3], "body": body})
        except (ValueError, AttributeError, KeyError) as error:
            return refuse(409, str(error))
        self.send(200, b'{"ok":true}', "application/json")

    def do_GET(self):
        path = urlsplit(self.path)
        if path.path == "/healthz":
            try:
                with connect() as db:
                    db.execute("select id from posts limit 1").fetchone()
                return self.send(200, b"ok", "text/plain")
            except Exception:
                return self.send(503, b"storage unavailable", "text/plain")
        claims = self.identity()
        if claims is None:
            return self.send(403, b"Owner sign-in required", "text/plain")
        try:
            if path.path == "/api/board" or path.path.startswith("/api/post/"):
                with connect() as db:
                    db.execute("begin")
                    if path.path == "/api/board":
                        result = index(db, parse_qs(path.query))
                    else:
                        result = thread(db, path.path.split("/")[-1])
                        result.pop("lease")
                        result.pop("rework_lease")
                        result["can_rework"] = LOCAL or str(claims.get("email", "")).lower() in OWNERS
                        row = db.execute("select seen from hosts where name=?", (result["host"],)).fetchone()
                        result["online"] = bool(row and time.time()-row[0]<100)
                return self.send(200, json.dumps(result).encode(), "application/json")
            files = {"/style.css": "style.css", "/board.js": "board.js", "/mark.svg":"mark.svg",
                     "/rain.png": "rain.png", "/fonts/bitcount-latin-400.woff2": "fonts/bitcount-latin-400.woff2"}
            if path.path in files:
                name = files[path.path]
            elif path.path in ("/", "/projects") or path.path.startswith(("/post/", "/project/")):
                name = "index.html"
            else:
                return self.send(404, b"Not found", "text/plain")
            self.send(200, (STATIC/name).read_bytes(), mimetypes.guess_type(name)[0])
        except ValueError:
            self.send(404, b"Post or page not found", "text/plain")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    initialize()
    addr = "127.0.0.1" if LOCAL else "0.0.0.0"
    logging.warning("Serving on %s", addr)
    ThreadingHTTPServer((addr, int(os.getenv("PORT","8080"))), Board).serve_forever()
