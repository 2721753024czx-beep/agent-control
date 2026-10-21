import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "node"))

from engines import build_cli_command  # noqa: E402


class EngineCommandTests(unittest.TestCase):
    def test_prompt_is_one_argument(self):
        prompt = 'hello; echo "not shell" && whoami'
        parts = build_cli_command('opencode run "{prompt}"', prompt, platform="posix")
        self.assertEqual(parts[1], "run")
        self.assertEqual(parts[2], prompt)
        self.assertEqual(len(parts), 3)

    def test_requires_prompt_placeholder(self):
        with self.assertRaises(ValueError):
            build_cli_command("opencode run", "hello", platform="posix")

    def test_windows_batch_shim_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "official opencode.exe"):
            build_cli_command('C:/tools/opencode.cmd run "{prompt}"', "hello", platform="nt")

    def test_windows_exe_is_direct_argv(self):
        parts = build_cli_command('C:/tools/opencode.exe run "{prompt}"', "hello & dir", platform="nt")
        self.assertEqual(parts, ["C:/tools/opencode.exe", "run", "hello & dir"])


if __name__ == "__main__":
    unittest.main()
