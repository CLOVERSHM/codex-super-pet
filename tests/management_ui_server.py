"""Temporary synthetic historical source, never the user's chat index."""
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from console import main, Store
from management import Management, period
from session_board import SessionBoard

original = Store.__init__


def initialize(self, *args, **kwargs):
    kwargs["projects"] = None
    original(self, *args, **kwargs)
    project = self.catalog()["projects"][0]
    home = self.path.parent
    thread = "00000000-0000-4000-8000-000000000001"
    with closing(sqlite3.connect(home / "state_5.sqlite")) as db, db:
        db.execute("CREATE TABLE threads(id,title,name,project_id,archived,updated_at_ms)")
        db.execute("INSERT INTO threads VALUES (?,'合成历史会话',NULL,?,0,1)", (thread, project["id"]))
    with closing(sqlite3.connect(home / "thread_history_1.sqlite")) as db, db:
        db.execute("CREATE TABLE thread_items(thread_id,turn_id,item_id,created_at_ms,item_type,item_json,updated_at_ordinal)")
        start, _ = period({"start": "2026-10-17", "end": "2026-10-17"})
        db.execute("INSERT INTO thread_items VALUES (?,'turn','item',?,'userMessage',?,1)",
                   (thread, start, json.dumps({"content": [{"type": "text", "text": "增加合成导出"}]})))
    self.management = Management(self, home)
    self.board = SessionBoard(self, home)


Store.__init__ = initialize
main()
