#!/usr/bin/env python3
"""Small cross-platform shared-memory helper with an exclusive file lock."""
import argparse
import os
import socket
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


FILES = ("index.md", "requirements.md", "context.md", "decisions.md")
TEMPLATES = {
    "index.md": "# 记忆索引\n\n- 先读本文件，再读 requirements.md / context.md；关键决策看 decisions.md。\n",
    "requirements.md": "# 核心需求\n\n## 待办\n\n- （记录当前任务的核心需求）\n\n## 已完成\n",
    "context.md": "# 核心上下文\n\n- 关键文件/路径：\n- 关键数据与结论：\n- 注意事项：\n",
    "decisions.md": "# 关键决策\n\n| 日期 | 决策 | 原因 |\n|------|------|------|\n",
}


@contextmanager
def locked(memory_dir):
    memory_dir.mkdir(parents=True, exist_ok=True)
    path = memory_dir / ".lock"
    with open(path, "a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def initialize(memory_dir):
    with locked(memory_dir):
        for name, content in TEMPLATES.items():
            path = memory_dir / name
            if not path.exists():
                path.write_text(content, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".", help="project root; defaults to current directory")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("read")
    append = sub.add_parser("append")
    append.add_argument("file", choices=FILES[1:])
    append.add_argument("title")
    append.add_argument("--text", default=None)
    args = parser.parse_args()
    memory_dir = Path(args.root).resolve() / ".agent-memory"

    if args.command == "init":
        initialize(memory_dir)
        print("initialized", memory_dir)
        return
    initialize(memory_dir)
    if args.command == "read":
        for name in FILES:
            path = memory_dir / name
            print("\n## " + name)
            print(path.read_text(encoding="utf-8"), end="")
        return

    body = args.text if args.text is not None else sys.stdin.read()
    body = body.strip()
    if not body:
        raise SystemExit("append content is empty")
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    host = socket.gethostname()
    section = "\n\n## %s [%s] %s\n%s\n" % (stamp, host, args.title, body)
    with locked(memory_dir):
        with open(memory_dir / args.file, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(section)
    print("appended", memory_dir / args.file)


if __name__ == "__main__":
    main()
