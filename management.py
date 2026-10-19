"""Local historical review and evidence-based reports; no model or host writes."""
import hashlib
import json
import re
import sqlite3
import threading
import uuid
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from chat_capture import excerpt, readonly, user_text

SHANGHAI = timezone(timedelta(hours=8))


def period(data):
    try:
        start, end = date.fromisoformat(data["start"]), date.fromisoformat(data["end"])
    except (ValueError, TypeError, KeyError):
        raise ValueError("请选择有效的起止日期")
    if end < start or (end - start).days > 365:
        raise ValueError("时间范围须为 1 至 366 天")
    return (int(datetime.combine(start, time.min, SHANGHAI).timestamp() * 1000),
            int(datetime.combine(end + timedelta(days=1), time.min, SHANGHAI).timestamp() * 1000))


def stamp():
    return datetime.now(SHANGHAI).isoformat()


def triage(value):
    # This is a transparent local prefilter, not semantic/model classification.
    if re.fullmatch(r"(?:请问)?(?:今天|明天|现在)?(?:的)?天气(?:怎么样|如何|好吗)?[？?。！! ]*", value.strip()):
        return "闲聊线索"
    return "需求线索" if re.search(r"新增|增加|支持|修复|需求|修改|删除|实现|bug", value, re.I) else "待人工分析"


