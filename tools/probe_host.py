#!/usr/bin/env python3
"""Read-only shared-host handshake; never bootstraps a daemon or reads chats."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_host import Rpc

try:
    with Rpc(shared=True):
        print(json.dumps({"shared_handshake": True, "desktop_event_coverage": "not_tested",
                          "desktop_write": "not_tested"}))
except Exception:
    print(json.dumps({"shared_handshake": False, "desktop_event_coverage": "unavailable",
                      "desktop_write": "unavailable",
                      "action": "No daemon started or configuration changed."}))
    sys.exit(1)
