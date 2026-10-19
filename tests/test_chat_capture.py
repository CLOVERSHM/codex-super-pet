import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing

from chat_capture import ChatCapture
from console import Store
from codex_projects import DesktopProjects
from blocker_analysis import explain
from skill_registry import ROOT as SKILL_ROOT


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        state = self.root / "projects.json"
        state.write_text(json.dumps({"local-projects": {"p": {"id": "p", "name": "合成", "rootPaths": []}},
                                     "project-order": ["p"], "thread-project-assignments": {}}))
        self.store = Store(self.root / "ledger.sqlite", projects=DesktopProjects(state))
        with closing(sqlite3.connect(self.root / "state_5.sqlite")) as db, db:
            db.execute("CREATE TABLE threads(id,title,name,project_id,archived,updated_at_ms)")
            db.execute("INSERT INTO threads VALUES ('t','合成会话',NULL,'p',0,1)")
        with closing(sqlite3.connect(self.root / "thread_history_1.sqlite")) as db, db:
            db.execute("CREATE TABLE thread_items(thread_id,turn_id,item_id,created_at_ms,item_type,item_json,updated_at_ordinal)")
        self.capture = ChatCapture(self.store, self.root)

    def tearDown(self):
        self.capture.close()
        self.temp.cleanup()

    def message(self, ident, value, kind="userMessage"):
        with closing(sqlite3.connect(self.root / "thread_history_1.sqlite")) as db, db:
            db.execute("INSERT INTO thread_items VALUES ('t','turn',?,1,?,?,1)",
                       (ident, kind, json.dumps({"content": [{"type": "text", "text": value}]})))

    def test_baseline_skips_history_then_captures_once(self):
        self.message("old", "历史正文不能回填")
        self.capture.poll()
        self.assertEqual(self.capture.snapshot()["inputs"], [])
        self.message("new", "请增加导出 password=secret-value")
        self.capture.poll()
        self.capture.poll()
        snapshot = self.capture.snapshot()
        self.assertEqual(len(snapshot["inputs"]), 1)
        self.assertNotIn("secret-value", json.dumps(snapshot))
        self.assertEqual(self.store.listing(), [])
        self.assertEqual(ChatCapture(self.store, self.root).snapshot()["inputs"], snapshot["inputs"])

    def test_tools_and_context_not_captured(self):
        self.capture.poll()
        self.message("tool", "凭据和工具输出", "commandExecution")
        self.message("context", "<environment_context>runtime</environment_context>")
        self.capture.poll()
        self.assertEqual(self.capture.snapshot()["inputs"], [])

    def test_new_thread_and_edited_message(self):
        self.capture.poll()
        with closing(sqlite3.connect(self.root / "state_5.sqlite")) as db, db:
            db.execute("INSERT INTO threads VALUES ('t2','新会话',NULL,NULL,0,2)")
        self.message("edit", "新增能力")
        self.capture.poll()
        with closing(sqlite3.connect(self.root / "thread_history_1.sqlite")) as db, db:
            db.execute("UPDATE thread_items SET updated_at_ordinal=2,item_json=? WHERE item_id='edit'",
                       (json.dumps({"content": [{"type": "text", "text": "修改验收"}]}),))
        self.capture.poll()
        snapshot = self.capture.snapshot()
        self.assertEqual(len(snapshot["chats"]), 2)
        self.assertEqual(len(snapshot["inputs"]), 2)
        self.assertTrue(snapshot["inputs"][0]["edited"])

    def test_missing_source_does_not_guess(self):
        (self.root / "state_5.sqlite").unlink()
        self.capture.poll()
        self.assertIn("不可用", self.capture.snapshot()["health"])
        self.assertEqual(self.capture.snapshot()["chats"], [])

    def test_recorded_blocker_and_development_scope(self):
        item = {"status": "阻塞", "blocker": "合成测试未通过"}
        result = explain(item)
        self.assertEqual(len(result["topics"]), 1)
        self.assertEqual(result["approval"]["scope"], "develop")
        self.assertIn("不包含", result["approval"]["exclusions"])
        self.assertIn("合成测试未通过", result["summary"])

    def test_unknown_failure_not_invented(self):
        result = explain({"status": "阻塞", "failed_tests": ["test_new_unknown"]})
        self.assertEqual(result["topics"][0]["key"], "recorded")
        self.assertIn("尚未提供", result["summary"])

    def test_generated_project_skills_default_to_private_data(self):
        self.assertEqual(SKILL_ROOT, Path.home() / ".local/share/delivery-console/private/skills")
