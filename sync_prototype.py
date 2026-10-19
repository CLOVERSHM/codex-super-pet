"""Isolated event/command prototype. No desktop configuration or real chat writes."""
import hashlib
import json
import secrets
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path


def string(value, limit=4000):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError("文本格式或长度不合法")
    return value.strip()


def normalized(notification):
    """Project only supported protocol fields; never retain tool/reasoning payloads."""
    method, params = notification.get("method"), notification.get("params", {})
    if not isinstance(params, dict):
        raise ValueError("事件参数必须为对象")
    if method == "thread/started":
        thread = params["thread"]
        return {"kind": "session", "thread_id": thread["id"],
                "payload": {"title": string(thread.get("name") or "未命名会话", 160),
                            "cwd": string(thread["cwd"], 2000)}}
    if method in ("item/started", "item/completed"):
        item = params["item"]
        if item.get("type") != "userMessage":
            return None
        contents = item.get("content", [])
        if not isinstance(contents, list):
            raise ValueError("消息内容必须为数组")
        message = "\n".join(string(c["text"]) for c in contents if c.get("type") == "text")
        return {"kind": "input", "thread_id": params["threadId"],
                "source_id": string(item["id"], 160), "payload": {"text": string(message)}}
    if method == "thread/goal/updated":
        goal = params["goal"]
        return {"kind": "goal", "thread_id": params["threadId"],
                "payload": {"objective": string(goal["objective"]),
                            "status": string(goal["status"], 40)}}
    if method == "thread/goal/cleared":
        return {"kind": "goal", "thread_id": params["threadId"],
                "payload": {"objective": "", "status": "cleared"}}
    return None


