import json
import os
import signal
import subprocess
import tempfile
import time
import unittest
import urllib.request
import zipfile
from pathlib import Path


class PackageTests(unittest.TestCase):
    def test_archive_extracted_app_runs_independently(self):
        archive = Path.home() / "Desktop/需求交付控制台-0.1.0-web.zip"
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            with zipfile.ZipFile(archive) as package:
                self.assertIsNone(package.testzip())
                self.assertFalse(any("runtime.json" in name or ".sqlite3" in name or
                                     "artifacts/" in name or "/private/" in name
                                     for name in package.namelist()))
                self.assertFalse(any(b"xdbamp" in package.read(name).lower()
                                     for name in package.namelist()
                                     if name.endswith((".py", ".js", ".md", ".json", ".html"))
                                     and not name.endswith("/test_package.py")))
                for name in ("README.md", "README.en.md", "docs/images/first-run.png",
                             "docs/images/session-board.png", "docs/images/management.png"):
                    self.assertIn("delivery-console/" + name, package.namelist())
                package.extractall(directory)
                executable = package.getinfo("delivery-console/启动交付控制台.command")
                self.assertTrue((executable.external_attr >> 16) & 0o100)
            app = directory / "delivery-console"
            data = directory / "private-data"
            codex_home = directory / "synthetic-codex"
            codex_home.mkdir()
            (codex_home / ".codex-global-state.json").write_text(json.dumps({
                "local-projects": {"synthetic-project": {"id": "synthetic-project",
                    "name": "合成项目", "rootPaths": [str(directory)]}},
                "project-order": ["synthetic-project"]}))
            process = subprocess.Popen(["python3", str(app / "console.py"), "--data", str(data),
                                        "--source", str(directory / "empty-source"), "--port", "8901"],
                                       env={**os.environ, "CODEX_HOME": str(codex_home)},
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                for _ in range(100):
                    if (data / "runtime.json").exists():
                        break
                    time.sleep(.05)
                runtime = json.loads((data / "runtime.json").read_text())
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(runtime["url"] + "/") as response:
                    self.assertIn("会话工作台", response.read().decode())
                request = urllib.request.Request(runtime["url"] + "/api/items",
                                                  data=json.dumps({"title": "包内独立运行", "project_id": "synthetic-project"}).encode(),
                                                  headers={"Authorization": "Bearer " + runtime["agent_token"],
                                                           "Content-Type": "application/json"})
                with opener.open(request) as response:
                    self.assertEqual(json.load(response)["title"], "包内独立运行")
            finally:
                process.terminate()
                process.wait(timeout=10)
            self.assertFalse((data / "runtime.json").exists())

    def test_archive_launcher_creates_private_log_directory(self):
        archive = Path.home() / "Desktop/需求交付控制台-0.1.0-web.zip"
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            with zipfile.ZipFile(archive) as package:
                package.extractall(directory)
            home = directory / "home"
            home.mkdir()
            codex_home = directory / "synthetic-codex"
            codex_home.mkdir()
            (codex_home / ".codex-global-state.json").write_text(json.dumps({
                "local-projects": {}, "project-order": []}))
            app = directory / "delivery-console"
            result = subprocess.run(["python3", str(app / "launcher.py"), "--no-browser"],
                                    env={**os.environ, "HOME": str(home), "CODEX_HOME": str(codex_home)},
                                    capture_output=True, text=True, timeout=15)
            data = home / ".local/share/delivery-console"
            runtime = data / "runtime.json"
            try:
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue((data / "private/server.log").is_file())
                self.assertTrue(runtime.is_file())
            finally:
                if runtime.is_file():
                    os.kill(json.loads(runtime.read_text())["pid"], signal.SIGTERM)
                    for _ in range(100):
                        if not runtime.exists():
                            break
                        time.sleep(.05)
                self.assertFalse(runtime.exists())


if __name__ == "__main__":
    unittest.main()
