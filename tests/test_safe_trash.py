import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "node"))
from safe_trash import move_to_trash  # noqa: E402


class SafeTrashTests(unittest.TestCase):
    def test_move_records_original_and_preserves_content(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "note.txt").write_text("keep me", encoding="utf-8")
            result = move_to_trash(root, "note.txt", "task-1")
            self.assertFalse((root / "note.txt").exists())
            self.assertEqual((root / result["stored"]).read_text(encoding="utf-8"), "keep me")
            manifest = json.loads((root / result["stored"]).parent.joinpath("manifest.json").read_text())
            self.assertEqual(manifest["original"], "note.txt")
            self.assertEqual(manifest["task_id"], "task-1")

    def test_rejects_parent_traversal_and_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(ValueError):
                move_to_trash(root, "../outside")
            with self.assertRaises(ValueError):
                move_to_trash(root, ".agent-control-trash")
            outside = root.parent / (root.name + "-outside")
            outside.mkdir()
            try:
                link = root / "link"
                try:
                    link.symlink_to(outside, target_is_directory=True)
                except (OSError, NotImplementedError):
                    return
                with self.assertRaises(ValueError):
                    move_to_trash(root, "link")
            finally:
                outside.rmdir()

    def test_rejects_ambiguous_and_windows_style_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for value in (".", "a//b", "a/../b", "C:/Windows", r"..\outside",
                          ".AGENT-CONTROL-TRASH/item"):
                with self.subTest(path=value), self.assertRaises(ValueError):
                    move_to_trash(root, value)

    def test_manifest_exists_before_move_and_failed_move_is_clean(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "note.txt"
            source.write_text("keep me", encoding="utf-8")

            def fail_after_check(_source, target):
                manifest = target.parent / "manifest.json"
                self.assertTrue(manifest.is_file())
                self.assertEqual(json.loads(manifest.read_text())["original"], "note.txt")
                raise OSError("simulated rename failure")

            with patch("safe_trash.os.rename", side_effect=fail_after_check):
                with self.assertRaises(OSError):
                    move_to_trash(root, "note.txt")
            self.assertEqual(source.read_text(encoding="utf-8"), "keep me")
            self.assertEqual(list((root / ".agent-control-trash").iterdir()), [])

    def test_rejects_windows_reparse_point(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "junction").mkdir()
            with patch("safe_trash._is_link_like", side_effect=lambda p: p.name == "junction"):
                with self.assertRaises(ValueError):
                    move_to_trash(root, "junction")


if __name__ == "__main__":
    unittest.main()
