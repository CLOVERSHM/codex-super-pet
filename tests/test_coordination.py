import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from console import Store

THREAD = "11111111-1111-4111-8111-111111111111"


class FakeHost:
    def read(self, ident):
        return {"id": ident, "title": "合成对话", "cwd": "/tmp/synthetic", "status": "idle"}

    def run(self, job, receipt):
        receipt("执行中", "合成已接收", "synthetic-turn")
        receipt("本轮结束", "合成回执，不是生产", "synthetic-turn")


class CoordinationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.store = Store(self.root / "db.sqlite3", self.source)
        self.store.host = FakeHost()
        self.item = self.store.create({"title": "合成推进需求"})

    def tearDown(self):
        for worker in self.store.workers.values():
            worker.join(timeout=5)
        self.temp.cleanup()

    def act(self, action, actor="human", **fields):
        self.item = self.store.coordinate(self.item["id"], {
            "revision": self.item.get("coordination", {}).get("revision", 0),
            "action": action, **fields}, actor)
        return self.item["coordination"]

    def ready(self, scope="preflight"):
        self.act("bind", thread_id=THREAD)
        self.act("propose", scope=scope, next_step="合成明确范围")

    def test_binding_verified_and_persisted(self):
        self.act("bind", thread_id=THREAD, progress="已合并未上线", progress_evidence="合成证据")
        actual = Store(self.store.path, self.source).get(self.item["id"])
        self.assertEqual(actual["coordination"]["thread"]["title"], "合成对话")

    def test_human_only_operations(self):
        for action in ["bind", "approve", "reject", "dispatch"]:
            with self.assertRaises(ValueError):
                self.act(action, actor="agent", thread_id=THREAD)

    def test_no_production_scope(self):
        with self.assertRaises(ValueError):
            self.act("propose", scope="production", next_step="部署")

    def test_approval_needs_binding_and_note(self):
        self.act("propose", scope="preflight", next_step="预检")
        with self.assertRaises(ValueError):
            self.act("approve", note="批准")
        self.act("bind", thread_id=THREAD)
        with self.assertRaises(ValueError):
            self.act("approve")

    def test_stale_source_approval_blocked(self):
        self.ready()
        self.store.mutate(self.item["id"], {"version": self.item["version"], "action": "update", "candidate": "v2"})
        with self.assertRaises(ValueError):
            self.act("approve", note="批准")

    def test_merge_requires_latest_pass(self):
        self.ready("merge")
        with self.assertRaises(ValueError):
            self.act("approve", note="批准")

    def test_duplicate_approval_and_rebinding_blocked(self):
        self.ready()
        self.act("approve", note="只读预检")
        for action, fields in [("approve", {"note": "重复"}), ("bind", {"thread_id": THREAD}),
                               ("propose", {"scope": "merge", "next_step": "重复"})]:
            with self.assertRaises(ValueError):
                self.act(action, **fields)
        prompt = self.item["coordination"]["jobs"][0]["prompt"]
        self.assertIn("不部署", prompt)
        self.assertIn("不推送远端", prompt)

    def test_dispatch_and_receipt_not_deployment(self):
        self.ready()
        plan = self.act("approve", note="合成批准")
        job_id = plan["jobs"][0]["id"]
        self.act("dispatch", job_id=job_id)
        self.store.workers[job_id].join(timeout=5)
        self.item = self.store.get(self.item["id"])
        self.assertEqual(self.item["coordination"]["jobs"][0]["state"], "本轮结束")
        self.assertNotEqual(self.item["status"], "已完成")
        with self.assertRaises(ValueError):
            self.act("dispatch", job_id=job_id)

    def test_unknown_not_retried_and_wrong_receipt_thread_rejected(self):
        self.ready()
        plan = self.act("approve", note="合成批准")
        job_id = plan["jobs"][0]["id"]
        with self.assertRaises(ValueError):
            self.act("receipt", actor="agent", job_id=job_id, thread_id="wrong",
                     state="执行中", detail="假回执")
        self.act("receipt", actor="agent", job_id=job_id, thread_id=THREAD,
                 state="执行中", detail="合成已收到")
        self.store = Store(self.store.path, self.source)
        self.item = self.store.get(self.item["id"])
        self.assertEqual(self.item["coordination"]["jobs"][0]["state"], "结果未知")
        with self.assertRaises(ValueError):
            self.act("dispatch", job_id=job_id)

    def test_revision_conflict(self):
        self.ready()
        with self.assertRaises(ValueError):
            self.store.coordinate(self.item["id"], {"revision": 0, "action": "approve", "note": "合成"}, "human")

    def test_cancel_unsent_job_then_replan(self):
        self.ready()
        plan = self.act("approve", note="合成批准")
        self.act("cancel", job_id=plan["jobs"][0]["id"])
        self.assertEqual(self.item["coordination"]["jobs"][0]["state"], "已撤销")
        self.act("propose", scope="develop", next_step="合成重新规划")


if __name__ == "__main__":
    unittest.main()
