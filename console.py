#!/usr/bin/env python3
"""Local delivery ledger. Never executes business workflows."""
import argparse
import hashlib
import json
import os
import re
import secrets
import signal
import sqlite3
import sys
import threading
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager, nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from codex_host import Host
from project_platform import ProjectPlatform, DEFAULT_NODES
from codex_projects import DesktopProjects
from blocker_analysis import explain
from private_extensions import load_extension
from chat_capture import ChatCapture
from management import Management
from session_board import SessionBoard
from skill_registry import discover

ROOT = Path(__file__).resolve().parent
PHASES = ["需求", "方案", "开发", "测试", "待发布", "观察", "完成"]
STATUSES = ["待处理", "进行中", "阻塞", "待决策", "已完成"]
SCOPES = {"develop": "继续本地开发与隔离测试", "merge": "合并已验证的本地代码",
          "preflight": "发布预检（只读核验，不部署）"}


def now():
    return datetime.now(timezone.utc).isoformat()


def text(value, limit=4000):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError("文本格式或长度不合法")
    return value.strip()


class Store(ProjectPlatform):
    def __init__(self, path, source=None, projects=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        private_root = self.path.parent if self.path.parent.name == "private" else self.path.parent / "private"
        self.extension = load_extension(self, private_root / "extensions/adapter.py", source)
        self.project_source = projects
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS coordination (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        self.host = Host()
        self.capture = None
        self.init_platform()
        self.management = Management(self, Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
        self.board = SessionBoard(self, Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
        self.workers = {}
        # A send interrupted by app shutdown is not safe to repeat.
        with self.connect() as db:
            for ident, raw in db.execute("SELECT id,data FROM coordination").fetchall():
                plan = json.loads(raw)
                for job in plan.get("jobs", []):
                    if job["state"] in ["发送中", "执行中"]:
                        job.update(state="结果未知", detail="应用重启，请核对关联对话；不自动重试")
                db.execute("UPDATE coordination SET data=? WHERE id=?", (json.dumps(plan, ensure_ascii=False), ident))
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        try:
            with db:
                yield db
        finally:
            db.close()

    def listing(self):
        with self.lock, self.connect() as db:
            return sorted((self.decorate(json.loads(r[0]), db) for r in db.execute("SELECT data FROM items")),
                          key=lambda r: r["updated"], reverse=True)

    def get(self, ident):
        with self.lock, self.connect() as db:
            row = db.execute("SELECT data FROM items WHERE id=?", (ident,)).fetchone()
            if not row:
                raise ValueError("需求不存在")
            return self.decorate(json.loads(row[0]), db)

    def decorate(self, item, db):
        item.setdefault("workflow", DEFAULT_NODES)
        row = db.execute("SELECT data FROM coordination WHERE id=?", (item["id"],)).fetchone()
        if row:
            item["coordination"] = json.loads(row[0])
            item["coordination"]["stale"] = (
                item["coordination"].get("fingerprint") is not None and
                item["coordination"]["fingerprint"] != self.fingerprint(item))
        if self.project_source is None:
            project = self.ensure_project(db, item["project"])
            item["project_id"] = project["id"]
            item["project_label"] = project["name"]
        else:
            snapshot = self.project_source.snapshot()
            thread_id = item.get("coordination", {}).get("thread", {}).get("id")
            ident = snapshot["assignments"].get(thread_id, item.get("project_id", ""))
            project = next((p for p in snapshot["projects"] if p["id"] == ident), None)
            item["project_id"] = project["id"] if project else ""
            item["project_label"] = project["name"] if project else "未关联 Codex 项目"
        item["blocker_analysis"] = (self.extension.explain(item) if self.extension and
                                    hasattr(self.extension, "explain") else explain(item))
        if self.extension and self.extension.handles(item["id"]):
            item["display_title"] = self.extension.display_title(item)
        return item

    def save(self, db, item):
        item = {k: v for k, v in item.items()
                if k not in ["coordination", "project_label", "blocker_analysis", "display_title"]}
        db.execute("INSERT OR REPLACE INTO items VALUES (?,?)",
                   (item["id"], json.dumps(item, ensure_ascii=False)))

    def fingerprint(self, item):
        payload = [item.get("source_digest"), item["version"], item["candidate"], item["evidence"],
                   item.get("source_health", "正常")]
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def coordinate(self, ident, data, actor):
        with self.lock:
            if self.extension and self.extension.handles(ident):
                self.sync()
            item = self.get(ident)
            plan = item.get("coordination", {"revision": 0, "jobs": []})
            plan.pop("stale", None)
            if data.get("revision") != plan["revision"]:
                raise ValueError("审批信息已更新，请刷新后重试")
            action = data.get("action")
            if item.get("archived") and action in ["propose", "approve", "dispatch"]:
                raise ValueError("已归档需求不可推进")
            if action in ["bind", "approve", "reject", "dispatch", "cancel"] and actor != "human":
                raise ValueError("此操作必须由人在控制台确认")
            active = [j for j in plan["jobs"] if j["state"] in ["待发送", "发送中", "执行中", "结果未知"]]
            if action == "bind":
                if active:
                    raise ValueError("存在未结束的推进指令，不可更换对话")
                ident_thread = text(data.get("thread_id", ""), 80)
                if not re.fullmatch(r"[a-f0-9-]{36}", ident_thread):
                    raise ValueError("无效对话编号")
                thread = self.host.read(ident_thread)
                plan.update(thread=thread, progress=text(data.get("progress", ""), 2000),
                            progress_evidence=text(data.get("progress_evidence", ""), 2000))
            elif action == "propose":
                if active:
                    raise ValueError("存在未结束的推进指令")
                scope = data.get("scope")
                if scope not in SCOPES:
                    raise ValueError("不支持该审批类型；生产操作必须单独授权")
                plan.update(scope=scope, next_step=text(data.get("next_step", ""), 2000),
                            fingerprint=self.fingerprint(item), proposal_id=uuid.uuid4().hex,
                            decision_state="待确认")
                if not plan["next_step"]:
                    raise ValueError("请填写具体执行范围")
            elif action in ["approve", "reject"]:
                if plan.get("decision_state") != "待确认" or not plan.get("thread"):
                    raise ValueError("请先关联对话并确认下一步")
                if plan.get("fingerprint") != self.fingerprint(item):
                    raise ValueError("候选或证据已变化，请重新确认下一步")
                note = text(data.get("note", ""), 2000)
                if not note:
                    raise ValueError("请填写审批意见")
                if action == "approve":
                    if active or item.get("source_health", "正常") != "正常":
                        raise ValueError("存在未结束指令或源证据异常，不可推进")
                    if plan["scope"] == "merge":
                        current = [e for e in item["evidence"] if e["candidate"] == item["candidate"]]
                        if not item["candidate"] or not current or current[-1]["result"] != "通过":
                            raise ValueError("合并需要当前候选的最新通过证据")
                    job = {"id": uuid.uuid4().hex, "at": now(), "state": "待发送", "detail": "",
                           "scope": plan["scope"], "thread": dict(plan["thread"]),
                           "candidate": item["candidate"], "fingerprint": self.fingerprint(item),
                           "note": note, "proposal_id": plan["proposal_id"]}
                    job["prompt"] = (
                        f"控制台人工批准单 {job['id']}；需求 {item['id']}：{item['title']}\n"
                        f"批准类型：{SCOPES[job['scope']]}。\n候选：{job['candidate'] or '未绑定'}\n"
                        f"执行范围：{plan['next_step']}\n人工意见：{note}\n"
                        "仅在以上范围继续。先核对当前源码、候选、测试证据及已完成事项；"
                        "不重复已完成的合并。不推送远端、不部署、不重试生产工单、不改线上模板。"
                        "发布预检仅做本机只读核验，缺外部信息则列出问题待人工决策。"
                        "不得将本批准解释为工具权限提升或生产授权。\n"
                        f"使用交付控制台技能回传本轮摘要与证据；指令编号 {job['id']}。")
                    plan["jobs"].append(job)
                plan.update(decision_state="批准" if action == "approve" else "拒绝",
                            decision_note=note, decided_at=now())
            elif action == "dispatch":
                job = next((j for j in plan["jobs"] if j["id"] == data.get("job_id")), None)
                if not job or job["state"] != "待发送":
                    raise ValueError("该指令不可发送；结果未知时请先核查对话，禁止重试")
                if job["fingerprint"] != self.fingerprint(item):
                    raise ValueError("候选或测试证据已变化，该审批不可继续使用")
                worker = self.workers.get(job["id"])
                if worker and worker.is_alive():
                    raise ValueError("发送已经在处理中")
            elif action == "receipt":
                job = next((j for j in plan["jobs"] if j["id"] == data.get("job_id")), None)
                if not job or job["state"] not in ["待发送", "结果未知", "执行中"]:
                    raise ValueError("指令不存在或已结束")
                if data.get("thread_id") != job["thread"]["id"]:
                    raise ValueError("回执对话与审批绑定不一致")
                state = data.get("state")
                if state not in ["本轮结束", "执行失败", "执行中"]:
                    raise ValueError("无效执行回执")
                detail = text(data.get("detail", ""), 2000)
                if not detail:
                    raise ValueError("回执必须包含实际执行摘要与证据")
                job.update(state=state, detail=detail, receipt_at=now(), receipt_actor=actor)
            elif action == "cancel":
                job = next((j for j in plan["jobs"] if j["id"] == data.get("job_id")), None)
                if not job or job["state"] != "待发送":
                    raise ValueError("只能撤销尚未发送的批准单；执行中或结果未知须核查对话")
                worker = self.workers.get(job["id"])
                if worker and worker.is_alive():
                    raise ValueError("发送连接仍在处理中，请等待回执")
                job.update(state="已撤销", detail="人工撤销尚未发送的指令", receipt_at=now())
            else:
                raise ValueError("未知协调操作")
            plan["revision"] += 1
            plan["updated"] = now()
            with self.connect() as db:
                db.execute("INSERT OR REPLACE INTO coordination VALUES (?,?)",
                           (ident, json.dumps(plan, ensure_ascii=False)))
            if action == "dispatch":
                def receipt(state, detail, turn_id=""):
                    with self.lock, self.connect() as db:
                        row = db.execute("SELECT data FROM coordination WHERE id=?", (ident,)).fetchone()
                        latest = json.loads(row[0])
                        target = next(j for j in latest["jobs"] if j["id"] == job["id"])
                        target.update(state=state, detail=detail, turn_id=turn_id, receipt_at=now())
                        latest["revision"] += 1
                        db.execute("UPDATE coordination SET data=? WHERE id=?",
                                   (json.dumps(latest, ensure_ascii=False), ident))
                worker = threading.Thread(target=self.host.run, args=(job, receipt), daemon=True)
                self.workers[job["id"]] = worker
                worker.start()
            return self.get(ident)

    def event(self, item, message, actor):
        item["updated"] = now()
        item["version"] += 1
        item["events"].append({"at": item["updated"], "actor": actor, "message": message})

    def create(self, data, actor="agent", db=None):
        title = text(data.get("title", ""), 160)
        if not title:
            raise ValueError("请输入需求标题")
        kind = data.get("kind", "需求")
        if kind not in ["需求", "Bug"]:
            raise ValueError("无效类型")
        item = {"id": uuid.uuid4().hex[:12], "title": title, "kind": kind,
                "project": text(data.get("project", "未分类"), 80) or "未分类",
                "description": text(data.get("description", "")),
                "acceptance": text(data.get("acceptance", "")), "phase": "需求",
                "status": "待处理", "blocker": "", "candidate": "", "version": 0,
                "updated": now(), "events": [], "decisions": [], "evidence": [],
                "origin": "local"}
        self.event(item, "创建需求", actor)
        with self.lock, (self.connect() if db is None else nullcontext(db)) as db:
            project = self.project_for(db, data)
            item.update(project=project["name"], project_id=project["id"],
                        workflow=project["template"], template_revision=project["revision"])
            self.save(db, item)
        return item

    def mutate(self, ident, data, actor="agent", db=None):
        with self.lock, (self.connect() if db is None else nullcontext(db)) as db:
            item = self.get(ident)
            if item["origin"] != "local":
                raise ValueError("导入记录只读，请在原交付工具中更新后同步")
            if data.get("version") != item["version"]:
                raise ValueError("记录已更新，请刷新后重试")
            action = data.get("action")
            if item.get("archived") and action != "restore":
                raise ValueError("已归档需求请先恢复")
            if action == "update":
                phase = data.get("phase", item["phase"])
                status = data.get("status", item["status"])
                node = next((n for n in item["workflow"] if n["name"] == phase), None)
                if not node or status not in STATUSES:
                    raise ValueError("无效阶段或状态")
                if actor == "agent" and (node["human"] or status == "已完成"):
                    raise ValueError("上线观察及最终完成须由人确认")
                if phase == "完成" and status != "已完成":
                    raise ValueError("完成阶段须使用已完成状态")
                if status == "已完成" and phase != "完成":
                    raise ValueError("已完成状态须使用完成阶段")
                blocker = text(data.get("blocker", item["blocker"]))
                if status == "阻塞" and not blocker:
                    raise ValueError("请填写阻塞原因")
                candidate = text(data.get("candidate", item["candidate"]), 160)
                if node["evidence"]:
                    current_evidence = [e for e in item["evidence"] if e["candidate"] == candidate]
                    if not candidate or not current_evidence or current_evidence[-1]["result"] != "通过":
                        raise ValueError("缺少当前候选版本的通过证据")
                if node["approval"]:
                    decisions = [d for d in item["decisions"] if d["candidate"] == candidate]
                    if (not decisions or decisions[-1]["state"] != "批准" or
                            decisions[-1].get("evidence_id") != current_evidence[-1]["id"]):
                        raise ValueError("缺少当前候选和最新测试证据的人工批准记录")
                if node["human"] and not text(data.get("note", "")):
                    raise ValueError("请填写实际部署或验收凭据说明；批准不代表已经部署")
                for key, limit in [("title", 160), ("project", 80), ("description", 4000),
                                   ("acceptance", 4000)]:
                    if key in data:
                        if key == "project" and data[key] != item["project"]:
                            raise ValueError("已有需求项目不可直接更换")
                        item[key] = text(data[key], limit)
                        if key in ["title", "project"] and not item[key]:
                            raise ValueError("标题和项目不可为空")
                if "kind" in data:
                    if data["kind"] not in ["需求", "Bug"]:
                        raise ValueError("无效类型")
                    item["kind"] = data["kind"]
                item.update(phase=phase, status=status, blocker=blocker, candidate=candidate)
                self.event(item, text(data.get("note", ""), 2000) or "更新阶段与状态", actor)
            elif action in ["archive", "restore"]:
                if actor != "human":
                    raise ValueError("归档和恢复须由人确认")
                if any(j["state"] in ["待发送", "发送中", "执行中", "结果未知"]
                       for j in item.get("coordination", {}).get("jobs", [])):
                    raise ValueError("存在未结束指令，不可归档或恢复")
                item["archived"] = action == "archive"
                self.event(item, "归档需求" if item["archived"] else "恢复需求", actor)
            elif action == "evidence":
                result = data.get("result")
                candidate = text(data.get("candidate", ""), 160)
                label = text(data.get("label", ""), 160)
                if result not in ["通过", "失败", "未验证"] or not candidate or not label:
                    raise ValueError("证据须包含标题、候选版本及结果")
                item["evidence"].append({"id": uuid.uuid4().hex[:12], "at": now(),
                                        "candidate": candidate, "label": label, "result": result,
                                        "detail": text(data.get("detail", "")), "actor": actor,
                                        "verification": "人工/代理记录，未独立认证", "profiles": {}})
                self.event(item, "追加测试证据：" + label + " / " + result, actor)
            elif action == "decision":
                label = text(data.get("label", ""), 160)
                if not label or not item["candidate"]:
                    raise ValueError("请先填写候选版本和决策标题")
                item["decisions"].append({"id": uuid.uuid4().hex[:12], "label": label,
                                         "candidate": item["candidate"], "state": "待确认",
                                         "at": now(), "note": ""})
                self.event(item, "请求人工决策：" + label, actor)
            elif action == "resolve":
                if actor != "human":
                    raise ValueError("代理不能代替人批准")
                decision = next((d for d in item["decisions"] if d["id"] == data.get("decision")), None)
                if not decision or decision["state"] != "待确认":
                    raise ValueError("决策不存在或已处理")
                if decision["candidate"] != item["candidate"]:
                    raise ValueError("候选已变化，请创建新的决策")
                state = data.get("state")
                note = text(data.get("note", ""))
                if state not in ["批准", "拒绝"] or not note:
                    raise ValueError("请填写决策结果和意见")
                if state == "批准":
                    current = [e for e in item["evidence"] if e["candidate"] == item["candidate"]]
                    if not current or current[-1]["result"] != "通过":
                        raise ValueError("当前候选最后一次测试未通过，不可批准")
                    decision["evidence_id"] = current[-1]["id"]
                decision.update(state=state, note=note, resolved_at=now())
                self.event(item, "人工" + state + "：" + decision["label"] + "；" + note, actor)
            else:
                raise ValueError("未知操作")
            self.save(db, item)
            return item

    def sync(self):
        if self.extension is None:
            return {"imported": 0, "errors": ["未配置本机私有导入器"]}
        return self.extension.sync()

    def read_source(self, path):
        if self.extension is None:
            raise ValueError("未配置本机私有导入器")
        return self.extension.read_source(path)


def make_server(store, port=8767):
    human_token, agent_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, value, mime="application/json; charset=utf-8", frame_self=False):
            raw = json.dumps(value, ensure_ascii=False).encode() if isinstance(value, (dict, list)) else value
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; frame-ancestors " + ("'self'" if frame_self else "'none'"))
            self.end_headers()
            self.wfile.write(raw)

        def valid_host(self):
            return self.headers.get("Host") in [f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"]

        def do_GET(self):
            if not self.valid_host():
                return self.reply(403, {"error": "非法 Host"})
            path = urlparse(self.path).path
            if path == "/api/session":
                if self.headers.get("Sec-Fetch-Site") not in [None, "same-origin"] or self.headers.get("Origin") not in [None, f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"]:
                    return self.reply(403, {"error": "非法来源"})
                return self.reply(200, {"token": human_token, "phases": PHASES,
                                        "statuses": STATUSES, "source_import": bool(store.extension)})
            if path.startswith("/api/"):
                if self.headers.get("Authorization") not in ["Bearer " + human_token, "Bearer " + agent_token]:
                    return self.reply(401, {"error": "未授权"})
                if path in ["/api/items", "/api/export"]:
                    try:
                        return self.reply(200, store.listing())
                    except ValueError as exc:
                        return self.reply(503, {"error": str(exc)})
                if path == "/api/catalog":
                    try:
                        return self.reply(200, store.catalog())
                    except ValueError as exc:
                        return self.reply(503, {"error": str(exc)})
                if path == "/api/intake":
                    return self.reply(200, store.intake_listing())
                if path == "/api/capture":
                    return self.reply(200, store.capture.snapshot() if store.capture else
                                      {"enabled": False, "health": "未启用", "chats": [], "inputs": []})
                if path == "/api/management":
                    return self.reply(200, store.management.snapshot())
                if path == "/api/board":
                    return self.reply(200, store.board.snapshot())
                if path == "/api/session-skills":
                    return self.reply(200, discover())
                if path == "/api/board/context":
                    try:
                        from urllib.parse import parse_qs
                        ident = parse_qs(urlparse(self.path).query).get("thread_id", [""])[0]
                        return self.reply(200, store.board.context(ident))
                    except ValueError as exc:
                        return self.reply(400, {"error": str(exc)})
                if path == "/api/management/chats":
                    try:
                        return self.reply(200, store.management.chat_index()[0])
                    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError):
                        return self.reply(503, {"error": "本机会话索引不可用"})
                return self.reply(404, {"error": "接口不存在"})
            files = {"/": ("sessions.html", "text/html; charset=utf-8"),
                     "/legacy": ("index.html", "text/html; charset=utf-8"),
                     "/sessions.js": ("sessions.js", "text/javascript; charset=utf-8"),
                     "/sessions.css": ("sessions.css", "text/css; charset=utf-8"),
                     "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                     "/management.js": ("management.js", "text/javascript; charset=utf-8"),
                     "/i18n.js": ("i18n.js", "text/javascript; charset=utf-8"),
                     "/style.css": ("style.css", "text/css; charset=utf-8"),
                     "/lucide.js": ("lucide.js", "text/javascript; charset=utf-8"),
                     "/icon.png": ("icon.png", "image/png")}
            if path not in files:
                return self.reply(404, {"error": "文件不存在"})
            name, mime = files[path]
            asset = ROOT / "web" / name
            if path == "/i18n.js":
                private_root = store.path.parent if store.path.parent.name == "private" else store.path.parent / "private"
                private = private_root / "web/translations.js"
                if private.is_file() and not private.is_symlink():
                    asset = private
            return self.reply(200, asset.read_bytes(), mime, frame_self=path=="/legacy")

        def do_POST(self):
            if not self.valid_host():
                return self.reply(403, {"error": "非法 Host"})
            auth = self.headers.get("Authorization")
            actor = "human" if auth == "Bearer " + human_token else "agent"
            if auth not in ["Bearer " + human_token, "Bearer " + agent_token]:
                return self.reply(401, {"error": "未授权"})
            origin = self.headers.get("Origin")
            if origin and origin not in [f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"]:
                return self.reply(403, {"error": "非法来源"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 32000:
                    raise ValueError("请求长度不合法")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("请求必须为对象")
                path = urlparse(self.path).path
                if path == "/api/items":
                    result = store.create(data, actor)
                elif path == "/api/catalog":
                    result = store.manage_catalog(data, actor)
                elif path == "/api/intake":
                    result = store.propose_change(data, actor)
                elif re.fullmatch(r"/api/intake/[a-f0-9]+", path):
                    result = store.review_change(path.split("/")[-1], data, actor)
                elif path == "/api/sync":
                    result = store.sync()
                elif path == "/api/threads":
                    if actor != "human":
                        raise ValueError("对话查询由人在控制台发起")
                    result = store.host.threads(text(data.get("query", ""), 160))
                elif path == "/api/management/history":
                    result = store.management.history(data, actor)
                elif re.fullmatch(r"/api/management/jobs/[a-f0-9]+/cancel", path):
                    result = store.management.cancel(path.split("/")[-2], actor)
                elif re.fullmatch(r"/api/management/candidates/[a-f0-9]+", path):
                    result = store.management.review(path.split("/")[-1], data, actor)
                elif path == "/api/management/report":
                    result = store.management.report(data, actor)
                elif path == "/api/board/reconcile":
                    if actor != "human":
                        raise ValueError("手动对账须由人在控制台发起")
                    store.board.reconcile()
                    result = store.board.snapshot()
                elif path == "/api/board/report":
                    result = store.board.report(data, actor)
                elif path == "/api/board/historical-review":
                    result = store.board.historical_review(data, actor)
                elif re.fullmatch(r"/api/items/[a-z0-9-]+/coordinate", path):
                    result = store.coordinate(path.split("/")[-2], data, actor)
                elif re.fullmatch(r"/api/items/[a-z0-9-]+", path):
                    result = store.mutate(path.split("/")[-1], data, actor)
                else:
                    return self.reply(404, {"error": "接口不存在"})
                return self.reply(200, result)
            except (ValueError, TypeError, KeyError) as exc:
                return self.reply(400, {"error": str(exc)})
            except Exception:
                return self.reply(500, {"error": "内部错误，请查看本地运行环境"})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return server, agent_token


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--data", type=Path, default=Path.home() / ".local/share/delivery-console")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--capture-chats", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    store = Store(args.data / "private/console.sqlite3", args.source, projects=DesktopProjects())
    store.board.start()
    capture_enabled = args.capture_chats or (args.data / "private/capture-enabled").exists()
    if capture_enabled:
        store.capture = ChatCapture(store, Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
        store.capture.start()
    for port in range(args.port, args.port + 20):
        try:
            server, token = make_server(store, port)
            break
        except OSError:
            continue
    else:
        sys.exit("没有可用端口")
    runtime = args.data / "runtime.json"
    runtime.write_text(json.dumps({"url": f"http://127.0.0.1:{server.server_port}", "agent_token": token,
                                  "pid": os.getpid()}))
    os.chmod(runtime, 0o600)
    print(f"需求交付控制台 http://127.0.0.1:{server.server_port}", flush=True)
    def terminate(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    try:
        server.serve_forever()
    finally:
        store.board.close()
        store.management.close()
        if store.capture:
            store.capture.close()
        server.server_close()
        runtime.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
