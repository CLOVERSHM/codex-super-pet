"""Project catalog and reviewed semantic changes. No host or production execution."""
import copy
import hashlib
import json
import re
import uuid
from skill_registry import discover


DEFAULT_NODES = [
    {"name": name, "human": name in ["观察", "完成"],
     "evidence": name in ["待发布", "观察", "完成"],
     "approval": name in ["观察", "完成"]}
    for name in ["需求", "方案", "开发", "测试", "待发布", "观察", "完成"]
]


def template(value):
    if not isinstance(value, list) or not 2 <= len(value) <= 20:
        raise ValueError("流程须包含 2 至 20 个节点")
    nodes = []
    for node in value:
        if not isinstance(node, dict):
            raise ValueError("无效流程节点")
        name = node.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 40:
            raise ValueError("节点名称须为 1 至 40 字")
        name = name.strip()
        if any(name == n["name"] for n in nodes):
            raise ValueError("节点名称不可重复")
        if any(type(node.get(k, False)) is not bool for k in ["human", "evidence", "approval"]):
            raise ValueError("门禁必须为布尔值")
        base = next((n for n in DEFAULT_NODES if n["name"] == name), {})
        approval = node.get("approval", False) or base.get("approval", False)
        nodes.append({"name": name, "human": node.get("human", False) or approval or base.get("human", False),
                      "evidence": node.get("evidence", False) or approval or base.get("evidence", False),
                      "approval": approval})
    if nodes[0]["name"] != "需求" or nodes[-1]["name"] != "完成":
        raise ValueError("流程须以需求开始、完成结束")
    return nodes


