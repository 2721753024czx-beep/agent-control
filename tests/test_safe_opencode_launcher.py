import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import run_safe_opencode


class SafeOpenCodeLauncherTests(unittest.TestCase):
    def test_model_and_deny_permissions_are_injected(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "node.json"
            config_path.write_text(json.dumps({
                "serve_password": "x" * 32,
                "serve_port": 4097,
                "opencode_argv": ["/bin/echo"],
                "opencode_model": "deepseek/deepseek-flash",
                "default_work_dir": directory,
            }), encoding="utf-8")
            with mock.patch.object(run_safe_opencode, "CONFIG", config_path):
                with mock.patch.object(run_safe_opencode.subprocess, "call", return_value=0) as call:
                    with self.assertRaises(SystemExit) as result:
                        run_safe_opencode.main()
        self.assertEqual(result.exception.code, 0)
        kwargs = call.call_args.kwargs
        config = json.loads(kwargs["env"]["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(config["model"], "deepseek/deepseek-flash")
        for tool in ("bash", "edit", "task", "external_directory"):
            self.assertEqual(config["permission"][tool], "deny")
        self.assertEqual(call.call_args.args[0][-6:],
                         ["serve", "--pure", "--hostname", "127.0.0.1", "--port", "4097"])


if __name__ == "__main__":
    unittest.main()
