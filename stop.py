#!/usr/bin/env python3
import json
import os
import signal
from pathlib import Path

from launcher import running

path = Path.home() / ".local/share/delivery-console/runtime.json"
if running():
    runtime = json.loads(path.read_text())
    os.kill(runtime["pid"], signal.SIGTERM)
    print("已停止本机控制台，数据保留。")
else:
    print("控制台未运行。")
