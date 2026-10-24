"""Explicit personal ownership and opt-in team access; no first-user takeover."""
import ntpath
from pathlib import Path


def policy(agent):
    mode = agent.get("access_mode", "personal")
    allowed = agent.get("allowed_open_ids")
    if (mode not in {"personal", "shared"} or not isinstance(allowed, list)
            or not allowed or any(not isinstance(x, str) or not x for x in allowed)):
        raise ValueError("须配置 personal/shared 模式及非空 allowed_open_ids")
    owner = agent.get("owner_open_id")
    if owner is None and mode == "personal" and len(allowed) == 1:
        owner = allowed[0]  # Backward-compatible single-owner deployment only.
    if not isinstance(owner, str) or owner not in allowed:
        raise ValueError("须明确配置白名单内的 owner_open_id；多人白名单不会自动开启共享")
    roles = agent.get("member_roles", {})
    if (not isinstance(roles, dict) or any(key not in allowed or value not in {"reader", "editor"}
                                          for key, value in roles.items())):
        raise ValueError("member_roles 只能为白名单成员配置 reader/editor")
    members = {owner: "editor"} if mode == "personal" else {
        user: ("editor" if user == owner else roles.get(user, "reader")) for user in allowed}
    return {"mode": mode, "owner": owner, "members": members}


def identity(path):
    if ntpath.splitdrive(str(path))[0]:
        return ntpath.normcase(ntpath.normpath(str(path))).replace("\\", "/").rstrip("/")
    return str(Path(path).resolve()).rstrip("/") or "/"


def validate_agents(agents):
    """Different logical agents on a device need non-overlapping project roots."""
    roots = []
    seen = set()
    for agent in agents:
        if agent["app_id"] in seen:
            raise ValueError("同一飞书 App ID 只能映射一个逻辑 Agent")
        seen.add(agent["app_id"])
        policy(agent)
        paths = [agent.get("work_dir")] + list((agent.get("workspaces") or {}).values())
        if not agent.get("work_dir"):
            raise ValueError("Agent 必须有独立工作目录")
        for path in paths:
            if not path:
                continue
            key = identity(path)
            for device, app, other in roots:
                if device == agent["device_id"] and app != agent["app_id"] and (
                        key == other or key.startswith(other.rstrip("/") + "/")
                        or other.startswith(key.rstrip("/") + "/")):
                    raise ValueError("同设备不同 Agent 的工作目录不能重叠；团队项目请配置一个 shared Agent")
            roots.append((agent["device_id"], agent["app_id"], key))
