from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pin_codex import pin


class PinCodexTests(unittest.TestCase):
    def test_app_source_keeps_signed_bundle_context(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package = root / 'installed-package'
            package.mkdir()
            (package / 'codex-package.json').write_text('{}')
            (package / 'bin').mkdir()
            (package / 'bin/codex-code-mode-host').write_bytes(b'helper')
            app = package / 'Original.app'
            source = app / 'Contents/MacOS/codex'
            source.parent.mkdir(parents=True)
            source.write_bytes(b'verified-binary'); source.chmod(0o700)
            (app / 'Contents/embedded.provisionprofile').write_bytes(b'profile')
            def ditto(args, **kwargs):
                shutil.copytree(args[-2], args[-1])
            with patch('pin_codex.sys.platform', 'darwin'), \
                 patch('pin_codex.version_of', return_value='codex-cli 0.166.0-alpha.5.1'), \
                 patch('pin_codex.subprocess.run', side_effect=ditto):
                pinned = pin(source, root / 'runtime')
                self.assertEqual(pinned.read_bytes(), b'verified-binary')
                self.assertEqual((pinned.parent.parent / 'embedded.provisionprofile').read_bytes(), b'profile')
                self.assertEqual((pinned.parents[3] / 'bin/codex-code-mode-host').read_bytes(), b'helper')

    def test_pinned_copy_survives_source_update_and_detects_tampering(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'codex'
            source.write_bytes(b'verified-binary')
            source.chmod(0o700)
            with patch('pin_codex.sys.platform', 'darwin'), \
                 patch('pin_codex.version_of', return_value='codex-cli 0.166.0-alpha.5.1'):
                pinned = pin(source, root / 'runtime')
                self.assertEqual(pin(source, root / 'runtime'), pinned)
                source.write_bytes(b'app-updated-binary')
                self.assertEqual(pinned.read_bytes(), b'verified-binary')
                source.write_bytes(b'verified-binary')
                pinned.chmod(0o700)
                pinned.write_bytes(b'tampered')
                with self.assertRaisesRegex(ValueError, '校验失败'):
                    pin(source, root / 'runtime')

    def test_unverified_source_leaves_no_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'codex'; source.write_bytes(b'unverified'); source.chmod(0o700)
            with patch('pin_codex.sys.platform', 'darwin'), \
                 patch('pin_codex.version_of', side_effect=ValueError('unverified')):
                with self.assertRaises(ValueError):
                    pin(source, root / 'runtime')
            self.assertFalse((root / 'runtime').exists())
