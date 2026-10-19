"""Opt-in, read-only incremental projection of this desktop's local chat index."""
import hashlib
import json
import re
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path


def readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    db.execute("PRAGMA query_only=ON")
    db.row_factory = sqlite3.Row
    return db


def excerpt(value):
    # Persist a short redacted excerpt, never tools, attachments or full conversations.
    value = re.sub(r"(?i)\b(?:bearer|api[_-]?key|password|token|secret)\s*[:= ]+\S+",
                   "[敏感字段已隐藏]", value)
    value = re.sub(r"-----BEGIN [^-]+-----[\s\S]*?-----END [^-]+-----", "[密钥已隐藏]", value)
    return " ".join(value.split())[:240]


def user_text(payload):
    content = payload.get("content", [])
    if not isinstance(content, list):
        return ""
    value = "\n".join(c.get("text", "") for c in content
                      if isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str))
    return "" if value.lstrip().startswith(("<environment_context>", "<system", "# AGENTS.md")) else value.strip()


class ChatCapture:
    def __init__(self, store, home):
        self.store, self.home = store, Path(home)
        self.stop_event = threading.Event()
        self.thread = None
        with store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS captured_chats (id TEXT PRIMARY KEY,data TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS captured_inputs (id TEXT PRIMARY KEY,data TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS capture_seen (id TEXT PRIMARY KEY,digest TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS capture_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)")

    def snapshot(self):
        excluded = {p["id"] for p in self.store.catalog()["projects"] if p.get("capture_excluded")}
        with self.store.connect() as db:
            meta = dict(db.execute("SELECT key,value FROM capture_meta"))
            chats = [json.loads(r[0]) for r in db.execute("SELECT data FROM captured_chats ORDER BY rowid DESC")]
            eligible = {c["id"] for c in chats if c["project_id"] not in excluded}
            return {"enabled": True, "adapter": "本机只读索引（非官方事件订阅）",
                    "health": meta.get("health", "尚未启动"), "last_poll": meta.get("last_poll"),
                    "chats": chats,
                    "inputs": [e for r in db.execute("SELECT data FROM captured_inputs ORDER BY rowid DESC LIMIT 200")
                               if (e := json.loads(r[0]))["thread_id"] in eligible],
                    "classification": "待分析，不自动生成需求", "history": "首次启动不回填历史正文"}

    def poll(self):
        try:
            projects = self.store.project_source.snapshot()
            with closing(readonly(self.home / "state_5.sqlite")) as source:
                chats = [dict(r) for r in source.execute(
                    "SELECT id,COALESCE(name,title) AS title,project_id,archived,updated_at_ms FROM threads")]
            # The projected database contains stable IDs even when a thread is already open.
            with closing(readonly(self.home / "thread_history_1.sqlite")) as source:
                rows = list(source.execute(
                    "SELECT thread_id,turn_id,item_id,created_at_ms,updated_at_ordinal,length(item_json) AS size "
                    "FROM thread_items WHERE item_type='userMessage'"))
            identities = {c["id"] for c in chats}
            valid_projects = {p["id"] for p in projects["projects"]}
            current = []
            for row in rows:
                if row["thread_id"] not in identities:
                    continue
                ident = hashlib.sha256((row["thread_id"] + "\0" + row["turn_id"] + "\0" + row["item_id"]).encode()).hexdigest()
                marker = f"{row['updated_at_ordinal']}:{row['size']}"
                current.append((ident, marker, row))
            with self.store.lock, self.store.connect() as db:
                excluded = {p["id"] for p in self.store.catalog()["projects"] if p.get("capture_excluded")}
                assigned_projects = {}
                initialized = db.execute("SELECT value FROM capture_meta WHERE key='initialized'").fetchone()
                for chat in chats:
                    assigned = projects["assignments"].get(chat["id"], chat.get("project_id"))
                    chat["project_id"] = assigned if assigned in valid_projects else ""
                    assigned_projects[chat["id"]] = chat["project_id"]
                    db.execute("INSERT OR REPLACE INTO captured_chats VALUES (?,?)",
                               (chat["id"], json.dumps(chat, ensure_ascii=False)))
                if identities:
                    # Source deletion removes only chat metadata, never the delivery ledger.
                    for ident, in db.execute("SELECT id FROM captured_chats").fetchall():
                        if ident not in identities:
                            db.execute("DELETE FROM captured_chats WHERE id=?", (ident,))
                for ident, marker, row in current:
                    seen = db.execute("SELECT digest FROM capture_seen WHERE id=?", (ident,)).fetchone()
                    if seen and seen[0] == marker:
                        continue
                    if initialized and assigned_projects.get(row["thread_id"]) not in excluded:
                        if row["size"] > 1_000_000:
                            continue
                        with closing(readonly(self.home / "thread_history_1.sqlite")) as source:
                            raw = source.execute("SELECT item_json FROM thread_items WHERE thread_id=? AND turn_id=? AND item_id=?",
                                                 (row["thread_id"], row["turn_id"], row["item_id"])).fetchone()
                        if not raw:
                            continue
                        payload = json.loads(raw[0])
                        digest = hashlib.sha256(raw[0].encode()).hexdigest()
                        value = user_text(payload)
                        # Runtime context updates aren't user tasks.
                        if value.strip() and not value.lstrip().startswith(("<environment_context>", "<system", "# AGENTS.md")):
                            event_id = ident + "-" + digest[:16]
                            entry = {"id": event_id, "thread_id": row["thread_id"], "turn_id": row["turn_id"],
                                     "item_id": row["item_id"], "created_at_ms": row["created_at_ms"],
                                     "excerpt": excerpt(value), "state": "待分析", "edited": bool(seen)}
                            db.execute("INSERT OR IGNORE INTO captured_inputs VALUES (?,?)",
                                       (event_id, json.dumps(entry, ensure_ascii=False)))
                    db.execute("INSERT OR REPLACE INTO capture_seen VALUES (?,?)", (ident, marker))
                for key, value in {"initialized": "1", "health": "运行中",
                                   "last_poll": str(time.time())}.items():
                    db.execute("INSERT OR REPLACE INTO capture_meta VALUES (?,?)", (key, value))
            if hasattr(self.store, "board"):
                self.store.board.reconcile()
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
            with self.store.connect() as db:
                db.execute("INSERT OR REPLACE INTO capture_meta VALUES ('health',?)",
                           ("采集不可用：索引格式或来源异常；保留上次结果，不读取其他文件",))

    def start(self):
        self.poll()
        def run():
            while not self.stop_event.wait(3):
                self.poll()
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=6)
