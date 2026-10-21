import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import history_loop  # noqa: E402


class HistoryLoopTests(unittest.TestCase):
    def test_snapshot_contains_status_not_prompts(self):
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            db_path = root / "hub.db"
            conn = sqlite3.connect(str(db_path))
            conn.executescript(
                "CREATE TABLE tasks (task_id TEXT, status TEXT, prompt TEXT, "
                "created_at TEXT, finished_at TEXT);"
                "INSERT INTO tasks VALUES ('t_1','done','secret prompt','now','now');"
            )
            conn.close()
            with mock.patch.object(history_loop, "hub_health", return_value={"ok": True}):
                payload = history_loop.write_snapshot(root / "history", db_path, "unused")
            text = next((root / "history").glob("*.jsonl")).read_text(encoding="utf-8")
            self.assertEqual(payload["tasks"]["counts"]["done"], 1)
            self.assertNotIn("secret prompt", text)


if __name__ == "__main__":
    unittest.main()
