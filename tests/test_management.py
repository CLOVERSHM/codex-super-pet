import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import closing

from console import Store, make_server
from codex_projects import DesktopProjects
from chat_capture import ChatCapture
from management import Management, period

THREAD = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / "projects.json"
        self.state.write_text(json.dumps({
            "local-projects": {i: {"id": i, "name": i, "rootPaths": []} for i in ["p", "q"]},
            "project-order": ["p", "q"], "thread-project-assignments": {
                THREAD: {"projectKind": "local", "projectId": "p"},
                OTHER: {"projectKind": "local", "projectId": "q"}}}))
        self.store = Store(self.root / "ledger.sqlite", projects=DesktopProjects(self.state))
        self.manager = self.store.management = Management(self.store, self.root)
        with closing(sqlite3.connect(self.root / "state_5.sqlite")) as db, db:
            db.execute("CREATE TABLE threads(id,title,name,project_id,archived,updated_at_ms)")
            db.executemany("INSERT INTO threads VALUES (?,?,NULL,NULL,0,1)", [(THREAD, "合成任务"), (OTHER, "其他")])
        with closing(sqlite3.connect(self.root / "thread_history_1.sqlite")) as db, db:
            db.execute("CREATE TABLE thread_items(thread_id,turn_id,item_id,created_at_ms,item_type,item_json,updated_at_ordinal)")
        self.window = {"start": "2026-10-17", "end": "2026-10-17", "project_ids": ["p"]}
        self.start, self.end = period(self.window)

    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()

    def message(self, ident, text, at=None, thread=THREAD, kind="userMessage"):
        with closing(sqlite3.connect(self.root / "thread_history_1.sqlite")) as db, db:
            db.execute("INSERT INTO thread_items VALUES (?,'turn',?,?,?,?,1)",
                       (thread, ident, at if at is not None else self.start, kind,
                        json.dumps({"content": [{"type": "text", "text": text}]})))

    def run_history(self):
        job = self.manager.history(self.window, "human")
        self.manager.workers[job["id"]].join(timeout=10)
        self.assertFalse(self.manager.workers[job["id"]].is_alive())
        return self.manager.snapshot()

    def exclude(self, enabled=True, project="p"):
        p = next(p for p in self.store.catalog()["projects"] if p["id"] == project)
        self.store.manage_catalog({"id": p["id"], "revision": p["revision"], "capture_excluded": enabled}, "human")

    def test_bounds_projects_tools_context_redaction_and_duplicates(self):
        self.message("valid", "增加报表 token=private-value")
        self.message("weather", "今天天气如何")
        self.message("before", "修复旧问题", self.start - 1)
        self.message("after", "修复明天问题", self.end)
        self.message("other", "修复其他项目", thread=OTHER)
        self.message("tool", "不读工具 secret=tool", kind="commandExecution")
        self.message("context", "# AGENTS.md context")
        result = self.run_history()
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(result["jobs"][0]["state"], "待审阅")
        self.assertNotIn("private-value", json.dumps(result))
        self.assertIn("闲聊线索", {c["triage"] for c in result["candidates"]})
        self.assertEqual(self.store.listing(), [])
        self.assertEqual(self.run_history()["jobs"][0]["added"], 0)

    def test_exclusion_prevents_plaintext_fetch_and_no_live_backfill(self):
        self.exclude()
        with patch("management.readonly", side_effect=AssertionError("no reads")):
            with self.assertRaises(ValueError):
                self.manager.history(self.window, "human")
        capture = ChatCapture(self.store, self.root)
        capture.poll()
        self.message("secret", "增加秘密功能")
        capture.poll()
        self.assertEqual(capture.snapshot()["inputs"], [])
        self.exclude(False)
        capture.poll()
        self.assertEqual(capture.snapshot()["inputs"], [])
        self.message("next", "增加新功能")
        capture.poll()
        self.assertEqual(len(capture.snapshot()["inputs"]), 1)

    def test_exclusion_mid_job_skips_read_and_registration(self):
        self.message("valid", "增加报表")
        original = self.manager.chat_index
        calls = [0]

        def change():
            calls[0] += 1
            if calls[0] == 2:
                self.exclude()
            return original()

        with patch.object(self.manager, "chat_index", side_effect=change):
            result = self.run_history()
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["jobs"][0]["skipped"], 1)

    def test_review_create_idempotent_no_approval_and_exclusion(self):
        self.message("valid", "增加报表")
        entry = self.run_history()["candidates"][0]
        self.exclude()
        with self.assertRaises(ValueError):
            self.manager.review(entry["id"], {"decision": "create", "title": "报表"}, "human")
        self.assertEqual(self.manager.snapshot()["candidates"][0]["excerpt"], "")
        self.exclude(False)
        payload = {"decision": "create", "title": "报表导出", "description": "人工确认功能"}
        result = self.manager.review(entry["id"], payload, "human")
        self.assertEqual(self.manager.review(entry["id"], payload, "human"), result)
        self.assertEqual(len(self.store.listing()), 1)
        item = self.store.listing()[0]
        self.assertEqual(item["phase"], "需求")
        self.assertEqual(item["decisions"], [])
        self.assertEqual(item["intake_source"]["thread_id"], THREAD)

    def test_review_changes_require_confirmed_binding_and_exact_version(self):
        item = self.store.create({"title": "旧标题", "project_id": "p"}, "human")
        self.message("change", "修改需求")
        entry = self.run_history()["candidates"][0]
        data = {"decision": "update", "title": "新标题", "item_id": item["id"], "version": item["version"]}
        with self.assertRaises(ValueError):
            self.manager.review(entry["id"], data, "human")
        with self.store.connect() as db:
            db.execute("INSERT INTO coordination VALUES (?,?)",
                       (item["id"], json.dumps({"revision": 1, "jobs": [], "thread": {"id": THREAD}})))
        with self.assertRaises(ValueError):
            self.manager.review(entry["id"], {**data, "version": 999}, "human")
        self.manager.review(entry["id"], data, "human")
        self.assertEqual(self.store.get(item["id"])["title"], "新标题")

    def test_assignment_change_blocks_review(self):
        self.message("valid", "增加报表")
        entry = self.run_history()["candidates"][0]
        state = json.loads(self.state.read_text())
        state["thread-project-assignments"][THREAD] = {"projectKind": "local", "projectId": "q"}
        self.state.write_text(json.dumps(state))
        with self.assertRaises(ValueError):
            self.manager.review(entry["id"], {"decision": "create", "title": "报表"}, "human")

    def test_daily_weekly_report_range_and_current_snapshot(self):
        item = self.store.create({"title": "合成日报", "project_id": "p"}, "human")
        with self.store.connect() as db:
            item["events"] = [{"at": "2026-10-17T01:00:00+08:00", "message": "方案已记录", "actor": "human"},
                              {"at": "2026-10-16T23:59:59+08:00", "message": "范围外", "actor": "human"}]
            self.store.save(db, item)
        self.message("valid", "计划新增导出")
        self.run_history()
        data = {**self.window, "kind": "日报", "scope": "projects", "ids": ["p"]}
        report = self.manager.report(data, "human")
        self.assertEqual(report["events"], 1)
        self.assertIn("方案已记录", report["markdown"])
        self.assertNotIn("范围外", report["markdown"])
        self.assertIn("不是期末状态", report["markdown"])
        self.assertIn("计划新增导出", report["markdown"])
        self.exclude()
        report = self.manager.report({**data, "kind": "周报"}, "human")
        self.assertNotIn("计划新增导出", report["markdown"])
        self.assertIn("合成日报", report["markdown"])
        self.assertEqual(len(self.manager.snapshot()["reports"]), 2)

    def test_report_chat_requirement_and_missing_source(self):
        item = self.store.create({"title": "关联需求", "project_id": "p"}, "human")
        with self.store.connect() as db:
            item["intake_source"] = {"thread_id": THREAD}
            self.store.save(db, item)
        for scope, ident in [("chats", THREAD), ("requirements", item["id"])]:
            result = self.manager.report({**self.window, "kind": "周报", "scope": scope, "ids": [ident]}, "human")
            self.assertIn("关联需求", result["markdown"])
        (self.root / "state_5.sqlite").unlink()
        result = self.manager.report({**self.window, "kind": "日报", "scope": "requirements", "ids": [item["id"]]}, "human")
        self.assertIn("覆盖不完整", result["markdown"])

    def test_human_only_and_invalid_selection_dates(self):
        with self.assertRaises(ValueError):
            self.manager.history(self.window, "agent")
        for data in [{**self.window, "end": "2026-10-16"}, {**self.window, "project_ids": []},
                     {**self.window, "project_ids": ["missing"]}, {**self.window, "start": "bad"}]:
            with self.assertRaises(ValueError):
                self.manager.history(data, "human")
        with self.assertRaises(ValueError):
            self.exclude("true")

    def test_cancel_restart_and_missing_index(self):
        with patch("management.threading.Thread") as worker:
            job = self.manager.history(self.window, "human")
            worker.return_value.start.assert_called_once()
        self.manager.cancel(job["id"], "human")
        self.manager.scan(job["id"], self.start, self.end)
        self.assertEqual(self.manager.snapshot()["jobs"][0]["state"], "已取消")
        with self.store.connect() as db:
            job["state"] = "采集中"
            self.manager.save(db, "management_jobs", job["id"], job)
        restarted = Management(self.store, self.root)
        self.assertEqual(restarted.snapshot()["jobs"][0]["state"], "已中断")
        (self.root / "state_5.sqlite").unlink()
        self.assertEqual(self.run_history()["jobs"][0]["state"], "失败")

    def test_http_human_role_management_routes(self):
        import threading
        import urllib.request
        import urllib.error
        server, agent = make_server(self.store, 0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(url + "/api/session") as response:
                human = json.load(response)["token"]
            for token, expected in [(agent, 400), (human, 200)]:
                request = urllib.request.Request(url + "/api/management/history",
                                                 data=json.dumps(self.window).encode(),
                                                 headers={"Authorization": "Bearer " + token})
                try:
                    with urllib.request.urlopen(request) as response:
                        self.assertEqual(response.status, expected)
                except urllib.error.HTTPError as exc:
                    self.assertEqual(exc.code, expected)
                    exc.close()
            for path in ["/api/management", "/api/management/chats", "/management.js"]:
                request = urllib.request.Request(url + path, headers={"Authorization": "Bearer " + human})
                with urllib.request.urlopen(request) as response:
                    self.assertEqual(response.status, 200)
        finally:
            self.manager.close()
            server.shutdown()
            server.server_close()
            thread.join()
