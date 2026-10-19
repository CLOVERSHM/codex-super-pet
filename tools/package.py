#!/usr/bin/env python3
"""Package only application files, never local records, tokens or screenshots."""
import hashlib
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "releases/需求交付控制台-0.1.0-web.zip"
DESKTOP = Path.home() / "Desktop" / OUTPUT.name
FILES = [
    "console.py", "session_board.py", "skill_registry.py", "management.py",
    "blocker_analysis.py", "private_extensions.py", "chat_capture.py",
    "project_platform.py", "codex_projects.py", "codex_host.py",
    "sync_prototype.py", "launcher.py", "stop.py",
    "启动交付控制台.command", "停止交付控制台.command",
    "README.md", "README.en.md", ".gitignore",
]
DIRECTORIES = ["web", "plugins", ".agents", "tools"]
DOCS = ["chat-capture.md", "management-panel.md", "session-board.md",
        "sync-prototype.md", "privacy.md"]
TESTS = ["test_chat_capture.py", "test_codex_projects.py", "test_console.py",
         "test_coordination.py", "test_host.py", "test_management.py",
         "test_package.py", "test_projects.py", "test_session_board.py",
         "test_sync_prototype.py", "ui_server.py", "management_ui_server.py",
         "desktop_projects_browser.cjs", "management_browser.cjs",
         "projects_browser.cjs", "session_workspace_browser.cjs", "sync_browser.cjs"]
paths = [ROOT / p for p in FILES]
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
for directory in DIRECTORIES:
    paths.extend(p for p in (ROOT / directory).rglob("*")
                 if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
paths.extend(ROOT / "docs" / name for name in DOCS)
paths.extend(ROOT / "tests" / name for name in TESTS)
paths.extend(p for p in (ROOT / "docs/images").glob("*.png") if p.is_file())
for path in paths:
    if (not path.is_file() or path.is_symlink() or
            not path.resolve().is_relative_to(ROOT.resolve())):
        raise ValueError(f"Unexpected package path: {path}")
with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(paths):
        archive.write(path, "delivery-console/" + str(path.relative_to(ROOT)))
digest = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
OUTPUT.with_suffix(".zip.sha256").write_text(digest + "  " + OUTPUT.name + "\n")
DESKTOP.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(OUTPUT, DESKTOP)
shutil.copy2(OUTPUT.with_suffix(".zip.sha256"), DESKTOP.with_suffix(".zip.sha256"))
print(str(OUTPUT))
print("sha256 " + digest)
