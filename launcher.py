#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = Path.home() / ".local/share/delivery-console"


def running():
    try:
        runtime = json.loads((DATA / "runtime.json").read_text())
        # A stale PID or an unrelated listener must not be treated as this app.
        os.kill(runtime["pid"], 0)
        request = urllib.request.Request(runtime["url"] + "/api/items",
                                         headers={"Authorization": "Bearer " + runtime["agent_token"]})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=1) as response:
            json.load(response)
        return runtime["url"]
    except (OSError, ValueError, KeyError):
        return None


def main():
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    url = running()
    if not url:
        (DATA / "private").mkdir(parents=True, exist_ok=True, mode=0o700)
        log = open(DATA / "private/server.log", "ab")
        subprocess.Popen([sys.executable, str(ROOT / "console.py")], cwd=ROOT,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        log.close()
        for _ in range(50):
            time.sleep(.1)
            url = running()
            if url:
                break
    if not url:
        sys.exit("启动失败，请检查 ~/.local/share/delivery-console/private/server.log")
    print(url)
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)


if __name__ == "__main__":
    main()
