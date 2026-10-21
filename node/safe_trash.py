"""Reversible workspace-local move used by mobile /trash commands."""
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path


def move_to_trash(work_dir, relative_path, task_id=None):
    root = Path(work_dir).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("invalid workspace")
    candidate = Path(relative_path)
    if (not relative_path or candidate.is_absolute() or candidate.drive
            or any(part in ("", ".", "..") for part in candidate.parts)
            or candidate.parts[0] == ".agent-control-trash"):
        raise ValueError("只接受工作区内的相对路径")
    source = root / candidate
    # Reject links at every component, including the leaf. A resolved target
    # within the root would still be an ambiguous path to move.
    cursor = root
    for part in candidate.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("不允许移动符号链接或穿过符号链接")
    if not source.exists():
        raise FileNotFoundError(str(candidate))
    if source.resolve(strict=True) == root:
        raise ValueError("不能移动工作区根目录")

    trash_root = root / ".agent-control-trash"
    if trash_root.is_symlink():
        raise ValueError("暂存目录不能是符号链接")
    trash_root.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slot = trash_root / (stamp + "-" + uuid.uuid4().hex[:12])
    slot.mkdir()
    target = slot / source.name
    try:
        os.rename(source, target)
        manifest = {
            "original": str(candidate),
            "stored": str(target.relative_to(root)),
            "task_id": task_id,
            "moved_at": datetime.now(timezone.utc).isoformat(),
        }
        (slot / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        if target.exists() and not source.exists():
            os.rename(target, source)
        try:
            slot.rmdir()
        except OSError:
            pass
        raise
    return manifest
