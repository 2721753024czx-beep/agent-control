"""Reversible workspace-local move used by mobile /trash commands."""
import json
import hashlib
import os
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath


PROTECTED_NAMES = {"AGENTS.md", "SKILL.md", "VERSION", "CHANGELOG.md"}


def _is_link_like(path):
    """Reject symlinks and Windows reparse points (including junctions)."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _source(work_dir, relative_path):
    root = Path(work_dir).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("invalid workspace")
    if not isinstance(relative_path, str) or not relative_path or "\\" in relative_path:
        raise ValueError("只接受以 / 分隔的工作区相对路径")
    parts = relative_path.split("/")
    candidate = Path(relative_path)
    if (candidate.is_absolute() or PureWindowsPath(relative_path).drive
            or len(relative_path) > 240
            or any(not part or part in (".", "..") or part.startswith(".")
                   or part.endswith((" ", ".")) or ":" in part
                   or any(ord(char) < 32 for char in part)
                   or PureWindowsPath(part).is_reserved() for part in parts)
            or parts[-1].casefold() in {name.casefold() for name in PROTECTED_NAMES}):
        raise ValueError("只接受工作区内的相对路径")
    source = root.joinpath(*parts)
    # Reject links at every component, including the leaf. A resolved target
    # within the root would still be an ambiguous path to move.
    cursor = root
    for part in parts:
        cursor = cursor / part
        if _is_link_like(cursor):
            raise ValueError("不允许移动符号链接、junction 或穿过这些目录")
    if not source.exists():
        raise FileNotFoundError("目标不存在：" + str(candidate))
    if not source.resolve(strict=True).is_relative_to(root):
        raise ValueError("路径逃逸工作区")

    return root, source


def prepare_trash(work_dir, relative_path):
    """Bounded content snapshot; nothing is created or moved before approval."""
    root, source = _source(work_dir, relative_path)
    digest = hashlib.sha256()
    files = total = entries = 0

    def scan(path):
        nonlocal files, total, entries
        entries += 1
        if entries > 2000 or _is_link_like(path):
            raise ValueError("清理目标过大或含链接，请缩小范围")
        info = path.stat()
        digest.update(json.dumps([str(path.relative_to(root)), info.st_mode,
                                  info.st_size, info.st_mtime_ns, info.st_ino],
                                 ensure_ascii=False).encode())
        if path.is_dir():
            for child in sorted(path.iterdir()):
                scan(child)
        elif path.is_file():
            files += 1
            total += info.st_size
            if total > 64 * 1024 * 1024:
                raise ValueError("清理目标超过64MiB，请缩小范围")
            read_bytes = 0
            with path.open('rb') as stream:
                while True:
                    data = stream.read(65536)
                    if not data:
                        break
                    read_bytes += len(data)
                    if read_bytes > info.st_size:
                        raise ValueError("清理目标正在变化，请重试")
                    digest.update(data)
        else:
            raise ValueError("不支持清理特殊文件")

    scan(source)
    return {"path": relative_path, "kind": "目录" if source.is_dir() else "文件",
            "files": files, "bytes": total, "sha256": digest.hexdigest()}


def move_to_trash(work_dir, relative_path, task_id=None, expected=None):
    if expected is not None and prepare_trash(work_dir, relative_path) != expected:
        raise ValueError("审批后目标已变化；未移动，请重新发起")
    root, source = _source(work_dir, relative_path)
    candidate = Path(relative_path)
    trash_root = root / ".agent-control-trash"
    if _is_link_like(trash_root):
        raise ValueError("暂存目录不能是符号链接或 junction")
    trash_root.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slot = trash_root / (stamp + "-" + uuid.uuid4().hex[:12])
    slot.mkdir(mode=0o700)
    target = slot / source.name
    manifest = {
        "original": str(candidate),
        "stored": str(target.relative_to(root)),
        "task_id": task_id,
        "moved_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = slot / "manifest.json"
    try:
        # Write the recovery pointer before moving the source. If the process
        # stops immediately after rename, the original location is still known.
        with open(manifest_path, "x", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.rename(source, target)
    except Exception:
        if target.exists() and not source.exists():
            os.rename(target, source)
        if not target.exists():
            manifest_path.unlink(missing_ok=True)
            slot.rmdir()
        raise
    return manifest
