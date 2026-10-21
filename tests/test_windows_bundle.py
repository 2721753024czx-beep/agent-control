import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_windows_bundle import build  # noqa: E402


class WindowsBundleTests(unittest.TestCase):
    def test_bundle_contains_installer_and_no_runtime_secrets(self):
        with tempfile.TemporaryDirectory() as temp_name:
            output = Path(temp_name) / "bundle.zip"
            build(output)
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
            self.assertIn("agent-control/scripts/windows/install.ps1", names)
            self.assertIn("agent-control/scripts/windows/verify.ps1", names)
            self.assertIn("agent-control/scripts/windows/run-opencode.cmd", names)
            self.assertIn("agent-control/scripts/run_safe_opencode.py", names)
            self.assertIn("agent-control/assets/windows/shared-memory/SKILL.md", names)
            self.assertIn("agent-control/assets/windows/shared-memory/scripts/memory.py", names)
            self.assertIn("agent-control/MANIFEST.sha256", names)
            self.assertNotIn("agent-control/hub/config.json", names)
            self.assertNotIn("agent-control/node/config.json", names)
            self.assertFalse(any(name.endswith((".db", ".log")) for name in names))


if __name__ == "__main__":
    unittest.main()
