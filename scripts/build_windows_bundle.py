#!/usr/bin/env python3
"""Build a Windows deployment zip from an explicit, secret-free allowlist."""
import argparse
import hashlib
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TOP_FILES = ("README.md", "AGENTS.md", "VERSION", "CHANGELOG.md", "e2e_test.py")
PATTERNS = (
    "hub/*.py",
    "hub/requirements.txt",
    "hub/config.example.json",
    "hub/config.example.windows.json",
    "node/*.py",
    "node/requirements.txt",
    "node/config.example.json",
    "node/config.example.windows.json",
    "scripts/feishu_cli_setup.py",
    "scripts/history_loop.py",
    "scripts/run_safe_opencode.py",
    "scripts/configure_local.py",
    "scripts/doctor.py",
    "scripts/enroll_identity.py",
    "scripts/probe_native_engines.py",
    "scripts/verify_codex_adapter.py",
    "scripts/build_windows_bundle.py",
    "scripts/build_macos_bundle.py",
    "scripts/windows/*",
    "assets/windows/shared-memory/SKILL.md",
    "assets/windows/shared-memory/scripts/*.py",
    "docs/*.md",
    "tests/*.py",
)
FORBIDDEN_EXACT = {"hub/config.json", "node/config.json", "hub/hub.db"}


def selected_files():
    files = []
    for name in TOP_FILES:
        path = ROOT / name
        if path.is_file():
            files.append(path)
    for pattern in PATTERNS:
        files.extend(path for path in ROOT.glob(pattern) if path.is_file())
    unique = sorted(set(files), key=lambda path: path.as_posix())
    for path in unique:
        rel = path.relative_to(ROOT).as_posix()
        if rel in FORBIDDEN_EXACT or path.suffix in (".db", ".log"):
            raise RuntimeError("forbidden deployment file: " + rel)
    return unique


def build(output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = []
    files = selected_files()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            rel = path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            archive.writestr("agent-control/" + rel, data)
            manifest.append("%s  %s" % (hashlib.sha256(data).hexdigest(), rel))
        archive.writestr("agent-control/MANIFEST.sha256", "\n".join(manifest) + "\n")
    return output, len(files)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT / "dist" / "agent-control-windows.zip"))
    args = parser.parse_args()
    output, count = build(args.output)
    print("built %s with %d files" % (output, count))


if __name__ == "__main__":
    main()
