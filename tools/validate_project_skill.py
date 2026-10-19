#!/usr/bin/env python3
"""Validate generated skill manifest and mandatory report instructions."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from skill_registry import SLUG


def validate(folder):
    folder = Path(folder)
    manifest = json.loads((folder / "contract.json").read_text())
    content = (folder / "SKILL.md").read_text()
    if (manifest.get("schema") != 1 or manifest.get("id") != folder.name or
            not SLUG.fullmatch(manifest["id"]) or not isinstance(manifest.get("project_id"), str) or
            not manifest["project_id"] or not content.startswith(f"---\nname: {folder.name}\n") or
            any(term not in content for term in ["board-report", "observation_hash", "turn_id",
                                                 "skill_id", "session-status/SKILL.md"])):
        raise ValueError("skill 缺少平台状态回报契约")
    return manifest


if __name__ == "__main__":
    print(json.dumps(validate(sys.argv[1]), ensure_ascii=False))
