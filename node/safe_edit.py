"""Single-file, reviewable text changes for the mobile /edit command."""
import difflib
import hashlib
import json
import os
import stat
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

from safe_trash import _is_link_like


MAX_BYTES = 32768
MAX_PREVIEW = 1800
TEXT_SUFFIXES = {".py", ".md", ".txt", ".js", ".jsx", ".ts", ".tsx", ".css", ".html"}
PROTECTED_NAMES = {"AGENTS.md", "SKILL.md", "VERSION", "CHANGELOG.md"}


def _target(work_dir, relative_path):
    root = Path(work_dir).resolve(strict=True)
    if not root.is_dir() or not isinstance(relative_path, str) or not relative_path or "\\" in relative_path:
        raise ValueError("只接受以 / 分隔的工作区相对文件路径")
    parts = relative_path.split("/")
    if (Path(relative_path).is_absolute() or PureWindowsPath(relative_path).drive
            or len(relative_path) > 240 or any(
                not part or part in (".", "..") or part.startswith(".")
                or part.endswith((" ", ".")) or ":" in part
                or any(ord(char) < 32 for char in part)
                or PureWindowsPath(part).is_reserved()
                for part in parts)
            or parts[-1].casefold() in {name.casefold() for name in PROTECTED_NAMES}
            or Path(parts[-1]).suffix.lower() not in TEXT_SUFFIXES):
        raise ValueError("路径或文件类型不允许远程编辑")
    cursor = root
    for part in parts:
        cursor = cursor / part
        if _is_link_like(cursor):
            raise ValueError("不允许编辑符号链接、junction 或穿过这些目录")
    target = root.joinpath(*parts)
    if not target.parent.is_dir() or not target.parent.resolve(strict=True).is_relative_to(root):
        raise ValueError("父目录不存在或路径逃逸工作区")
    if target.exists():
        info = target.lstat()
        if not stat.S_ISREG(info.st_mode) or not target.resolve(strict=True).is_relative_to(root):
            raise ValueError("只能编辑工作区内的普通文件")
    return root, target


def _read(target):
    if not target.exists():
        return None
    if target.stat().st_size > MAX_BYTES:
        raise ValueError("文件超过远程编辑大小限制")
    data = target.read_bytes()
    if len(data) > MAX_BYTES or b"\x00" in data:
        raise ValueError("只允许不超过 32 KiB 的 UTF-8 文本")
    return data.decode("utf-8")


def snapshot(work_dir, relative_path):
    root, target = _target(work_dir, relative_path)
    original = _read(target)
    return {"root": root, "path": relative_path, "original": original,
            "sha256": hashlib.sha256((original or "").encode("utf-8")).hexdigest()}


def parse_proposal(response):
    text = response.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("content"), str):
        raise ValueError("模型未返回含 content 字段的 JSON 草案")
    content = parsed["content"]
    if not content or "\x00" in content or len(content.encode("utf-8")) > MAX_BYTES:
        raise ValueError("草案为空、含 NUL 或超过 32 KiB")
    return content


def preview(change, content):
    root, target = _target(change["root"], change["path"])
    if root != change["root"] or _read(target) != change["original"]:
        raise RuntimeError("文件在草案生成期间发生变化，拒绝覆盖")
    original = change["original"] or ""
    if original == content:
        raise ValueError("草案没有产生变更")
    diff = "".join(difflib.unified_diff(
        original.splitlines(keepends=True), content.splitlines(keepends=True),
        fromfile="a/" + change["path"], tofile="b/" + change["path"],
    ))
    if len(diff) > MAX_PREVIEW or not diff:
        raise ValueError("完整差异超过 1800 字，须拆分成更小的编辑")
    return diff


def apply_approved(change, content, task_id):
    root, target = _target(change["root"], change["path"])
    original = _read(target)
    if root != change["root"] or original != change["original"]:
        raise RuntimeError("审批期间文件发生变化，拒绝覆盖")
    backup_root = root / ".agent-control-edits"
    if _is_link_like(backup_root):
        raise ValueError("备份目录不能是符号链接或 junction")
    backup_root.mkdir(mode=0o700, exist_ok=True)
    slot = backup_root / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12])
    slot.mkdir(mode=0o700)
    if original is not None:
        with open(slot / "original", "xb") as handle:
            handle.write(original.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
    manifest = {"original": change["path"], "backup": str(slot.relative_to(root)),
                "task_id": task_id, "sha256_before": change["sha256"],
                "created_at": datetime.now(timezone.utc).isoformat()}
    with open(slot / "manifest.json", "x", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    fd, temporary = tempfile.mkstemp(prefix=".agent-control-edit-", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        if original is None:
            # Hard-link creation fails if another process created the target.
            os.link(temporary, target)
        else:
            os.chmod(temporary, stat.S_IMODE(target.stat().st_mode))
            if _read(target) != original:
                raise RuntimeError("写入前文件再次变化，拒绝覆盖")
            os.replace(temporary, target)
        return manifest
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
