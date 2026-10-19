"""Synthetic Codex host for browser acceptance; never sends to real chats."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from console import main, Store
from test_coordination import FakeHost, THREAD

original_init = Store.__init__


def initialize(self, *args, **kwargs):
    kwargs["projects"] = None
    original_init(self, *args, **kwargs)
    self.host = FakeHost()
    self.host.threads = lambda query: [self.host.read(THREAD)]


Store.__init__ = initialize
main()
