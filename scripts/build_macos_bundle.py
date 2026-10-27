#!/usr/bin/env python3
"""Build a macOS bundle from the same reviewed source allowlist."""
import argparse
import hashlib
from pathlib import Path
import zipfile
from build_windows_bundle import ROOT, selected_files


def build(output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    files = [p for p in selected_files() if not p.relative_to(ROOT).as_posix().startswith('scripts/windows/')]
    files += [p for p in (ROOT / 'scripts/macos').glob('*') if p.is_file() and p.suffix in {'.py', '.sh'}]
    files = sorted(set(files))
    manifest = []
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            rel = path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            archive.writestr('agent-control/' + rel, data)
            manifest.append(hashlib.sha256(data).hexdigest() + '  ' + rel)
        archive.writestr('agent-control/MANIFEST.sha256', '\n'.join(manifest) + '\n')
    return output, len(files)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT / 'dist/agent-control-macos.zip'))
    args = parser.parse_args()
    output, count = build(args.output)
    print('built %s with %d files' % (output, count))
