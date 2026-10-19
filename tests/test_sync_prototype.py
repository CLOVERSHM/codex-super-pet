import tempfile
import threading
import unittest
import json
import urllib.error
import urllib.request
from pathlib import Path
from sync_prototype import SyncLedger, SyntheticHost, normalized, serve


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = SyncLedger(Path(self.temp.name) / "sync.sqlite3")
        self.host = SyntheticHost(self.ledger)
        self.ident = self.host.create("合成会话")

    def tearDown(self):
        self.temp.cleanup()

    def session(self):
        return self.ledger.snapshot()["sessions"][0]

    def approve(self):
        return self.ledger.approve(self.ident, self.session()["revision"], "preflight",
                                   "合成只读检查", "人工确认", "human")

    def test_session_input_goal_projection(self):
        self.host.emit(self.ident, "input", "新任务")
        self.host.emit(self.ident, "goal", "新目标")
        self.assertEqual(self.session()["revision"], 2)
        self.assertEqual(self.session()["inputs"][0]["text"], "新任务")
        self.assertEqual(self.session()["objective"], "新目标")

    def test_user_item_started_completed_deduplicated(self):
        event = {"method": "item/started", "params": {"threadId": self.ident,
                 "item": {"type": "userMessage", "id": "one", "content": [
                     {"type": "text", "text": "任务"}]}}}
        self.assertTrue(self.ledger.ingest(event))
        event["method"] = "item/completed"
        self.assertFalse(self.ledger.ingest(event))
        self.assertEqual(len(self.session()["inputs"]), 1)

    def test_goal_change_back_not_lost(self):
        for value in ("A", "B", "A"):
            self.host.emit(self.ident, "goal", value)
        self.assertEqual(self.session()["revision"], 3)
        self.host.emit(self.ident, "goal", "A")
        self.assertEqual(self.session()["revision"], 3)

    def test_unknown_session_not_guessed(self):
        with self.assertRaises(ValueError):
            self.host.emit("11111111-1111-4111-8111-111111111111", "input", "任务")

    def test_tool_payload_not_retained(self):
        self.assertIsNone(normalized({"method": "item/started", "params": {
            "item": {"type": "commandExecution", "output": "private"}}}))

    def test_stale_approval_rejected(self):
        self.host.emit(self.ident, "goal", "A")
        with self.assertRaises(ValueError):
            self.ledger.approve(self.ident, 0, "preflight", "scope", "note", "human")

    def test_new_input_invalidates_approved_command(self):
        command = self.approve()
        self.host.emit(self.ident, "input", "新的范围")
        with self.assertRaises(ValueError):
            self.ledger.dispatch(command["id"], self.host, "human")
        self.assertEqual(self.host.sent, [])
        self.assertTrue(self.session()["commands"][0]["stale"])

    def test_goal_clear_invalidates_command(self):
        self.host.emit(self.ident, "goal", "A")
        command = self.approve()
        self.ledger.ingest({"method": "thread/goal/cleared", "params": {"threadId": self.ident}})
        with self.assertRaises(ValueError):
            self.ledger.dispatch(command["id"], self.host, "human")

    def test_receipt_exact_thread_and_echo_not_new_task(self):
        command = self.approve()
        result = self.ledger.dispatch(command["id"], self.host, "human")
        self.assertEqual(result["receipt"]["thread_id"], self.ident)
        self.assertEqual(result["receipt"]["command_id"], command["id"])
        self.assertEqual(self.session()["inputs"], [])
        self.assertEqual(self.session()["revision"], 0)

    def test_duplicate_approval_and_send_rejected(self):
        command = self.approve()
        with self.assertRaises(ValueError):
            self.approve()
        self.ledger.dispatch(command["id"], self.host, "human")
        with self.assertRaises(ValueError):
            self.ledger.dispatch(command["id"], self.host, "human")
        self.assertEqual(len(self.host.sent), 1)

    def test_unknown_result_not_retried(self):
        class FailedHost:
            def send(self, _):
                raise ConnectionError()
        command = self.approve()
        self.assertEqual(self.ledger.dispatch(command["id"], FailedHost(), "human")["state"], "unknown")
        with self.assertRaises(ValueError):
            self.ledger.dispatch(command["id"], self.host, "human")
        with self.assertRaises(ValueError):
            self.approve()

    def test_wrong_receipt_not_accepted(self):
        class WrongHost:
            def send(self, command):
                return {"thread_id": "wrong", "command_id": command["id"]}
        command = self.approve()
        self.assertEqual(self.ledger.dispatch(command["id"], WrongHost(), "human")["state"],
                         "unknown")

    def test_restart_sending_becomes_unknown(self):
        command = self.approve()
        with self.ledger.db() as db:
            command["state"] = "sending"
            db.execute("UPDATE commands SET data=? WHERE id=?",
                       (json.dumps(command), command["id"]))
        restored = SyncLedger(self.ledger.path)
        self.assertEqual(restored.snapshot()["sessions"][0]["commands"][0]["state"], "unknown")

    def test_agent_cannot_approve_or_send(self):
        with self.assertRaises(ValueError):
            self.ledger.approve(self.ident, 0, "preflight", "scope", "note", "agent")
        with self.assertRaises(ValueError):
            self.ledger.dispatch("any", self.host, "agent")

    def test_production_scope_rejected(self):
        with self.assertRaises(ValueError):
            self.ledger.approve(self.ident, 0, "production", "scope", "note", "human")

    def test_persistence_and_reconnect_snapshot(self):
        self.host.emit(self.ident, "input", "任务")
        restored = SyncLedger(self.ledger.path)
        self.assertEqual(restored.snapshot(), self.ledger.snapshot())

    def test_long_poll_wakes_on_event(self):
        after = self.ledger.snapshot()["cursor"]
        result = []
        worker = threading.Thread(target=lambda: result.append(self.ledger.snapshot(after, 2)))
        worker.start()
        self.host.emit(self.ident, "goal", "A")
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertGreater(result[0]["cursor"], after)


class SyncHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server, self.ledger, self.host = serve(self.temp.name, 0)
        self.worker = threading.Thread(target=self.server.serve_forever)
        self.worker.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.token = self.request("/api/session")["token"]

    def tearDown(self):
        self.server.shutdown()
        self.worker.join()
        self.server.server_close()
        self.temp.cleanup()

    def request(self, path, body=None, headers=None):
        headers = {"Authorization": "Bearer " + getattr(self, "token", ""), **(headers or {})}
        request = urllib.request.Request(self.url + path,
            data=json.dumps(body).encode() if body is not None else None, headers=headers)
        with self.opener.open(request, timeout=5) as response:
            return json.load(response)

    def test_http_session_event_approval_round_trip(self):
        ident = self.request("/api/synthetic/session", {"title": "HTTP合成"})["id"]
        self.request("/api/synthetic/event", {"thread_id": ident, "kind": "input", "value": "任务"})
        command = self.request("/api/approve", {"thread_id": ident, "revision": 1,
            "scope": "preflight", "step": "scope", "note": "note"})
        result = self.request("/api/dispatch", {"command_id": command["id"]})
        self.assertEqual(result["receipt"]["thread_id"], ident)
        self.assertEqual(result["state"], "received")
        self.assertEqual(len(self.request("/api/snapshot")["sessions"][0]["inputs"]), 1)

    def test_unauthorized_snapshot_and_write(self):
        for path, body in (("/api/snapshot", None), ("/api/synthetic/session", {"title": "x"})):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request(path, body, {"Authorization": "Bearer wrong"})
            self.assertEqual(error.exception.code, 401)

    def test_cross_origin_denied(self):
        for path, body in (("/api/session", None), ("/api/synthetic/session", {"title": "x"})):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request(path, body, {"Origin": "http://evil.example"})
            self.assertEqual(error.exception.code, 403)

    def test_invalid_host_denied(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/session", headers={"Host": "evil.example"})
        self.assertEqual(error.exception.code, 403)

    def test_malformed_body_denied(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/approve", [])
        self.assertEqual(error.exception.code, 400)

    def test_arbitrary_real_thread_denied(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.request("/api/synthetic/event", {"thread_id":
                "11111111-1111-4111-8111-111111111111", "kind": "input", "value": "任务"})


if __name__ == "__main__":
    unittest.main()
