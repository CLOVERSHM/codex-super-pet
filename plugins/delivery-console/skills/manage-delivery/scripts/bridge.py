#!/usr/bin/env python3
"""Agent-only HTTP bridge; no approval or deployment operations."""
import argparse
import json
import re
import sys
import urllib.error
import urllib.request
import urllib.parse
from pathlib import Path
from urllib.parse import urlparse


def call(command, data=None, ident=None, runtime=None):
    config = json.loads(Path(runtime or Path.home() / ".local/share/delivery-console/runtime.json").read_text())
    url = urlparse(config["url"])
    if url.scheme != "http" or url.hostname != "127.0.0.1" or url.path:
        raise ValueError("仅允许本机控制台")
    if command in ["update", "coordinate"]:
        if not ident or not re.fullmatch("[a-z0-9-]+", ident):
            raise ValueError("无效需求编号")
        if (data.get("action") in ["resolve", "bind", "approve", "reject", "dispatch", "cancel"] or
                data.get("phase") in ["观察", "完成"] or data.get("status") == "已完成"):
            raise ValueError("此操作须人工在控制台确认")
    paths = {"list": "/api/items", "sync": "/api/sync", "create": "/api/items",
             "catalog": "/api/catalog", "intake": "/api/intake", "changes": "/api/intake",
             "board-context": "/api/board/context?thread_id=" + urllib.parse.quote(ident or ""),
             "board-report": "/api/board/report",
             "update": "/api/items/" + (ident or ""),
             "coordinate": "/api/items/" + (ident or "") + "/coordinate"}
    body = None if command in ["list", "catalog", "changes", "board-context"] else json.dumps(data or {}).encode()
    request = urllib.request.Request(config["url"] + paths[command], data=body,
                                     headers={"Authorization": "Bearer " + config["agent_token"],
                                              "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=60) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["list", "sync", "create", "update", "coordinate", "catalog", "intake", "changes", "board-context", "board-report"])
    parser.add_argument("--id")
    parser.add_argument("--json", default="{}")
    parser.add_argument("--runtime", type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.json)
        if not isinstance(data, dict):
            raise ValueError("JSON 必须为对象")
        print(json.dumps(call(args.command, data, args.id, args.runtime), ensure_ascii=False, indent=2))
    except urllib.error.HTTPError as exc:
        print(exc.read().decode(), file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, KeyError) as exc:
        print("控制台连接失败或请求非法：" + type(exc).__name__ + "；请确认应用已启动。", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
