import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from console import Store
from session_board import SessionBoard
from tools.create_project_skill import generate
from tools.validate_project_skill import validate
from skill_registry import discover

THREAD = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"


class Projects:
    def __init__(self):
        self.assignments = {}

    def snapshot(self):
        return {"projects": [{"id": "p1", "name": "合成甲", "root_paths": [], "path": ""},
                             {"id": "p2", "name": "合成乙", "root_paths": [], "path": ""}],
                "assignments": self.assignments}


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.projects = Projects()
        self.store = Store(self.home / "console.sqlite3", projects=self.projects)
        self.store.board = SessionBoard(self.store, self.home, interval=600)
        with sqlite3.connect(self.home / "state_5.sqlite") as db:
            db.execute("CREATE TABLE threads(id,title,name,project_id,archived,updated_at_ms)")
            db.execute("INSERT INTO threads VALUES (?,'合成任务',NULL,'p1',0,100)", (THREAD,))
        with sqlite3.connect(self.home / "thread_history_1.sqlite") as db:
            db.execute("CREATE TABLE thread_items(thread_id,turn_id,item_id,created_at_ms,item_type,item_json,updated_at_ordinal)")
            db.execute("INSERT INTO thread_items VALUES (?,'turn1','item1',100,'userMessage',?,1)",
                       (THREAD, json.dumps({"content": [{"type": "text", "text": "私密内容不得读取"}]})))
            db.execute("INSERT INTO thread_items VALUES (?,'turn1','tool',101,'tool',?,2)",
                       (THREAD, json.dumps({"content": [{"type": "text", "text": "工具内容不得读取"}]})))
        self.store.board.reconcile()

    def tearDown(self):
        self.store.board.close()
        self.temp.cleanup()

    def payload(self, **extra):
        context = self.store.board.context(THREAD)
        return {"thread_id": THREAD, "project_id": context["project_id"],
                "turn_id": context["latest_turn_id"], "observation_hash": context["observation_hash"],
                "task_key": "synthetic", "workflow": "通用", "stage": "实现",
                "state": "blocked", "summary": "合成任务未完成", "reason": "隔离测试失败",
                "next_action": "修复后重测", "evidence": [], **extra}

    def session(self):
        return self.store.board.snapshot()["sessions"][0]

    def test_input_metadata_without_content_and_report_guard(self):
        self.assertEqual(self.session()["state"], "unverified")
        self.assertEqual(self.session()["input_count"], 1)
        self.assertNotIn("私密内容", json.dumps(self.session(), ensure_ascii=False))
        self.assertEqual(self.store.board.snapshot()["interval_seconds"], 600)
        with self.assertRaises(ValueError):
            self.store.board.report(self.payload(), "human")
        report = self.store.board.report(self.payload(), "agent")
        self.assertEqual(self.store.board.report(self.payload(), "agent"), report)
        self.assertEqual(self.session()["state"], "blocked")
        with self.assertRaises(ValueError):
            self.store.board.report(self.payload(reason="另一个原因"), "agent")

    def test_new_and_edited_input_invalidates_report(self):
        old = self.payload()
        self.store.board.report(old, "agent")
        with sqlite3.connect(self.home / "thread_history_1.sqlite") as db:
            db.execute("UPDATE thread_items SET updated_at_ordinal=4 WHERE item_id='item1'")
        self.store.board.reconcile()
        self.assertEqual(self.session()["state"], "awaiting_report")
        self.assertIsNotNone(self.session()["previous_report"])
        with self.assertRaises(ValueError):
            self.store.board.report(old, "agent")
        with sqlite3.connect(self.home / "thread_history_1.sqlite") as db:
            db.execute("INSERT INTO thread_items VALUES (?,'turn2','item2',200,'userMessage','{}',5)", (THREAD,))
        self.store.board.reconcile()
        self.assertEqual(self.store.board.context(THREAD)["latest_turn_id"], "turn2")

    def test_assignment_and_exclusion(self):
        original = self.payload()
        self.store.board.report(original, "agent")
        self.projects.assignments[THREAD] = "p2"
        self.store.board.reconcile()
        self.assertEqual(self.session()["state"], "awaiting_report")
        self.assertEqual(self.session()["project_id"], "p2")
        self.projects.assignments[THREAD] = "p1"
        self.store.board.reconcile()
        self.assertEqual(self.session()["state"], "awaiting_report")
        self.assertNotEqual(self.store.board.context(THREAD)["observation_hash"], original["observation_hash"])
        self.projects.assignments[THREAD] = "p2"
        self.store.board.reconcile()
        self.store.manage_catalog({"id": "p2", "revision": 1, "capture_excluded": True}, "human")
        self.store.board.reconcile()
        self.assertEqual(self.store.board.snapshot()["sessions"], [])
        with self.assertRaises(ValueError):
            self.store.board.context(THREAD)
        self.store.manage_catalog({"id": "p2", "revision": 2, "capture_excluded": False}, "human")
        self.store.board.reconcile()
        self.assertEqual(self.session()["state"], "unverified")

    def test_revert_input_does_not_resurrect_old_report_and_restart_persists_version(self):
        original = self.payload()
        self.store.board.report(original, "agent")
        with sqlite3.connect(self.home / "thread_history_1.sqlite") as db:
            db.execute("UPDATE thread_items SET updated_at_ordinal=2 WHERE item_id='item1'")
        self.store.board.reconcile()
        with sqlite3.connect(self.home / "thread_history_1.sqlite") as db:
            db.execute("UPDATE thread_items SET updated_at_ordinal=1 WHERE item_id='item1'")
        self.store.board.reconcile()
        self.assertEqual(self.session()["state"], "awaiting_report")
        self.assertNotEqual(original["observation_hash"], self.store.board.context(THREAD)["observation_hash"])
        self.store.board = SessionBoard(self.store, self.home)
        self.store.board.reconcile()
        self.assertEqual(self.session()["state"], "awaiting_report")

    def test_failed_source_keeps_snapshot_and_disables_reports(self):
        (self.home / "thread_history_1.sqlite").unlink()
        self.store.board.reconcile()
        self.assertIn("不可用", self.store.board.snapshot()["health"])
        self.assertEqual(len(self.store.board.snapshot()["sessions"]), 1)
        with self.assertRaises(ValueError):
            self.store.board.report(self.payload(), "agent")

    def test_generated_skill_must_be_enabled_for_project(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            skill = generate("p1", "synthetic-flow", "合成流程", "诊断", "合成专用场景",
                             "先做隔离分析，再汇报状态。", root=root)
            self.assertEqual(validate(skill)["project_id"], "p1")
            with patch("project_platform.discover", lambda: discover(root)):
                self.store.manage_catalog({"id": "p1", "revision": 1,
                                           "session_skills": ["synthetic-flow"]}, "human")
                with self.assertRaises(ValueError):
                    self.store.manage_catalog({"id": "p2", "revision": 1,
                                               "session_skills": ["synthetic-flow"]}, "human")
            self.store.board.report(self.payload(skill_id="synthetic-flow"), "agent")
            self.assertEqual(self.session()["report"]["skill_id"], "synthetic-flow")

    def test_historical_review_is_separate_stale_and_lower_priority(self):
        context = self.store.board.context(THREAD)
        review = {**context, "thread_id": THREAD, "task_key": "synthetic",
                  "stage": "历史核查", "state": "waiting_user", "summary": "仅有合成答复",
                  "reason": "缺少合成验收", "next_action": "补充合成验收",
                  "evidence": ["合成会话 turn1 的答复"]}
        with self.assertRaises(ValueError):
            self.store.board.historical_review(review, "agent")
        with self.assertRaises(ValueError):
            self.store.board.historical_review({**review, "evidence": []}, "human")
        self.store.board.historical_review(review, "human")
        self.assertEqual(self.session()["state"], "waiting_user")
        self.assertEqual(self.session()["report"]["skill_id"], "historical-review")
        self.store.board.report(self.payload(), "agent")
        self.assertEqual(self.session()["state"], "blocked")
        with sqlite3.connect(self.home / "thread_history_1.sqlite") as db:
            db.execute("UPDATE thread_items SET updated_at_ordinal=9 WHERE item_id='item1'")
        self.store.board.reconcile()
        self.assertEqual(self.session()["state"], "awaiting_report")
        self.assertIsNone(self.session()["report"])
        with self.assertRaises(ValueError):
            self.store.board.historical_review(review, "human")
        self.store.manage_catalog({"id": "p1", "revision": 1, "capture_excluded": True}, "human")
        self.store.board.reconcile()
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM board_history_reviews").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
