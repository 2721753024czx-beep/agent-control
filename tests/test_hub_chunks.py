import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hub"))

from hub import _chunks  # noqa: E402


class HubChunkTests(unittest.TestCase):
    def test_long_unbroken_reply_is_hard_split(self):
        text = "x" * 25
        chunks = _chunks(text, limit=10)
        self.assertEqual(chunks, ["x" * 10, "x" * 10, "x" * 5])
        self.assertTrue(all(len(chunk) <= 10 for chunk in chunks))

    def test_paragraphs_are_kept_when_they_fit(self):
        chunks = _chunks("alpha\n\nbeta", limit=20)
        self.assertEqual(chunks, ["alpha\n\nbeta"])

    def test_invalid_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            _chunks("text", limit=0)


if __name__ == "__main__":
    unittest.main()
