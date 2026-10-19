import copy
import json
import tempfile
import unittest
import threading
import urllib.request
import urllib.error
from pathlib import Path

from console import Store, make_server
from project_platform import DEFAULT_NODES, template

THREAD = "00000000-0000-4000-8000-000000000001"


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "db")
        self.project = self.store.manage_catalog({"kind": "project", "name": "演示"}, "human")

    def tearDown(self):
        self.temp.cleanup()

    def proposal(self, **extra):
        return self.store.propose_change({"project_id": self.project["id"], "thread_id": THREAD,
                                         "event_id": "input-1", "intent": "create",
                                         "reason": "合成需求变更", "patch": {"title": "增加导出"}, **extra}, "agent")

    def test_generic_default_and_migration(self):
        item = self.store.create({"title": "普通需求"})
        self.assertEqual(item["project"], "未分类")
        with self.store.connect() as db:
            item.pop("workflow")
            item["project"] = "旧项目"
            self.store.save(db, item)
        migrated = Store(self.store.path)
        self.assertIn("旧项目", [p["name"] for p in migrated.catalog()["projects"]])
        self.assertEqual(migrated.get(item["id"])["workflow"], DEFAULT_NODES)

    def test_move_collection_revision(self):
        group = self.store.manage_catalog({"kind": "collection", "name": "工作"}, "human")
        moved = self.store.manage_catalog({"id": self.project["id"], "revision": 1, "collection_id": group["id"]}, "human")
        self.assertEqual(moved["collection_id"], group["id"])
        with self.assertRaises(ValueError):
            self.store.manage_catalog({"id": self.project["id"], "revision": 1}, "human")

    def test_agent_cannot_configure(self):
        with self.assertRaises(ValueError):
            self.store.manage_catalog({"kind": "project", "name": "其他"}, "agent")

    def test_template_snapshot_and_custom_gate(self):
        old = self.store.create({"title": "旧需求", "project_id": self.project["id"]})
        nodes = copy.deepcopy(DEFAULT_NODES)
        nodes.insert(-1, {"name": "生产环境验证", "human": True, "evidence": True, "approval": True})
        self.store.manage_catalog({"id": self.project["id"], "revision": 1, "template": nodes}, "human")
        new = self.store.create({"title": "新需求", "project_id": self.project["id"]})
        self.assertNotIn("生产环境验证", [n["name"] for n in self.store.get(old["id"])["workflow"]])
        for actor in ["human", "agent"]:
            with self.assertRaises(ValueError):
                self.store.mutate(new["id"], {"action": "update", "version": 1, "phase": "生产环境验证"}, actor)

    def test_template_invalid_and_reserved_gates(self):
        for nodes in [[], [{"name": "需求"}, {"name": "需求"}, {"name": "完成"}],
                      [{"name": "需求"}, {"name": "结束"}]]:
            with self.assertRaises(ValueError):
                template(nodes)
        self.assertTrue(template([{"name": "需求"}, {"name": "完成"}])[-1]["approval"])

    def test_ignore_is_not_requirement_and_replay(self):
        p = self.proposal(intent="ignore", reason="天气闲聊", patch={})
        self.assertEqual(p["state"], "忽略")
        self.assertEqual(p, self.proposal(intent="ignore", reason="天气闲聊", patch={}))
        self.assertEqual(self.store.listing(), [])
        with self.assertRaises(ValueError):
            self.proposal()

    def test_review_create_once(self):
        p = self.proposal()
        self.assertEqual(self.store.listing(), [])
        with self.assertRaises(ValueError):
            self.store.review_change(p["id"], {"decision": "accept"}, "agent")
        self.store.review_change(p["id"], {"decision": "accept"}, "human")
        self.assertEqual(len(self.store.listing()), 1)
        with self.assertRaises(ValueError):
            self.store.review_change(p["id"], {"decision": "accept"}, "human")

    def test_invalid_create_rolls_back_review(self):
        p = self.proposal(patch={})
        with self.assertRaises(ValueError):
            self.store.review_change(p["id"], {"decision": "accept"}, "human")
        self.assertEqual(self.store.intake_listing()[0]["state"], "待确认")
        self.assertEqual(self.store.listing(), [])

    def bound(self):
        item = self.store.create({"title": "已有", "project_id": self.project["id"]})
        with self.store.connect() as db:
            db.execute("INSERT INTO coordination VALUES (?,?)", (item["id"], json.dumps(
                {"revision": 1, "thread": {"id": THREAD}, "jobs": []})))
        return item

    def test_update_requires_exact_binding_and_version(self):
        item = self.bound()
        p = self.proposal(intent="update", item_id=item["id"], version=1)
        self.store.mutate(item["id"], {"action": "update", "version": 1, "title": "变更"}, "human")
        with self.assertRaises(ValueError):
            self.store.review_change(p["id"], {"decision": "accept"}, "human")
        with self.assertRaises(ValueError):
            self.proposal(intent="update", event_id="next", item_id=item["id"], version=2,
                          thread_id="00000000-0000-4000-8000-000000000002")

    def test_archive_is_reversible(self):
        item = self.bound()
        p = self.proposal(intent="archive", item_id=item["id"], version=1, patch={})
        self.store.review_change(p["id"], {"decision": "accept"}, "human")
        self.assertTrue(self.store.get(item["id"])["archived"])
        self.store.mutate(item["id"], {"action": "restore", "version": 2}, "human")
        self.assertFalse(self.store.get(item["id"])["archived"])

    def test_no_execution_payload_or_missing_project(self):
        for patch in [{"phase": "完成"}, {"command": "deploy"}]:
            with self.assertRaises(ValueError):
                self.proposal(patch=patch)
        with self.assertRaises(ValueError):
            self.proposal(project_id="")

    def test_clarify_requires_rejection_or_new_event(self):
        p = self.proposal(intent="clarify")
        with self.assertRaises(ValueError):
            self.store.review_change(p["id"], {"decision": "accept"}, "human")
        self.store.review_change(p["id"], {"decision": "reject"}, "human")

    def test_http_role_guard_and_intake_review(self):
        server, agent = make_server(self.store, 0)
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        url = f"http://127.0.0.1:{server.server_port}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def request(path, token, data=None):
            with opener.open(urllib.request.Request(url + path,
                data=None if data is None else json.dumps(data).encode(),
                headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"}), timeout=5) as r:
                return json.load(r)
        try:
            with opener.open(url + "/api/session") as r:
                human = json.load(r)["token"]
            self.assertEqual(len(request("/api/catalog", agent)["projects"]), 2)
            with self.assertRaises(urllib.error.HTTPError):
                request("/api/catalog", agent, {"kind": "collection", "name": "禁止"})
            entry = request("/api/intake", agent, {"project_id": self.project["id"], "thread_id": THREAD,
                "event_id": "http", "intent": "create", "reason": "合成新增", "patch": {"title": "HTTP需求"}})
            with self.assertRaises(urllib.error.HTTPError):
                request("/api/intake/" + entry["id"], agent, {"decision": "accept"})
            request("/api/intake/" + entry["id"], human, {"decision": "accept"})
            self.assertEqual(len(request("/api/items", agent)), 1)
        finally:
            server.shutdown()
            worker.join()
            server.server_close()
