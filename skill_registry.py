"""Read private project skill manifests; never install them."""
import json
import re
from pathlib import Path

ROOT = Path.home() / ".local/share/delivery-console/private/skills"
SLUG = re.compile(r"[a-z][a-z0-9-]{1,62}\Z")


def discover(root=ROOT):
    skills = []
    for manifest in root.glob("*/contract.json"):
        try:
            data = json.loads(manifest.read_text())
            if (SLUG.fullmatch(data["id"]) and data["id"] == manifest.parent.name and
                    isinstance(data["project_id"], str) and data["project_id"] and
                    isinstance(data["name"], str) and data["name"].strip() and
                    (manifest.parent / "SKILL.md").is_file()):
                skills.append({k: data[k] for k in ["id", "project_id", "name"]})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(skills, key=lambda s: s["id"])
