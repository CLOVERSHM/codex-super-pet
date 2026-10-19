"""Read-only desktop project metadata adapter."""
import json
import os
from pathlib import Path


class DesktopProjects:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / ".codex-global-state.json"

    def snapshot(self):
        try:
            if self.path.stat().st_size > 32 * 1024 * 1024:
                raise ValueError("状态文件过大")
            state = json.loads(self.path.read_text())
            local, order = state["local-projects"], state["project-order"]
            if not isinstance(local, dict) or not isinstance(order, list) or state.get("remote-projects"):
                raise ValueError("项目格式不支持或包含未支持的远程项目")
            projects = []
            for ident in dict.fromkeys([i for i in order if i in local] + list(local)):
                value = local[ident]
                if (not isinstance(ident, str) or not isinstance(value, dict) or value.get("id") != ident or
                        not isinstance(value.get("name"), str) or not value["name"].strip() or
                        not isinstance(value.get("rootPaths"), list) or
                        not all(isinstance(p, str) and p for p in value["rootPaths"])):
                    raise ValueError("项目元数据格式不支持")
                projects.append({"id": ident, "name": value["name"], "root_paths": value["rootPaths"],
                                 "path": next(iter(value["rootPaths"]), ""), "host_id": "local"})
            assignments = state.get("thread-project-assignments", {})
            if not isinstance(assignments, dict):
                raise ValueError("会话归属格式不支持")
            membership = {ident: value["projectId"] for ident, value in assignments.items()
                          if isinstance(value, dict) and value.get("projectKind") == "local" and
                          value.get("projectId") in local}
            return {"projects": projects, "assignments": membership}
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("无法读取 Codex 项目列表；请检查桌面应用状态或适配器版本") from exc