class SyncLedger:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT UNIQUE,
                    thread_id TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS commands (
                    id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, data TEXT NOT NULL);
            """)
            for ident, raw in db.execute("SELECT id,data FROM commands").fetchall():
                command = json.loads(raw)
                if command["state"] == "sending":
                    command["state"] = "unknown"
                    db.execute("UPDATE commands SET data=? WHERE id=?",
                               (json.dumps(command), ident))
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def ingest(self, notification):
        event = normalized(notification)
        if event is None:
            return False
        ident = str(uuid.UUID(event["thread_id"]))
        event["thread_id"] = ident
        # started/completed for a single user item share the same deduplication key.
        key_data = [ident, event["kind"], event.get("source_id") or event["payload"]]
        key = (hashlib.sha256(json.dumps(key_data, sort_keys=True).encode()).hexdigest()
               if event["kind"] != "goal" else None)
        with self.changed, self.db() as db:
            if db.execute("SELECT 1 FROM events WHERE event_key=?", (key,)).fetchone():
                return False
            row = db.execute("SELECT data FROM sessions WHERE id=?", (ident,)).fetchone()
            if event["kind"] == "session":
                if row:
                    session = json.loads(row[0])
                    if any(session[k] != event["payload"][k] for k in ("title", "cwd")):
                        raise ValueError("已登记会话身份变化，需人工重新登记")
                    return False
                session = {"id": ident, **event["payload"], "revision": 0,
                           "objective": "", "goal_status": "", "inputs": []}
            else:
                if not row:
                    raise ValueError("会话尚未登记；不能凭输入猜测目标会话")
                session = json.loads(row[0])
                if event["kind"] == "input":
                    # An echoed outbox item is an event, not another requirement.
                    command_id = event["payload"]["text"].split("\n", 1)[0].removeprefix(
                        "[delivery-command:").removesuffix("]")
                    echoed = db.execute("SELECT data FROM commands WHERE id=? AND thread_id=?",
                                        (command_id, ident)).fetchone()
                    if echoed and json.loads(echoed[0])["prompt"] == event["payload"]["text"]:
                        event["kind"] = "echo"
                    else:
                        session["inputs"].append({"id": event["source_id"],
                                                  "text": event["payload"]["text"]})
                        session["revision"] += 1
                elif event["kind"] == "goal":
                    if (session["objective"] == event["payload"]["objective"] and
                            session["goal_status"] == event["payload"]["status"]):
                        return False
                    if session["objective"] != event["payload"]["objective"]:
                        session["revision"] += 1
                    session["objective"] = event["payload"]["objective"]
                    session["goal_status"] = event["payload"]["status"]
            db.execute("INSERT INTO events(event_key,thread_id,data) VALUES (?,?,?)",
                       (key, ident, json.dumps(event, ensure_ascii=False)))
            db.execute("INSERT OR REPLACE INTO sessions VALUES (?,?)",
                       (ident, json.dumps(session, ensure_ascii=False)))
            self.changed.notify_all()
        return True

    def snapshot(self, after=0, wait=0):
        def cursor():
            with self.db() as db:
                return db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
        with self.changed:
            self.changed.wait_for(lambda: cursor() != after, timeout=wait)
            with self.db() as db:
                sessions = [json.loads(r[0]) for r in db.execute("SELECT data FROM sessions")]
                commands = [json.loads(r[0]) for r in db.execute("SELECT data FROM commands")]
                seq = cursor()
            for session in sessions:
                session["commands"] = [dict(c, stale=c["revision"] != session["revision"])
                                       for c in commands if c["thread_id"] == session["id"]]
            return {"cursor": seq, "mode": "synthetic", "sessions": sessions}

    def approve(self, ident, revision, scope, step, note, actor):
        if actor != "human":
            raise ValueError("审批只能由人工提交")
        if scope not in ("develop", "preflight"):
            raise ValueError("原型不支持生产、合并或权限审批")
        step, note = string(step, 2000), string(note, 2000)
        if not step or not note:
            raise ValueError("请填写执行范围及审批意见")
        with self.changed, self.db() as db:
            row = db.execute("SELECT data FROM sessions WHERE id=?", (ident,)).fetchone()
            if not row:
                raise ValueError("会话不存在")
            session = json.loads(row[0])
            if type(revision) is not int or revision != session["revision"]:
                raise ValueError("输入或目标已变化，请重新审批")
            for raw, in db.execute("SELECT data FROM commands WHERE thread_id=?", (ident,)):
                previous = json.loads(raw)
                if previous["state"] in ("sending", "unknown") or (
                        previous["revision"] == revision and previous["state"] != "stale"):
                    raise ValueError("该版本已有批准单；结果未知不能重复发送")
            command_id = uuid.uuid4().hex
            command = {"id": command_id, "thread_id": ident, "cwd": session["cwd"],
                       "revision": revision, "scope": scope, "step": step, "note": note,
                       "state": "pending", "objective": session["objective"]}
            command["prompt"] = (
                f"[delivery-command:{command_id}]\n"
                f"合成联通验收，不是生产授权。目标：{session['objective']}\n"
                f"范围：{step}\n意见：{note}\n"
                "不部署、不推送、不操作真实工单，不提升工具权限。")
            db.execute("INSERT INTO commands VALUES (?,?,?)",
                       (command_id, ident, json.dumps(command, ensure_ascii=False)))
            self._command_event(db, command)
            self.changed.notify_all()
            return command

    def _command_event(self, db, command):
        db.execute("INSERT INTO events(thread_id,data) VALUES (?,?)", (
            command["thread_id"], json.dumps({"kind": "command", "id": command["id"],
                                              "state": command["state"]})))

    def dispatch(self, command_id, host, actor):
        if actor != "human":
            raise ValueError("发送只能由人工提交")
        with self.changed, self.db() as db:
            row = db.execute("SELECT data FROM commands WHERE id=?", (command_id,)).fetchone()
            if not row:
                raise ValueError("批准单不存在")
            command = json.loads(row[0])
            if command["state"] != "pending":
                raise ValueError("批准单不可重发")
            session = json.loads(db.execute("SELECT data FROM sessions WHERE id=?",
                                           (command["thread_id"],)).fetchone()[0])
            if command["revision"] != session["revision"]:
                raise ValueError("批准后输入或目标已变化，禁止发送")
            command["state"] = "sending"
            db.execute("UPDATE commands SET data=? WHERE id=?", (json.dumps(command), command_id))
            self._command_event(db, command)
            self.changed.notify_all()
        # Never hold a SQLite transaction across an RPC or echo ingestion.
        try:
            receipt = host.send(command)
            if (not isinstance(receipt, dict) or
                    receipt.get("thread_id") != command["thread_id"] or
                    receipt.get("command_id") != command["id"]):
                raise ValueError("回执与批准单不一致")
            state = "received"
        except Exception:
            receipt, state = None, "unknown"
        with self.changed, self.db() as db:
            command.update(state=state, receipt=receipt)
            db.execute("UPDATE commands SET data=? WHERE id=?", (json.dumps(command), command_id))
            self._command_event(db, command)
            self.changed.notify_all()
        return command


class SyntheticHost:
    """Protocol-shaped host for isolated acceptance. Cannot address desktop chats."""
    def __init__(self, ledger):
        self.ledger = ledger
        self.sessions = {}
        self.sent = []
        self.lock = threading.RLock()

    def create(self, title):
        title = string(title, 160)
        if not title:
            raise ValueError("请填写会话名称")
        with self.lock:
            ident = str(uuid.uuid4())
            self.sessions[ident] = {"id": ident, "name": title, "cwd": "/tmp/delivery-synthetic"}
            self.ledger.ingest({"method": "thread/started",
                                "params": {"thread": self.sessions[ident]}})
            return ident

    def emit(self, ident, kind, value):
        with self.lock:
            if ident not in self.sessions:
                raise ValueError("仅允许本轮合成宿主创建的会话")
            value = string(value)
            if kind == "input":
                if not value:
                    raise ValueError("输入不能为空")
                self.ledger.ingest({"method": "item/started", "params": {
                    "threadId": ident, "item": {"type": "userMessage", "id": uuid.uuid4().hex,
                                               "content": [{"type": "text", "text": value}]}}})
            elif kind == "goal":
                self.ledger.ingest({"method": "thread/goal/updated", "params": {
                    "threadId": ident, "goal": {"objective": value, "status": "active"}}})
            else:
                raise ValueError("不支持的合成事件")

    def send(self, command):
        with self.lock:
            ident = command["thread_id"]
            if ident not in self.sessions or self.sessions[ident]["cwd"] != command["cwd"]:
                raise ValueError("会话身份不一致")
            current = next(s for s in self.ledger.snapshot()["sessions"] if s["id"] == ident)
            if current["revision"] != command["revision"]:
                raise ValueError("发送前会话版本变化")
            self.sent.append(command["id"])
            self.emit(ident, "input", command["prompt"])
            return {"thread_id": ident, "command_id": command["id"],
                    "message_id": uuid.uuid4().hex, "host": "synthetic"}


def serve(data, port=8770):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse
    root = Path(__file__).resolve().parent
    ledger = SyncLedger(Path(data) / "sync.sqlite3")
    host = SyntheticHost(ledger)
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, value, mime="application/json; charset=utf-8"):
            raw = json.dumps(value, ensure_ascii=False).encode() if isinstance(value, dict) else value
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; frame-ancestors 'none'; object-src 'none'")
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def trusted(self, navigation=False):
            base = f"http://127.0.0.1:{self.server.server_port}"
            return (self.headers.get("Host") == base.removeprefix("http://") and
                    self.headers.get("Origin") in (None, base) and
                    self.headers.get("Sec-Fetch-Site") in (
                        (None, "same-origin", "none") if navigation else (None, "same-origin")))

        def do_GET(self):
            if not self.trusted(navigation=True):
                return self.reply(403, {"error": "非法来源"})
            parsed = urlparse(self.path)
            if parsed.path == "/api/session":
                return self.reply(200, {"token": token, "mode": "synthetic"})
            if parsed.path == "/api/snapshot":
                if self.headers.get("Authorization") != "Bearer " + token:
                    return self.reply(401, {"error": "未授权"})
                try:
                    params = parse_qs(parsed.query)
                    after = int(params.get("after", ["0"])[0])
                    wait = min(20, max(0, int(params.get("wait", ["0"])[0])))
                    return self.reply(200, ledger.snapshot(after, wait))
                except ValueError:
                    return self.reply(400, {"error": "无效增量游标"})
            files = {"/": ("sync.html", "text/html; charset=utf-8"),
                     "/sync.js": ("sync.js", "text/javascript; charset=utf-8"),
                     "/sync.css": ("sync.css", "text/css; charset=utf-8"),
                     "/lucide.js": ("lucide.js", "text/javascript; charset=utf-8"),
                     "/icon.png": ("icon.png", "image/png")}
            if parsed.path not in files:
                return self.reply(404, {"error": "不存在"})
            name, mime = files[parsed.path]
            return self.reply(200, (root / "web" / name).read_bytes(), mime)

        def do_POST(self):
            if not self.trusted():
                return self.reply(403, {"error": "非法来源"})
            if self.headers.get("Authorization") != "Bearer " + token:
                return self.reply(401, {"error": "未授权"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16000:
                    raise ValueError("请求长度不合法")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("请求必须为对象")
                if self.path == "/api/synthetic/session":
                    result = {"id": host.create(body.get("title"))}
                elif self.path == "/api/synthetic/event":
                    host.emit(body.get("thread_id"), body.get("kind"), body.get("value"))
                    result = {"accepted": True}
                elif self.path == "/api/approve":
                    result = ledger.approve(body.get("thread_id"), body.get("revision"),
                                            body.get("scope"), body.get("step"), body.get("note"),
                                            "human")
                elif self.path == "/api/dispatch":
                    result = ledger.dispatch(body.get("command_id"), host, "human")
                else:
                    return self.reply(404, {"error": "不存在"})
                self.reply(200, result)
            except (ValueError, TypeError, KeyError, AttributeError) as error:
                self.reply(400, {"error": str(error)})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server, ledger, host


if __name__ == "__main__":
    import argparse
    import signal
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="delivery-sync-prototype-") as data:
        server, _, _ = serve(data, args.port)
        print(f"合成同步原型 http://127.0.0.1:{server.server_port}", flush=True)
        def shutdown(*_):
            threading.Thread(target=server.shutdown, daemon=True).start()
        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)
        try:
            server.serve_forever()
        finally:
            server.server_close()
