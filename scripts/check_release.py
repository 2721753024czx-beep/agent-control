#!/usr/bin/env python3
"""Fail a release if staged source contains local secrets or lacks version notes."""
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SENSITIVE_KEYS = {"app_id", "app_secret", "admin_token", "token", "serve_password"}


def staged_paths():
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--cached", "-z"],
        check=True, capture_output=True,
    )
    return [Path(p.decode()) for p in result.stdout.split(b"\0") if p]


def staged_content(relative):
    result = subprocess.run(
        ["git", "-C", str(ROOT), "show", ":" + relative.as_posix()],
        check=True, capture_output=True,
    )
    return result.stdout


def live_secrets():
    values = set()
    for relative in ("hub/config.json", "node/config.json"):
        path = ROOT / relative
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))

        def collect(item):
            if isinstance(item, dict):
                for key, value in item.items():
                    if key in SENSITIVE_KEYS and isinstance(value, str) and len(value) >= 8:
                        values.add(value.encode())
                    collect(value)
            elif isinstance(item, list):
                for value in item:
                    collect(value)

        collect(data)
    return values


def forbidden_path(path):
    name = path.as_posix()
    return (
        name.startswith(("logs/", "history/", "runtime/", "backups/", "dist/", ".agent-memory/", ".agent-control-edits/", ".agent-control-trash/", ".agent-control-files/"))
        or name in {"hub/config.json", "node/config.json"}
        or name.startswith(("hub/config.json.", "node/config.json."))
        or name.startswith("hub/hub.db")
        or path.suffix in {".pem", ".key", ".pyc"}
    )


def main():
    paths = staged_paths()
    if not paths:
        raise SystemExit("没有暂存文件，无法校验发布版本")
    version = staged_content(Path("VERSION")).decode("utf-8").strip()
    changelog = staged_content(Path("CHANGELOG.md")).decode("utf-8")
    if not re.fullmatch(r"v\d{4}\.\d{2}\.\d{2}\.\d+", version):
        raise SystemExit("VERSION 格式应为 vYYYY.MM.DD.N")
    section = changelog.split("## " + version, 1)
    if len(section) < 2 or not all(
        heading in section[1].split("\n## ", 1)[0]
        for heading in ("### 本版完成", "### 相对上版的替换与迭代", "### 验证与未完成")
    ):
        raise SystemExit("CHANGELOG.md 缺少本版完成、替换/迭代或验证章节")
    secrets = live_secrets()
    failures = []
    for relative in paths:
        if forbidden_path(relative):
            failures.append("禁止上传的路径：" + relative.as_posix())
            continue
        data = staged_content(relative)
        if any(secret in data for secret in secrets):
            failures.append("包含本机配置值：" + relative.as_posix())
        if re.search(rb"-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----", data):
            failures.append("包含私钥：" + relative.as_posix())
    if failures:
        raise SystemExit("\n".join(failures))
    print("RELEASE_CHECK_OK version=%s files=%d" % (version, len(paths)))


if __name__ == "__main__":
    main()
