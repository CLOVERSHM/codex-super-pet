import hashlib
import importlib.util
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import Store, make_server


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.store = Store(self.root / "data/db.sqlite3", self.source)
        self.item = self.store.create({"title": "预约启动修复", "kind": "Bug"})

    def tearDown(self):
        self.temp.cleanup()

    def mutate(self, data, actor="agent"):
        self.item = self.store.mutate(self.item["id"], {"version": self.item["version"], **data}, actor)
        return self.item

    def evidence(self, result="通过", candidate="v1"):
        return self.mutate({"action": "evidence", "candidate": candidate, "label": "完整回归", "result": result})

    def test_persistence(self):
        self.assertEqual(Store(self.store.path).get(self.item["id"])["title"], "预约启动修复")

    def test_conflict(self):
        self.mutate({"action": "update", "phase": "开发"})
        with self.assertRaises(ValueError):
            self.store.mutate(self.item["id"], {"action": "update", "version": 1})

    def test_block_requires_reason(self):
        with self.assertRaises(ValueError):
            self.mutate({"action": "update", "status": "阻塞"})
        self.mutate({"action": "update", "status": "阻塞", "blocker": "缺少当前模板"})

    def test_reject_unsupported_state(self):
        with self.assertRaises(ValueError):
            self.mutate({"action": "update", "phase": "部署中"})

    def test_version_bound_gate(self):
        self.evidence()
        with self.assertRaises(ValueError):
            self.mutate({"action": "update", "phase": "待发布", "candidate": "v2"})
        self.mutate({"action": "update", "phase": "待发布", "candidate": "v1"})

    def test_completed_status_requires_complete_phase(self):
        with self.assertRaises(ValueError):
            self.mutate({"action": "update", "phase": "需求", "status": "已完成"}, "human")

    def test_failed_attempt_retained_and_invalidates_pass(self):
        self.evidence()
        self.evidence("失败")
        self.assertEqual(len(self.item["evidence"]), 2)
        with self.assertRaises(ValueError):
            self.mutate({"action": "update", "phase": "待发布", "candidate": "v1"})

    def test_agent_cannot_approve_or_finish(self):
        self.evidence()
        self.mutate({"action": "update", "candidate": "v1"})
        self.mutate({"action": "decision", "label": "发布范围"})
        decision = self.item["decisions"][0]["id"]
        with self.assertRaises(ValueError):
            self.mutate({"action": "resolve", "decision": decision, "state": "批准", "note": "ok"})
        with self.assertRaises(ValueError):
            self.mutate({"action": "update", "phase": "观察"})
        with self.assertRaises(ValueError):
            self.mutate({"action": "update", "status": "已完成"})

    def test_human_completion_flow(self):
        self.evidence()
        self.mutate({"action": "update", "candidate": "v1", "phase": "待发布"})
        self.mutate({"action": "decision", "label": "发布范围"})
        self.mutate({"action": "resolve", "decision": self.item["decisions"][0]["id"],
                     "state": "批准", "note": "确认范围"}, "human")
        with self.assertRaises(ValueError):
            self.mutate({"action": "update", "phase": "观察"}, "human")
        self.mutate({"action": "update", "phase": "观察", "note": "外部部署回执编号 demo"}, "human")
        self.mutate({"action": "update", "phase": "完成", "status": "已完成", "note": "验收回执 demo"}, "human")
        self.assertEqual(self.item["status"], "已完成")

    def test_candidate_change_invalidates_decision(self):
        self.evidence()
        self.mutate({"action": "update", "candidate": "v1"})
        self.mutate({"action": "decision", "label": "发布"})
        decision = self.item["decisions"][0]["id"]
        self.mutate({"action": "update", "candidate": "v2"})
        with self.assertRaises(ValueError):
            self.mutate({"action": "resolve", "decision": decision, "state": "批准", "note": "ok"}, "human")

    def test_approval_requires_latest_pass(self):
        self.mutate({"action": "update", "candidate": "v1"})
        self.mutate({"action": "decision", "label": "发布"})
        with self.assertRaises(ValueError):
            self.mutate({"action": "resolve", "decision": self.item["decisions"][0]["id"],
                         "state": "批准", "note": "ok"}, "human")

    def test_new_evidence_invalidates_old_approval(self):
        self.evidence()
        self.mutate({"action": "update", "candidate": "v1"})
        self.mutate({"action": "decision", "label": "发布"})
        self.mutate({"action": "resolve", "decision": self.item["decisions"][0]["id"],
                     "state": "批准", "note": "ok"}, "human")
        self.evidence()
        with self.assertRaises(ValueError):
            self.mutate({"action": "update", "phase": "观察", "note": "receipt"}, "human")

    def test_new_decision_supersedes_old_approval(self):
        self.evidence()
        self.mutate({"action": "update", "candidate": "v1"})
        self.mutate({"action": "decision", "label": "发布"})
        self.mutate({"action": "resolve", "decision": self.item["decisions"][0]["id"],
                     "state": "批准", "note": "ok"}, "human")
        self.mutate({"action": "decision", "label": "重新确认"})
        with self.assertRaises(ValueError):
            self.mutate({"action": "update", "phase": "观察", "note": "receipt"}, "human")



class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "db.sqlite3")
        self.server, self.agent = make_server(self.store, 0)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.temp.cleanup()

    def request(self, path, data=None, headers=None):
        request = urllib.request.Request(self.url + path, data=json.dumps(data).encode() if data is not None else None,
                                         headers=headers or {})
        return self.opener.open(request, timeout=5)

    def test_unauthorized_api(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/items")
        self.assertEqual(error.exception.code, 401)

    def test_only_legacy_allows_same_origin_embedding(self):
        with self.request("/legacy?embedded=1") as response:
            self.assertIn("frame-ancestors 'self'", response.headers["Content-Security-Policy"])
        with self.request("/") as response:
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        with self.request("/api/session") as response:
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

    def test_cross_origin_session_rejected(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.request("/api/session", headers={"Sec-Fetch-Site": "cross-site"})

    def test_invalid_host_rejected(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.request("/api/session", headers={"Host": "evil.example"})

    def test_cross_origin_mutation_rejected(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.request("/api/items", {"title": "x"}, {"Authorization": "Bearer " + self.agent,
                                                          "Origin": "http://evil.example"})

    def test_agent_create_and_no_human_resolution(self):
        with self.request("/api/items", {"title": "HTTP Bug"}, {"Authorization": "Bearer " + self.agent}) as response:
            item = json.load(response)
        with self.assertRaises(urllib.error.HTTPError):
            self.request("/api/items/" + item["id"], {"version": 1, "action": "resolve"},
                         {"Authorization": "Bearer " + self.agent})

    def test_malformed_body_rejected(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.request("/api/items", ["invalid"], {"Authorization": "Bearer " + self.agent})

    def test_bridge_live_connection(self):
        bridge_path = ROOT / "plugins/delivery-console/skills/manage-delivery/scripts/bridge.py"
        spec = importlib.util.spec_from_file_location("bridge", bridge_path)
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        runtime = Path(self.temp.name) / "runtime.json"
        runtime.write_text(json.dumps({"url": self.url, "agent_token": self.agent}))
        item = bridge.call("create", {"title": "Bridge Bug"}, runtime=runtime)
        self.assertEqual(bridge.call("list", runtime=runtime)[0]["id"], item["id"])
        with self.assertRaises(ValueError):
            bridge.call("update", {"action": "resolve"}, item["id"], runtime)


if __name__ == "__main__":
    unittest.main()