class ProjectPlatform:
    def init_platform(self):
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS catalog (id TEXT PRIMARY KEY,data TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS intake (id TEXT PRIMARY KEY,data TEXT NOT NULL)")
            if self.project_source is None:
                self.ensure_project(db, "未分类")
                for raw, in db.execute("SELECT data FROM items").fetchall():
                    self.ensure_project(db, json.loads(raw).get("project", "未分类"))

    def ensure_project(self, db, name):
        for raw, in db.execute("SELECT data FROM catalog"):
            entry = json.loads(raw)
            if entry["kind"] == "project" and entry["name"] == name:
                return entry
        entry = {"id": uuid.uuid4().hex[:12], "kind": "project", "name": name,
                 "collection_id": "", "revision": 1, "template": copy.deepcopy(DEFAULT_NODES)}
        self.save_catalog(db, entry)
        return entry

    def save_catalog(self, db, entry):
        db.execute("INSERT OR REPLACE INTO catalog VALUES (?,?)",
                   (entry["id"], json.dumps(entry, ensure_ascii=False)))

    def catalog(self):
        with self.lock, self.connect() as db:
            if self.project_source is not None:
                entries = [json.loads(r[0]) for r in db.execute("SELECT data FROM catalog")]
                projects = []
                for project in self.project_source.snapshot()["projects"]:
                    entry = next((e for e in entries if e["id"] == project["id"]), None)
                    if not entry:
                        entry = {"id": project["id"], "kind": "project", "revision": 1,
                                 "collection_id": "", "template": copy.deepcopy(DEFAULT_NODES)}
                    entry.update(project, source="codex")
                    self.save_catalog(db, entry)
                    projects.append(entry)
                return {"projects": projects, "collections": [e for e in entries if e["kind"] == "collection"],
                        "source": "codex", "readonly_projects": True}
            for raw, in db.execute("SELECT data FROM items").fetchall():
                self.ensure_project(db, json.loads(raw).get("project", "未分类"))
            entries = [json.loads(r[0]) for r in db.execute("SELECT data FROM catalog")]
        return {"collections": sorted([e for e in entries if e["kind"] == "collection"], key=lambda e: e["name"]),
                "projects": sorted([e for e in entries if e["kind"] == "project"], key=lambda e: e["name"])}

    def project_for(self, db, data):
        ident = data.get("project_id")
        if self.project_source is not None:
            if not ident:
                raise ValueError("请从 Codex 项目列表选择项目")
            snapshot = self.project_source.snapshot()
            project = next((p for p in snapshot["projects"] if p["id"] == ident), None)
            if not project:
                raise ValueError("该项目已不在 Codex 项目列表中")
            row = db.execute("SELECT data FROM catalog WHERE id=?", (ident,)).fetchone()
            entry = json.loads(row[0]) if row else {
                "id": ident, "kind": "project", "revision": 1, "collection_id": "",
                "template": copy.deepcopy(DEFAULT_NODES)}
            entry.update(project, source="codex")
            self.save_catalog(db, entry)
            return entry
        if ident:
            row = db.execute("SELECT data FROM catalog WHERE id=?", (ident,)).fetchone()
            if not row or json.loads(row[0])["kind"] != "project":
                raise ValueError("项目不存在")
            return json.loads(row[0])
        name = data.get("project", "未分类")
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise ValueError("无效项目名称")
        return self.ensure_project(db, name.strip())

    def manage_catalog(self, data, actor):
        if actor != "human":
            raise ValueError("项目和流程配置须由人确认")
        with self.lock, self.connect() as db:
            ident = data.get("id")
            if ident:
                row = db.execute("SELECT data FROM catalog WHERE id=?", (ident,)).fetchone()
                if not row:
                    raise ValueError("项目配置不存在")
                entry = json.loads(row[0])
                if data.get("revision") != entry["revision"]:
                    raise ValueError("项目配置已变化，请刷新")
            else:
                if data.get("kind") not in ["collection", "project"]:
                    raise ValueError("无效配置类型")
                entry = {"id": uuid.uuid4().hex[:12], "kind": data["kind"], "revision": 0}
            if self.project_source is not None and entry["kind"] == "project":
                current = next((p for p in self.project_source.snapshot()["projects"] if p["id"] == ident), None)
                if not current:
                    raise ValueError("项目由 Codex 管理，请在 Codex 中新建或修改")
                entry["name"] = current["name"]
            name = data.get("name", entry.get("name", ""))
            if not isinstance(name, str) or not name.strip() or len(name) > 80:
                raise ValueError("请输入名称（最多80字）")
            # Project names are stable source keys, including read-only imports.
            if ident and entry["kind"] == "project" and name.strip() != entry["name"]:
                raise ValueError("项目名称为来源标识，不可重命名；可调整项目集和流程")
            for raw, in db.execute("SELECT data FROM catalog"):
                other = json.loads(raw)
                if (not (self.project_source is not None and entry["kind"] == "project") and
                        other["id"] != entry["id"] and other["kind"] == entry["kind"] and other["name"] == name.strip()):
                    raise ValueError("名称已存在")
            entry["name"] = name.strip()
            if entry["kind"] == "project":
                if "session_skills" in data:
                    chosen = data["session_skills"]
                    allowed = {s["id"] for s in discover() if s["project_id"] == entry["id"]}
                    if (not isinstance(chosen, list) or len(chosen) > 20 or
                            any(not isinstance(s, str) or s not in allowed for s in chosen) or
                            len(set(chosen)) != len(chosen)):
                        raise ValueError("请仅选择为此项目生成的专属 skill")
                    entry["session_skills"] = chosen
                if "capture_excluded" in data:
                    if type(data["capture_excluded"]) is not bool:
                        raise ValueError("采集加白设置须为布尔值")
                    entry["capture_excluded"] = data["capture_excluded"]
                group = data.get("collection_id", entry.get("collection_id", ""))
                if group:
                    row = db.execute("SELECT data FROM catalog WHERE id=?", (group,)).fetchone()
                    if not row or json.loads(row[0])["kind"] != "collection":
                        raise ValueError("项目集不存在")
                entry["collection_id"] = group
                entry["template"] = template(data.get("template", entry.get("template", DEFAULT_NODES)))
            entry["revision"] += 1
            self.save_catalog(db, entry)
            return entry

    def intake_listing(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT data FROM intake ORDER BY rowid DESC")]

    def propose_change(self, data, actor):
        """Accept an explicit model classification, never guess from keywords."""
        event_id, thread_id = data.get("event_id"), data.get("thread_id")
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 160:
            raise ValueError("需要稳定输入事件编号")
        if not isinstance(thread_id, str) or not re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", thread_id):
            raise ValueError("需要准确会话编号")
        intent = data.get("intent")
        if intent not in ["ignore", "create", "update", "archive", "clarify"]:
            raise ValueError("无效语义分类")
        reason = data.get("reason")
        if not isinstance(reason, str) or not 1 <= len(reason) <= 1000:
            raise ValueError("需要简短分类依据，不保存原始聊天")
        with self.lock, self.connect() as db:
            if not data.get("project_id"):
                raise ValueError("需要准确项目编号")
            project = self.project_for(db, {"project_id": data.get("project_id")})
            if project.get("capture_excluded"):
                raise ValueError("项目已加白，停止采集需求变更")
            ident = hashlib.sha256((thread_id + "\0" + event_id).encode()).hexdigest()[:24]
            normalized = {"thread_id": thread_id, "event_id": event_id, "project_id": project["id"],
                          "intent": intent, "reason": reason, "item_id": data.get("item_id", ""),
                          "version": data.get("version"), "patch": data.get("patch", {})}
            if not isinstance(normalized["patch"], dict) or set(normalized["patch"]) - {"title", "description", "acceptance", "kind"}:
                raise ValueError("变更只能涉及需求文本，不可包含审批、进度或执行命令")
            if intent in ["update", "archive"]:
                item = self.get(normalized["item_id"])
                if item["origin"] != "local" or item["project_id"] != project["id"] or item["version"] != normalized["version"]:
                    raise ValueError("需求来源、项目或版本不匹配")
                if item.get("coordination", {}).get("thread", {}).get("id") != thread_id:
                    raise ValueError("变更会话不是该需求的已确认关联会话")
            row = db.execute("SELECT data FROM intake WHERE id=?", (ident,)).fetchone()
            if row:
                old = json.loads(row[0])
                if old["input"] != normalized:
                    raise ValueError("事件编号重复但分类内容不同")
                return old
            entry = {"id": ident, "input": normalized, "state": "忽略" if intent == "ignore" else "待澄清" if intent == "clarify" else "待确认"}
            db.execute("INSERT INTO intake VALUES (?,?)", (ident, json.dumps(entry, ensure_ascii=False)))
            return entry

    def review_change(self, ident, data, actor):
        if actor != "human":
            raise ValueError("需求变更须由人在控制台确认")
        with self.lock, self.connect() as db:
            row = db.execute("SELECT data FROM intake WHERE id=?", (ident,)).fetchone()
            if not row:
                raise ValueError("变更不存在")
            entry = json.loads(row[0])
            if entry["state"] not in ["待确认", "待澄清"]:
                raise ValueError("变更已经处理")
            if data.get("decision") == "reject":
                entry["state"] = "已拒绝"
            elif data.get("decision") == "accept" and entry["state"] == "待确认":
                payload = entry["input"]
                if self.project_for(db, {"project_id": payload["project_id"]}).get("capture_excluded"):
                    raise ValueError("项目已加白，停止录入采集变更")
                if payload["intent"] == "create":
                    item = self.create({**payload["patch"], "project_id": payload["project_id"]}, actor, db=db)
                    item["intake_source"] = {"thread_id": payload["thread_id"], "event_id": payload["event_id"],
                                             "change_id": entry["id"]}
                    self.save(db, item)
                    entry["created_item_id"] = item["id"]
                else:
                    item = self.get(payload["item_id"])
                    project = self.project_for(db, {"project_id": payload["project_id"]})
                    if (item["project_id"] != project["id"] or
                            item.get("coordination", {}).get("thread", {}).get("id") != payload["thread_id"]):
                        raise ValueError("需求归属或会话已变化，请重新分类")
                    self.mutate(item["id"], {"action": "archive" if payload["intent"] == "archive" else "update",
                                           "version": payload["version"], **payload["patch"]}, actor, db=db)
                entry["state"] = "已采纳"
            else:
                raise ValueError("无效处理；待澄清内容不能直接采纳")
            db.execute("UPDATE intake SET data=? WHERE id=?", (json.dumps(entry, ensure_ascii=False), ident))
            return entry
