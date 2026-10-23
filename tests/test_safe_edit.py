import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "node"))

from node import execute  # noqa: E402
from runtime import NodeRuntime  # noqa: E402
from safe_edit import snapshot, parse_proposal, preview, apply_approved  # noqa: E402


class SafeEditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_existing_file_replaced_with_recoverable_backup(self):
        file = self.root / "notes.txt"
        file.write_text("before\n", encoding="utf-8")
        change = snapshot(self.root, "notes.txt")
        self.assertIn("+after", preview(change, "after\n"))
        manifest = apply_approved(change, "after\n", "task-1")
        self.assertEqual(file.read_text(encoding="utf-8"), "after\n")
        backup = self.root / manifest["backup"]
        self.assertEqual((backup / "original").read_text(encoding="utf-8"), "before\n")
        self.assertEqual(json.loads((backup / "manifest.json").read_text())["original"], "notes.txt")

    def test_new_file_and_stale_snapshot(self):
        change = snapshot(self.root, "new.md")
        self.assertIn("+hello", preview(change, "hello\n"))
        (self.root / "new.md").write_text("other\n", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            apply_approved(change, "hello\n", "task")

    def test_protected_paths_and_symlink(self):
        for name in ("../a.py", ".agent-memory/x.md", "AGENTS.md", "agents.md",
                     "hub/config.json", "folder//a.py", "C:/a.py", "a\\b.py",
                     "con.txt", "folder/a:b.py", "trailing./a.py"):
            with self.subTest(name=name), self.assertRaises((ValueError, FileNotFoundError)):
                snapshot(self.root, name)
        target = self.root / "target.py"
        target.write_text("x", encoding="utf-8")
        try:
            (self.root / "link.py").symlink_to(target)
        except (OSError, NotImplementedError):
            return
        with self.assertRaises(ValueError):
            snapshot(self.root, "link.py")

    def test_proposal_and_diff_limits(self):
        self.assertEqual(parse_proposal('```json\n{"content":"new"}\n```'), "new")
        for response in ("not json", '{"content":""}', '{"content":5}'):
            with self.assertRaises((ValueError, json.JSONDecodeError)):
                parse_proposal(response)
        file = self.root / "large.txt"
        file.write_text("old", encoding="utf-8")
        change = snapshot(self.root, "large.txt")
        with self.assertRaises(ValueError):
            preview(change, "new" * 700)

    def test_node_requires_approval_and_never_enables_raw_edit(self):
        file = self.root / "notes.txt"
        file.write_text("before\n", encoding="utf-8")
        runtime = NodeRuntime({"history_dir": str(self.root / "history"),
                               "budget_state_path": str(self.root / "budget.json")}, self.root)
        task = {"task_id": "test-1", "prompt": "/edit notes.txt: change it",
                "engine": {"type": "serve", "base_url": "http://127.0.0.1:4096",
                           "work_dir": str(self.root)}}
        with patch("node.run_serve", return_value=('{"content":"after\\n"}', "session")) as run:
            with self.assertRaises(PermissionError):
                execute(task, 10, runtime, lambda *_: False)
            self.assertEqual(file.read_text(encoding="utf-8"), "before\n")
            self.assertIn("只生成指定文件", run.call_args.args[1])
            seen = []
            def approve(session, permission_id, permission, item):
                seen.append((session, permission_id, permission, item["patterns"][0]))
                self.assertEqual(file.read_text(encoding="utf-8"), "before\n")
                return True
            result, session = execute(task, 10, runtime, approve)
        self.assertEqual(session, "session")
        self.assertIn("已应用批准", result)
        self.assertEqual(file.read_text(encoding="utf-8"), "after\n")
        self.assertEqual(seen[0][2], "safe_change")
        self.assertIn("+after", seen[0][3])


if __name__ == "__main__":
    unittest.main()
