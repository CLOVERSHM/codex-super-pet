import json
import tempfile
import unittest
from pathlib import Path

from codex_projects import DesktopProjects
from console import Store


class DesktopProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "state.json"
        self.state = {"local-projects": {
            "p1": {"id": "p1", "name": "同名", "rootPaths": ["/synthetic/one"]},
            "p2": {"id": "p2", "name": "同名", "rootPaths": ["/synthetic/two"]}},
            "project-order": ["p2", "p1"], "thread-project-assignments": {}}
        self.write()
        self.source = DesktopProjects(self.path)
        self.store = Store(self.root / "db", projects=self.source)

    def write(self):
        self.path.write_text(json.dumps(self.state))

    def tearDown(self):
        self.temp.cleanup()

    def test_exact_order_ids_and_no_synthetic_defaults(self):
        projects = self.store.catalog()["projects"]
        self.assertEqual([p["id"] for p in projects], ["p2", "p1"])
        self.assertEqual([p["name"] for p in projects], ["同名", "同名"])
        self.assertEqual(projects[0]["path"], "/synthetic/two")
        self.assertTrue(self.store.catalog()["readonly_projects"])

    def test_follow_add_rename_remove_without_deleting_records(self):
        item = self.store.create({"title": "需求", "project_id": "p1"})
        self.state["local-projects"]["p1"]["name"] = "改名"
        self.write()
        self.assertEqual(self.store.get(item["id"])["project_label"], "改名")
        del self.state["local-projects"]["p1"]
        self.state["local-projects"]["p3"] = {"id": "p3", "name": "新增", "rootPaths": []}
        self.state["project-order"] = ["p3", "p2"]
        self.write()
        self.assertEqual([p["id"] for p in self.store.catalog()["projects"]], ["p3", "p2"])
        self.assertEqual(self.store.get(item["id"])["project_id"], "")
        self.assertEqual(len(self.store.listing()), 1)
        with self.assertRaises(ValueError):
            self.store.create({"title": "不应创建", "project_id": "p1"})

    def test_no_project_creation_or_name_based_creation(self):
        self.store.catalog()
        for data in [{"kind": "project", "name": "自建"},
                     {"id": "p1", "revision": 1, "name": "自改"}]:
            with self.assertRaises(ValueError):
                self.store.manage_catalog(data, "human")
        with self.assertRaises(ValueError):
            self.store.create({"title": "需求", "project": "同名"})

    def test_collection_and_template_survive_desktop_rename(self):
        self.store.catalog()
        group = self.store.manage_catalog({"kind": "collection", "name": "工作"}, "human")
        self.store.manage_catalog({"id": "p1", "revision": 1, "collection_id": group["id"]}, "human")
        self.state["local-projects"]["p1"]["name"] = "新名称"
        self.write()
        entry = next(p for p in self.store.catalog()["projects"] if p["id"] == "p1")
        self.assertEqual(entry["collection_id"], group["id"])
        self.assertEqual(entry["revision"], 2)

    def test_binding_uses_explicit_membership_not_path_or_name(self):
        item = self.store.create({"title": "来源", "project_id": "p1"})
        with self.store.connect() as db:
            db.execute("INSERT INTO coordination VALUES (?,?)",
                       (item["id"], json.dumps({"revision": 1, "jobs": [], "thread": {"id": "t1"}})))
        self.state["thread-project-assignments"]["t1"] = {"projectKind": "local", "projectId": "p2"}
        self.write()
        self.assertEqual(self.store.get(item["id"])["project_id"], "p2")

    def test_unavailable_or_unsupported_does_not_fallback(self):
        for raw in ["{", "{}", json.dumps({**self.state, "remote-projects": {"remote": {}}})]:
            self.path.write_text(raw)
            with self.assertRaises(ValueError):
                self.store.catalog()
        self.path.unlink()
        with self.assertRaises(ValueError):
            self.source.snapshot()

    def test_source_readonly_and_private_fields_not_exposed(self):
        self.state["private"] = "not-project-metadata"
        self.write()
        before = self.path.read_bytes()
        result = self.store.catalog()
        self.assertNotIn("not-project-metadata", json.dumps(result))
        self.assertEqual(self.path.read_bytes(), before)
