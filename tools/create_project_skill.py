#!/usr/bin/env python3
"""Generate a portable project skill with a mandatory session-report contract."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from skill_registry import SLUG, ROOT


def generate(project_id, slug, name, workflow, description, instructions="", root=ROOT):
    if not isinstance(project_id, str) or not project_id or len(project_id) > 128:
        raise ValueError("需要准确项目编号")
    if slug in ["session-status", "manage-delivery", "create-session-skill"] or not SLUG.fullmatch(slug):
        raise ValueError("无效或保留的 skill 名称")
    for value in [name, workflow, description]:
        if (not isinstance(value, str) or not value.strip() or len(value) > 160 or
                any(c in value for c in "\n\r") or value != value.strip()):
            raise ValueError("名称、工作流及适用场景须为单行短文本")
    if description.startswith(("!", "&", "*", "-", "?", ":", "[", "{", "#", "|", ">", "@", "`", '"', "'")):
        raise ValueError("适用场景不能以 YAML 特殊字符开头")
    if not isinstance(instructions, str) or not instructions.strip() or len(instructions) > 20000:
        raise ValueError("请提供项目工作流说明（最多20000字）")
    folder = root / slug
    if folder.exists():
        raise ValueError("skill 已存在，请手动审阅更新，不覆盖")
    folder.mkdir(parents=True)
    manifest = {"schema": 1, "id": slug, "project_id": project_id, "name": name}
    body = f"""---
name: {slug}
description: {description}
---

# {name}

仅适用于项目 ID `{project_id}`，工作流 `{workflow}`。
先执行下述项目专属流程，保持其原有审批与业务边界：

{instructions.strip()}

## 状态回报契约

本轮工作结束或受阻时，按 `../session-status/SKILL.md` 取得当前准确会话 ID
及最新 `project_id`、`turn_id`、`observation_hash`。确保项目 ID 为
`{project_id}`，提交 `board-report`；将 `skill_id` 固定为 `{slug}`、
`workflow` 固定为 `{workflow}`，`stage` 使用本流程自己的节点名称。
统一 `state` 枚举与阻塞原因由通用契约校验；脱敏短摘要与实际证据引用必须可信。
无法获取准确身份、版本过期或项目已加白则不写回报。
不因本轮回复结束就声称任务完成，也不借状态回报执行审批或生产操作。
"""
    (folder / "SKILL.md").write_text(body)
    (folder / "contract.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return folder


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--instructions-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(generate(args.project_id, args.slug, args.name, args.workflow, args.description,
                       args.instructions_file.read_text()))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
