import unittest
from unittest.mock import patch
from codex_host import Host


class HostTests(unittest.TestCase):
    def test_no_fallback_write_if_shared_socket_missing(self):
        results = []
        with patch("codex_host.Rpc", side_effect=ConnectionError()) as rpc:
            Host().run({}, lambda *args: results.append(args))
            rpc.assert_called_once_with(shared=True)
        self.assertEqual(results[0][0], "待发送")

    def test_busy_thread_never_started(self):
        results = []
        with patch("codex_host.Rpc") as factory:
            rpc = factory.return_value
            rpc.call.return_value = {"thread": {"cwd": "/tmp/synthetic", "status": {"type": "active"}}}
            Host().run({"thread": {"id": "synthetic", "cwd": "/tmp/synthetic"}},
                       lambda *args: results.append(args))
            self.assertEqual(rpc.call.call_count, 1)
        self.assertEqual(results[0][0], "待发送")

    def test_unknown_send_never_automatically_retried(self):
        results = []
        with patch("codex_host.Rpc") as factory:
            rpc = factory.return_value
            rpc.call.side_effect = [
                {"thread": {"cwd": "/tmp/synthetic", "status": {"type": "idle"}}},
                {}, ConnectionError()]
            Host().run({"thread": {"id": "synthetic", "cwd": "/tmp/synthetic"},
                        "scope": "preflight", "prompt": "synthetic"},
                       lambda *args: results.append(args))
            self.assertEqual([c.args[0] for c in rpc.call.call_args_list],
                             ["thread/read", "thread/resume", "turn/start"])
            params = rpc.call.call_args_list[-1].args[1]
            self.assertEqual(params["sandboxPolicy"]["type"], "readOnly")
            self.assertEqual(params["approvalPolicy"], "never")
        self.assertEqual(results[-1][0], "结果未知")
