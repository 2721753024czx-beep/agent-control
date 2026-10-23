import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ConfigExampleTests(unittest.TestCase):
    def test_examples_use_protected_serve_not_rejected_cli(self):
        for suffix in ("", ".windows"):
            with self.subTest(suffix=suffix):
                hub = json.loads((ROOT / "hub" / ("config.example" + suffix + ".json")).read_text(encoding="utf-8"))
                node = json.loads((ROOT / "node" / ("config.example" + suffix + ".json")).read_text(encoding="utf-8"))
                self.assertGreaterEqual(len(node["serve_password"]), 32)
                self.assertEqual(node["serve_port"], 4096)
                self.assertIn(node["default_work_dir"], node["allowed_work_dirs"])
                for agent in hub["agents"]:
                    self.assertEqual(agent["engine_type"], "serve")
                    self.assertEqual(agent["exec_path"], "http://127.0.0.1:4096")
                    self.assertTrue(agent["allowed_open_ids"])
                    self.assertTrue(agent["work_dir"])


if __name__ == "__main__":
    unittest.main()