class Management:
    def __init__(self, store, home):
        self.store, self.home = store, Path(home)
        self.workers = {}
        self.stopping = threading.Event()
        with store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS management_jobs(id TEXT PRIMARY KEY,data TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS management_candidates(id TEXT PRIMARY KEY,data TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS management_reports(id TEXT PRIMARY KEY,data TEXT NOT NULL)")
            for ident, raw in db.execute("SELECT id,data FROM management_jobs").fetchall():
                job = json.loads(raw)
                if job["state"] in ["排队中", "采集中"]:
                    job.update(state="已中断", error="服务重启，未自动重读历史；可重新发起")
                    self.save(db, "management_jobs", ident, job)

    def save(self, db, table, ident, value):
        db.execute(f"INSERT OR REPLACE INTO {table} VALUES (?,?)",
                   (ident, json.dumps(value, ensure_ascii=False)))

    def snapshot(self):
        excluded = self.excluded()
        with self.store.lock, self.store.connect() as db:
            result = {key: [json.loads(r[0]) for r in db.execute(f"SELECT data FROM {table} ORDER BY rowid DESC LIMIT {limit}")]
                    for key, table, limit in [("jobs", "management_jobs", 30),
                                             ("candidates", "management_candidates", 5000),
                                             ("reports", "management_reports", 30)]}
            for candidate in result["candidates"]:
                if candidate["project_id"] in excluded:
                    candidate["excerpt"] = ""
            return result

    def chat_index(self):
        catalog = self.store.catalog()
        projects = {p["id"]: p for p in catalog["projects"]}
        assignments = self.store.project_source.snapshot()["assignments"] if self.store.project_source else {}
        with closing(readonly(self.home / "state_5.sqlite")) as db:
            rows = [dict(r) for r in db.execute(
                "SELECT id,COALESCE(name,title) AS title,project_id,archived,updated_at_ms FROM threads")]
        for row in rows:
            ident = assignments.get(row["id"], row["project_id"])
            row["project_id"] = ident if ident in projects else ""
        return rows, projects

    def validate_ids(self, value, valid):
        if (not isinstance(value, list) or not value or len(value) > 1000 or
                any(not isinstance(v, str) or v not in valid for v in value)):
            raise ValueError("请选择当前有效的项目、会话或需求")
        return set(value)

    def excluded(self):
        return {p["id"] for p in self.store.catalog()["projects"] if p.get("capture_excluded")}

    def history(self, data, actor):
        if actor != "human":
            raise ValueError("历史正文采集须由人在管理面板发起")
        start, end = period(data)
        projects = {p["id"] for p in self.store.catalog()["projects"]}
        selected = self.validate_ids(data.get("project_ids"), projects)
        if not selected - self.excluded():
            raise ValueError("所选项目均已加白，无法采集")
        with self.store.lock, self.store.connect() as db:
            if any(json.loads(r[0])["state"] in ["排队中", "采集中"]
                   for r in db.execute("SELECT data FROM management_jobs")):
                raise ValueError("已有历史采集正在运行，请等待或取消")
            ident = uuid.uuid4().hex
            job = {"id": ident, "state": "排队中", "start": data["start"], "end": data["end"],
                   "project_ids": sorted(selected), "created_at": stamp(), "processed": 0,
                   "total": 0, "added": 0, "skipped": 0,
                   "analysis": "本地规则初筛，非 AI 语义分析；候选须人工确认后录入"}
            self.save(db, "management_jobs", ident, job)
        worker = threading.Thread(target=self.scan, args=(ident, start, end), daemon=True)
        self.workers = {k: v for k, v in self.workers.items() if v.is_alive()}
        self.workers[ident] = worker
        worker.start()
        return job

    def job(self, ident, db):
        row = db.execute("SELECT data FROM management_jobs WHERE id=?", (ident,)).fetchone()
        if not row:
            raise ValueError("采集任务不存在")
        return json.loads(row[0])

    def cancel(self, ident, actor):
        if actor != "human":
            raise ValueError("取消须由人操作")
        with self.store.lock, self.store.connect() as db:
            job = self.job(ident, db)
            if job["state"] in ["排队中", "采集中"]:
                job["state"] = "已取消"
                self.save(db, "management_jobs", ident, job)
            return job

    def scan(self, ident, start, end):
        try:
            chats, projects = self.chat_index()
            with self.store.connect() as db:
                job = self.job(ident, db)
            chosen = set(job["project_ids"]) - {p for p, v in projects.items() if v.get("capture_excluded")}
            chats = {c["id"]: c for c in chats if c["project_id"] in chosen}
            # Metadata first. Unselected/excluded plaintext is never fetched.
            with closing(readonly(self.home / "thread_history_1.sqlite")) as source:
                rows = [dict(r) for r in source.execute(
                    "SELECT thread_id,turn_id,item_id,created_at_ms,length(item_json) AS size "
                    "FROM thread_items WHERE item_type='userMessage' AND created_at_ms>=? AND created_at_ms<? "
                    "ORDER BY created_at_ms,thread_id,turn_id,item_id", (start, end)) if r["thread_id"] in chats]
            if len(rows) > 5000:
                raise ValueError("范围内超过 5000 条输入，请缩小日期或项目范围")
            with self.store.lock, self.store.connect() as db:
                job = self.job(ident, db)
                if job["state"] == "已取消":
                    return
                job.update(state="采集中", total=len(rows))
                self.save(db, "management_jobs", ident, job)
            for row in rows:
                if self.stopping.is_set():
                    return
                with self.store.lock, self.store.connect() as db:
                    job = self.job(ident, db)
                    if job["state"] == "已取消":
                        return
                    chat = chats[row["thread_id"]]
                    # Recheck settings AND membership immediately before each body read.
                    current_chats, current_projects = self.chat_index()
                    current = next((c for c in current_chats if c["id"] == chat["id"]), {})
                    allowed = (current.get("project_id") == chat["project_id"] and
                               not current_projects.get(chat["project_id"], {}).get("capture_excluded"))
                    if not allowed or row["size"] > 1_000_000:
                        job["skipped"] += 1
                    else:
                        with closing(readonly(self.home / "thread_history_1.sqlite")) as source:
                            raw = source.execute(
                                "SELECT item_json FROM thread_items WHERE thread_id=? AND turn_id=? AND item_id=? "
                                "AND item_type='userMessage' AND created_at_ms>=? AND created_at_ms<?",
                                (row["thread_id"], row["turn_id"], row["item_id"], start, end)).fetchone()
                        value = user_text(json.loads(raw[0])) if raw else ""
                        if value:
                            event = hashlib.sha256(("\0".join([row["thread_id"], row["turn_id"], row["item_id"]])
                                                    + "\0" + raw[0]).encode()).hexdigest()
                            exists = db.execute("SELECT data FROM management_candidates WHERE id=?", (event,)).fetchone()
                            if not exists:
                                entry = {"id": event, "job_id": ident, "project_id": chat["project_id"],
                                         "thread_id": chat["id"], "thread_title": excerpt(chat["title"] or "未命名会话"),
                                         "created_at_ms": row["created_at_ms"], "excerpt": excerpt(value),
                                         "triage": triage(value), "state": "待确认",
                                         "title": "", "analysis": "本地规则初筛；未推断需求变更或交付进度"}
                                self.save(db, "management_candidates", event, entry)
                                job["added"] += 1
                            else:
                                job["skipped"] += 1
                        else:
                            job["skipped"] += 1
                    job["processed"] += 1
                    self.save(db, "management_jobs", ident, job)
            with self.store.lock, self.store.connect() as db:
                job = self.job(ident, db)
                if job["state"] != "已取消":
                    job.update(state="待审阅", finished_at=stamp())
                    self.save(db, "management_jobs", ident, job)
        except (ValueError, OSError, sqlite3.Error, KeyError, TypeError):
            with self.store.lock, self.store.connect() as db:
                job = self.job(ident, db)
                if job["state"] != "已取消":
                    job.update(state="失败", error="历史来源不可用、格式变化或范围过大；已采集候选保留，请缩小范围或检查索引")
                    self.save(db, "management_jobs", ident, job)

    def review(self, ident, data, actor):
        if actor != "human":
            raise ValueError("历史候选须由人在面板确认")
        with self.store.lock:
            with self.store.connect() as db:
                row = db.execute("SELECT data FROM management_candidates WHERE id=?", (ident,)).fetchone()
                if not row:
                    raise ValueError("候选不存在")
                entry = json.loads(row[0])
            if entry["state"] != "待确认":
                return entry
            if data.get("decision") == "ignore":
                entry["state"] = "已忽略"
            else:
                if entry["project_id"] in self.excluded():
                    raise ValueError("项目已加白，不再录入历史候选")
                chats, _ = self.chat_index()
                if not any(c["id"] == entry["thread_id"] and c["project_id"] == entry["project_id"] for c in chats):
                    raise ValueError("会话归属已变化，请重新采集")
                intent = data.get("decision")
                if intent not in ["create", "update", "archive"]:
                    raise ValueError("请选择新增、修改、归档或忽略")
                title, description = data.get("title", ""), data.get("description", "")
                if intent != "archive" and (not isinstance(title, str) or not title.strip() or len(title) > 160 or
                                             not isinstance(description, str) or len(description) > 4000):
                    raise ValueError("请确认简短需求标题和描述")
                change_id = hashlib.sha256((entry["thread_id"] + "\0history-" + ident).encode()).hexdigest()[:24]
                existing = next((e for e in self.store.intake_listing() if e["id"] == change_id), None)
                if not existing:
                    existing = self.store.propose_change(
                        {"event_id": "history-" + ident, "thread_id": entry["thread_id"],
                         "project_id": entry["project_id"], "intent": intent,
                         "reason": "历史采集候选经用户人工分析确认，不是模型自动判断",
                         "item_id": data.get("item_id", ""), "version": data.get("version"),
                         "patch": {} if intent == "archive" else {"title": title.strip(), "description": description}}, actor)
                if existing["state"] == "待确认":
                    existing = self.store.review_change(existing["id"], {"decision": "accept"}, actor)
                if existing["state"] != "已采纳":
                    raise ValueError("对应变更已退回，请在收件箱核对")
                entry.update(state="已录入", change_id=change_id,
                             item_id=existing.get("created_item_id") or existing["input"]["item_id"])
            with self.store.connect() as db:
                self.save(db, "management_candidates", ident, entry)
            return entry

    def report(self, data, actor):
        if actor != "human":
            raise ValueError("报告须由人在管理面板发起")
        start, end = period(data)
        kind, scope = data.get("kind"), data.get("scope")
        if kind not in ["日报", "周报", "自定义报告"] or scope not in ["projects", "chats", "requirements"]:
            raise ValueError("无效报告类型或范围")
        with self.store.lock:
            return self.build_report(data, start, end)

    def build_report(self, data, start, end):
        kind, scope = data["kind"], data["scope"]
        items = self.store.listing()
        catalog = {p["id"]: p for p in self.store.catalog()["projects"]}
        chats, unavailable = [], False
        try:
            chats, _ = self.chat_index()
        except (ValueError, OSError, sqlite3.Error, KeyError, TypeError):
            unavailable = True
        valid = catalog if scope == "projects" else {c["id"]: c for c in chats} if scope == "chats" else {i["id"]: i for i in items}
        ids = self.validate_ids(data.get("ids"), valid)
        linked = lambda i: i.get("coordination", {}).get("thread", {}).get("id") or i.get("intake_source", {}).get("thread_id")
        selected = [i for i in items if (i["project_id"] in ids if scope == "projects" else
                                        linked(i) in ids if scope == "chats" else i["id"] in ids)]
        chat_ids = ({c["id"] for c in chats if c["project_id"] in ids} if scope == "projects" else
                    ids if scope == "chats" else {linked(i) for i in selected if linked(i)})
        names = [valid[i].get("name") or valid[i].get("title") for i in sorted(ids)]
        clean = lambda s: excerpt(str(s)).replace("\n", " ").replace("`", "'").replace("#", "")
        lines = [f"# {kind}", f"日期：{data['start']} 至 {data['end']}（Asia/Shanghai）",
                 "范围：" + "、".join(clean(n) for n in names), f"生成时间：{stamp()}",
                 "", "## 期间进展（账本记录，不代表独立验证或生产已部署）"]
        count = 0

        def within(value):
            try:
                dt = datetime.fromisoformat(value)
                return dt.tzinfo is not None and start <= dt.timestamp() * 1000 < end
            except (ValueError, TypeError):
                return False

        for item in selected:
            events = [e for e in item["events"] if within(e.get("at"))]
            evidence = [e for e in item["evidence"] if within(e.get("at"))]
            if events or evidence:
                lines.append(f"### {clean(item.get('display_title') or item['title'])} [{item['id']}]")
                for event in events:
                    lines.append(f"- {clean(event['at'])}：{clean(event['message'])}（记录者：{clean(event['actor'])}）")
                    count += 1
                for e in evidence:
                    lines.append(f"- 测试记录：{clean(e['label'])}；声明结果 {clean(e['result'])}；候选 {clean(e['candidate'])}")
                    count += 1
        if not count:
            lines.append("- 期间没有匹配的账本进展记录，不能据此判定无工作或已完成。")
        lines.extend(["", "## 当前状态快照（生成时，不是期末状态）"])
        for item in selected:
            lines.append(f"- {clean(item.get('display_title') or item['title'])} [{item['id']}]：{clean(item['phase'])}节点 / {clean(item['status'])}")
            analysis = item.get("blocker_analysis")
            if analysis:
                lines.append(f"  阻塞：{clean(analysis['summary'])}；建议：{clean(analysis['approval']['next_step'])}")
            elif item.get("blocker"):
                lines.append("  阻塞：" + clean(item["blocker"]))
            c = item.get("coordination", {})
            if c.get("scope"):
                lines.append(f"  下一步：{clean(c.get('next_step', ''))}；决策：{clean(c.get('decision_state', ''))}；版本失效：{'是' if c.get('stale') else '否'}")
        if not selected:
            lines.append("- 所选范围尚无关联需求，不能从会话标题推断交付状态。")
        lines.extend(["", "## 期间聊天输入线索（不是完成证据）"])
        entries = []
        with self.store.connect() as db:
            for table in ["management_candidates", "captured_inputs"]:
                if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                    continue
                entries.extend(json.loads(r[0]) for r in db.execute(f"SELECT data FROM {table}"))
        eligible = {c["id"] for c in chats if c["project_id"] not in self.excluded()}
        seen = set()
        matched = 0
        for entry in sorted(entries, key=lambda e: e["created_at_ms"]):
            key = (entry["thread_id"], entry["created_at_ms"], entry["excerpt"])
            if (entry["thread_id"] in chat_ids & eligible and start <= entry["created_at_ms"] < end and key not in seen):
                seen.add(key)
                matched += 1
                if matched <= 200:
                    lines.append(f"- {clean(entry['excerpt'])}（会话 {entry['thread_id']}；输入线索）")
        if not matched:
            lines.append("- 没有已采集的期间输入；报告不会自动回读聊天正文，请先按范围历史采集。")
        if matched > 200:
            lines.append(f"- 另有 {matched - 200} 条输入未展开，请缩小时间范围。")
        if unavailable:
            lines.append("- 会话索引不可用，聊天覆盖不完整。")
        lines.extend(["", "## 边界", "- 项目加白后不引用其聊天摘要；已有需求账本仍可汇总。",
                      "- 仅使用已记录事件、测试声明和当前状态；未读取工具、推理或附件，未调用外部模型。"])
        result = {"id": uuid.uuid4().hex, "kind": kind, "scope": scope, "ids": sorted(ids),
                  "start": data["start"], "end": data["end"], "created_at": stamp(),
                  "markdown": "\n".join(lines), "events": count}
        with self.store.lock, self.store.connect() as db:
            self.save(db, "management_reports", result["id"], result)
        return result

    def close(self):
        self.stopping.set()
        for worker in list(self.workers.values()):
            worker.join(timeout=6)
