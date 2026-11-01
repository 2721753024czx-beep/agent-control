#!/usr/bin/env python3
"""Copy a verified Mac Codex executable into a versioned local runtime.

Does not change Node configuration or restart services. Point Node at the
printed executable only after live verification of the copied runtime.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'node'))
from codex_engine import SUPPORTED_VERSIONS


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def version_of(path):
    result = subprocess.run([str(path), '--version'], capture_output=True,
                            text=True, timeout=15, check=True)
    version = result.stdout.strip()
    if version not in SUPPORTED_VERSIONS:
        raise ValueError('Codex版本未经适配器验证；先完成兼容性测试')
    return version


def pin(source, runtime):
    if sys.platform != 'darwin':
        raise ValueError('当前仅支持Mac Codex运行时')
    source = Path(source).resolve(strict=True)
    if not source.is_file() or not os.access(source, os.X_OK):
        raise ValueError('须提供可执行的Codex文件')
    version = version_of(source)
    checksum = digest(source)
    runtime = Path(runtime).resolve()
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    bundle = source.parents[2] if (source.parent.name == 'MacOS'
             and source.parent.parent.name == 'Contents'
             and source.parents[2].suffix == '.app') else None
    package = bundle.parent if bundle and (bundle.parent / 'codex-package.json').is_file() else None
    target = runtime / (version.removeprefix('codex-cli ') + '-' + checksum[:16]
                        + ('-package' if package else ''))
    relative = Path('CodexCLI.app/Contents/MacOS') / source.name if bundle else Path('codex')
    if package:
        relative = Path('package') / source.relative_to(package)
    executable = target / relative
    if target.exists():
        if target.is_symlink() or executable.is_symlink() or digest(executable) != checksum:
            raise ValueError('已保存运行时校验失败；拒绝覆盖，请在电脑端核查')
        version_of(executable)
        return executable
    with tempfile.TemporaryDirectory(prefix='.pin-', dir=runtime) as temporary:
        stage = Path(temporary) / 'runtime'
        stage.mkdir(mode=0o700)
        if package or bundle:
            # Preserve the signed app context, provisioning profile and xattrs.
            # Extracting just the Mach-O may cause macOS to kill it at launch.
            subprocess.run(['/usr/bin/ditto', '--rsrc', '--extattr', str(package or bundle),
                            str(stage / ('package' if package else 'CodexCLI.app'))],
                           check=True, timeout=120)
        else:
            shutil.copy2(source, stage / relative)
            (stage / relative).chmod(0o500)
        copied = stage / relative
        if digest(copied) != checksum or version_of(copied) != version:
            raise ValueError('源文件在复制期间改变；未安装')
        manifest = stage / 'manifest.json'
        manifest.write_text(json.dumps({'version': version, 'sha256': checksum}, indent=2) + '\n')
        manifest.chmod(0o600)
        # Never overwrite another version or an existing installation.
        stage.rename(target)
    return executable


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, help='absolute path to an already installed, verified Codex CLI')
    args = parser.parse_args()
    try:
        print(pin(args.source, ROOT / 'runtime/codex'))
    except Exception as exc:
        raise SystemExit('PIN_CODEX_FAILED: ' + str(exc))
