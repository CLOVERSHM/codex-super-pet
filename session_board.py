"""Session index reconciliation and explicit per-turn status declarations."""
import hashlib
import json
import re
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

from chat_capture import excerpt, readonly

STATES = {"in_progress": "进行中", "completed": "已完成", "blocked": "遇阻塞",
          "waiting_approval": "待审批", "waiting_user": "待用户补充", "unverified": "待核实"}
THREAD = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
SKILL = re.compile(r"[a-z][a-z0-9-]{1,62}\Z")


def clean(value, limit=240, required=False):
    if not isinstance(value, str) or len(value) > limit * 8:
        raise ValueError("状态文本格式或长度不合法")
    result = excerpt(value)[:limit]
    if required and not result:
        raise ValueError("请填写状态摘要与原因")
    return result


def digest(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class SessionBoard:
    def __init__(self, store, home, interval=600):
        self.store, self.home, self.interval = store, Path(home), interval
        self.stop_event = threading.Event()
        self.thread = None
        with store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS board_threads(id TEXT PRIMARY KEY,data TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS board_reports(id TEXT PRIMARY KEY,data TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS board_history_reviews(id TEXT PRIMARY KEY,data TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS board_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")

    def reconcile(self):
        try:
            catalog = {p["id"]: p for p in self.store.catalog()["projects"]}
            assignments = self.store.project_source.snapshot()["assignments"] if self.store.project_source else {}
            with closing(readonly(self.home / "state_5.sqlite")) as src:
                threads = [dict(r) for r in src.execute(
                    "SELECT id,COALESCE(name,title) title,project_id,archived,updated_at_ms FROM threads")]
            valid = {t["id"]: t for t in threads if THREAD.fullmatch(t["id"])}
            with closing(readonly(self.home / "thread_history_1.sqlite")) as src:
                inputs = {}
                for row in src.execute(
                    "SELECT thread_id,turn_id,item_id,created_at_ms,updated_at_ordinal,length(item_json) size "
                    "FROM thread_items WHERE item_type='userMessage' ORDER BY thread_id,created_at_ms,turn_id,item_id"):
                    if row["thread_id"] in valid:
                        inputs.setdefault(row["thread_id"], []).append(
                            [row["turn_id"], row["item_id"], row["created_at_ms"],
                             row["updated_at_ordinal"], row["size"]])
            with self.store.lock, self.store.connect() as db:
                for ident, thread in valid.items():
                    project_id = assignments.get(ident, thread["project_id"])
                    project = catalog.get(project_id)
                    if not project or project.get("capture_excluded"):
                        db.execute("DELETE FROM board_threads WHERE id=?", (ident,))
                        db.execute("DELETE FROM board_reports WHERE id=?", (ident,))
                        db.execute("DELETE FROM board_history_reviews WHERE id=?", (ident,))
                        continue
                    rows = inputs.get(ident, [])
                    source_hash = digest(rows)
                    old = db.execute("SELECT data FROM board_threads WHERE id=?", (ident,)).fetchone()
                    old = json.loads(old[0]) if old else None
                    changed = (old is None or old.get("source_hash") != source_hash or
                               old["project_id"] != project_id)
                    version = (old.get("observation_version", 0) if old else 0) + int(changed)
                    record = {"id": ident, "project_id": project_id,
                              "title": clean(thread["title"] or "未命名会话", 160),
                              "archived": bool(thread["archived"]), "updated_at_ms": thread["updated_at_ms"],
                              "source_hash": source_hash, "observation_version": version,
                              "observation_hash": digest([source_hash, project_id, version]),
                              "latest_turn_id": rows[-1][0] if rows else "",
                              "input_count": len(rows),
                              "changed_at_ms": int(time.time() * 1000)
                              if changed else old["changed_at_ms"]}
                    db.execute("INSERT OR REPLACE INTO board_threads VALUES (?,?)",
                               (ident, json.dumps(record, ensure_ascii=False)))
                for ident, in db.execute("SELECT id FROM board_threads").fetchall():
                    if ident not in valid:
                        db.execute("DELETE FROM board_threads WHERE id=?", (ident,))
                        db.execute("DELETE FROM board_history_reviews WHERE id=?", (ident,))
                db.execute("INSERT OR REPLACE INTO board_meta VALUES ('health',?)", ("运行中",))
                db.execute("INSERT OR REPLACE INTO board_meta VALUES ('last_checked',?)", (str(time.time()),))
        except (ValueError, OSError, sqlite3.Error, KeyError, TypeError):
            with self.store.connect() as db:
                db.execute("INSERT OR REPLACE INTO board_meta VALUES ('health',?)",
                           ("对账不可用：本机索引异常；保留上次快照，状态不能视为最新",))

    def snapshot(self):
        excluded = {p["id"] for p in self.store.catalog()["projects"] if p.get("capture_excluded")}
        with self.store.lock, self.store.connect() as db:
            meta = dict(db.execute("SELECT key,value FROM board_meta"))
            records = [json.loads(r[0]) for r in db.execute("SELECT data FROM board_threads")]
            reports = {r[0]: json.loads(r[1]) for r in db.execute("SELECT id,data FROM board_reports")}
            reviews = {r[0]: json.loads(r[1]) for r in db.execute("SELECT id,data FROM board_history_reviews")}
        sessions = []
        for record in records:
            if record["project_id"] in excluded:
                continue
            report = reports.get(record["id"])
            current = bool(report and report["observation_hash"] == record["observation_hash"]
                           and report["project_id"] == record["project_id"])
            review = reviews.get(record["id"])
            reviewed = bool(review and review["observation_hash"] == record["observation_hash"]
                            and review["project_id"] == record["project_id"])
            visible = report if current else review if reviewed else None
            sessions.append({**record, "state": visible["state"] if visible else
                             "awaiting_report" if report or review else "unverified",
                             "report": visible,
                             "previous_report": (report if not current else review if not reviewed else None)})
        sessions.sort(key=lambda s: s["updated_at_ms"] or 0, reverse=True)
        return {"sessions": sessions, "health": meta.get("health", "尚未对账"),
                "last_checked": meta.get("last_checked"), "interval_seconds": self.interval,
                "source": "本机元数据对账；非可靠轮次结束事件"}

    def context(self, thread_id):
        if not isinstance(thread_id, str) or not THREAD.fullmatch(thread_id):
            raise ValueError("无效会话编号")
        snapshot = self.snapshot()
        if snapshot["health"] != "运行中":
            raise ValueError("对账来源不可用，无法核对回报版本")
        session = next((s for s in snapshot["sessions"] if s["id"] == thread_id), None)
        if not session:
            raise ValueError("会话未同步或项目已加白")
        return {key: session[key] for key in ["id", "project_id", "title", "latest_turn_id",
                                              "observation_hash", "input_count"]}

    def report(self, data, actor):
        if actor != "agent":
            raise ValueError("状态回报由当前会话的 skill 提交；人工操作不冒充代理")
        self.reconcile()
        current = self.context(data.get("thread_id"))
        if data.get("project_id") != current["project_id"] or data.get("observation_hash") != current["observation_hash"]:
            raise ValueError("会话归属或用户输入已变化，请重新获取回报上下文")
        if not current["latest_turn_id"] or data.get("turn_id") != current["latest_turn_id"]:
            raise ValueError("回报必须绑定最新用户输入轮次")
        if data.get("state") not in STATES:
            raise ValueError("无效统一状态")
        task_key = data.get("task_key")
        if not isinstance(task_key, str) or not 1 <= len(task_key) <= 80 or not re.fullmatch(r"[\w.-]+", task_key):
            raise ValueError("任务标识需由当前 skill 明确给出")
        skill_id = data.get("skill_id", "session-status")
        if not isinstance(skill_id, str) or not SKILL.fullmatch(skill_id):
            raise ValueError("无效 skill 标识")
        project = next(p for p in self.store.catalog()["projects"] if p["id"] == current["project_id"])
        if skill_id != "session-status" and skill_id not in project.get("session_skills", []):
            raise ValueError("此项目未启用该专属 skill")
        evidence = data.get("evidence", [])
        if not isinstance(evidence, list) or len(evidence) > 8:
            raise ValueError("证据引用须为不超过8条的列表")
        report = {"id": current["id"], "project_id": current["project_id"],
                  "turn_id": current["latest_turn_id"], "observation_hash": current["observation_hash"],
                  "task_key": task_key, "workflow": clean(data.get("workflow", ""), 80, True),
                  "stage": clean(data.get("stage", ""), 80, True), "state": data["state"],
                  "summary": clean(data.get("summary", ""), 240, True),
                  "reason": clean(data.get("reason", ""), 240,
                                  data["state"] in ["blocked", "waiting_approval", "waiting_user"]),
                  "next_action": clean(data.get("next_action", ""), 240),
                  "evidence": [clean(item, 160) for item in evidence],
                  "skill_id": skill_id, "reported_at_ms": int(time.time() * 1000),
                  "source": "skill 声明，非独立验证"}
        with self.store.lock, self.store.connect() as db:
            row = db.execute("SELECT data FROM board_threads WHERE id=?", (current["id"],)).fetchone()
            if not row or json.loads(row[0])["observation_hash"] != current["observation_hash"]:
                raise ValueError("对账版本已变化，请重新读取")
            old = db.execute("SELECT data FROM board_reports WHERE id=?", (current["id"],)).fetchone()
            if old and json.loads(old[0])["observation_hash"] == current["observation_hash"]:
                old = json.loads(old[0])
                if {k: v for k, v in old.items() if k != "reported_at_ms"} != {
                        k: v for k, v in report.items() if k != "reported_at_ms"}:
                    raise ValueError("同一输入已有不同回报；新任务请等待新输入后提交")
                return old
            db.execute("INSERT OR REPLACE INTO board_reports VALUES (?,?)",
                       (current["id"], json.dumps(report, ensure_ascii=False)))
        return report

    def historical_review(self, data, actor):
        if actor != "human":
            raise ValueError("历史复核只能由人工发起")
        self.reconcile()
        current = self.context(data.get("thread_id"))
        if (data.get("project_id") != current["project_id"] or
                data.get("observation_hash") != current["observation_hash"]):
            raise ValueError("历史复核版本或项目归属已经变化")
        if data.get("state") not in STATES:
            raise ValueError("无效统一状态")
        evidence = data.get("evidence", [])
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 4:
            raise ValueError("历史复核需要脱敏证据引用")
        review = {"id": current["id"], "project_id": current["project_id"],
                  "turn_id": current["latest_turn_id"], "observation_hash": current["observation_hash"],
                  "task_key": clean(data.get("task_key", ""), 80, True),
                  "workflow": "历史会话", "stage": clean(data.get("stage", ""), 80, True),
                  "state": data["state"], "summary": clean(data.get("summary", ""), 240, True),
                  "reason": clean(data.get("reason", ""), 240,
                                  data["state"] in ("blocked", "waiting_approval", "waiting_user", "unverified")),
                  "next_action": clean(data.get("next_action", ""), 240),
                  "evidence": [clean(item, 160, True) for item in evidence],
                  "skill_id": "historical-review", "reported_at_ms": int(time.time() * 1000),
                  "source": "历史复核；依据当时对话答复，非实时 skill 或独立完成认证"}
        with self.store.lock, self.store.connect() as db:
            row = db.execute("SELECT data FROM board_threads WHERE id=?", (current["id"],)).fetchone()
            if not row or json.loads(row[0])["observation_hash"] != current["observation_hash"]:
                raise ValueError("对账版本已变化，请重新读取")
            db.execute("INSERT OR REPLACE INTO board_history_reviews VALUES (?,?)",
                       (current["id"], json.dumps(review, ensure_ascii=False)))
        return review

    def start(self):
        self.reconcile()
        def run():
            while not self.stop_event.wait(self.interval):
                self.reconcile()
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
