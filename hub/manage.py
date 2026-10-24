#!/usr/bin/env python3
"""Hub 运行时管理工具：无需重启即可增删 Agent、改显示名、查任务。

用法：
  python3 manage.py agents                  # 列出所有 Agent
  python3 manage.py rename <app_id> <新显示名>  # 改显示名（即时生效）
  python3 manage.py add-agent --json '{...}'   # 新增/覆盖一个 Agent
  python3 manage.py seed --config config.json  # 从配置文件整体灌入
  python3 manage.py devices                  # 列出设备
  python3 manage.py tasks [--status pending]  # 查任务
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from db import (  # noqa: E402
    _connect, init_db, list_agents, set_display_name,
    upsert_agent, upsert_device,
    release_device_execution,
)
from access import validate_agents

AGENT_KEYS = ("app_id", "device_id", "agent_name", "display_name", "engine_type",
              "exec_path", "app_secret", "work_dir", "session_id", "enabled")


def cmd_agents(_):
    for a in list_agents():
        print("%s  %s  device=%s  engine=%s  enabled=%s" % (
            a["app_id"], a["display_name"], a["device_id"], a["engine_type"], a["enabled"]))


def cmd_devices(_):
    conn = _connect()
    rows = conn.execute("SELECT device_id, name, token, created_at FROM devices").fetchall()
    conn.close()
    for r in rows:
        print("%s  %s  token=%s  %s" % (r["device_id"], r["name"], r["token"], r["created_at"]))


def cmd_rename(args):
    set_display_name(args.app_id, args.display_name)
    print("renamed %s -> %s" % (args.app_id, args.display_name))


def _agent_kwargs(payload):
    kwargs = {k: payload.get(k) for k in AGENT_KEYS}
    if kwargs.get("enabled") is None:
        kwargs["enabled"] = 1
    return kwargs


def cmd_add_agent(args):
    payload = json.loads(args.json)
    upsert_agent(**_agent_kwargs(payload))
    print("added %s" % payload["app_id"])


def cmd_seed(args):
    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    validate_agents([a for a in cfg.get("agents", []) if a.get("enabled", 1)])
    for d in cfg.get("devices", []):
        upsert_device(d["device_id"], d["name"], d["token"])
    for a in cfg.get("agents", []):
        upsert_agent(**_agent_kwargs(a))
    print("seeded %d agents" % len(cfg.get("agents", [])))


def cmd_validate(args):
    with open(args.config, encoding="utf-8") as handle:
        cfg = json.load(handle)
    validate_agents([a for a in cfg.get("agents", []) if a.get("enabled", 1)])
    print("ACCESS_CONFIG_OK agents=%d" % len(cfg.get("agents", [])))


def cmd_release(args):
    if not args.confirm_worker_stopped:
        raise SystemExit("须先停止 Node 和其 OpenCode 服务，再传 --confirm-worker-stopped；不能用于抢占运行任务")
    print("released reservations=%d" % release_device_execution(args.device_id))


def cmd_tasks(args):
    conn = _connect()
    sql = "SELECT task_id, app_id, device_id, status, substr(prompt,1,40) AS p, created_at FROM tasks"
    params = ()
    if args.status:
        sql += " WHERE status=?"
        params = (args.status,)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params = params + (args.limit,)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    for r in rows:
        print("%s  %-8s %s  %s  %s" % (r["task_id"], r["status"], r["app_id"], r["p"], r["created_at"]))


def main():
    parser = argparse.ArgumentParser(description="Hub 运行时管理工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("agents", help="列出所有 Agent").set_defaults(func=cmd_agents)
    sub.add_parser("devices", help="列出设备").set_defaults(func=cmd_devices)
    p_validate = sub.add_parser("validate-config", help="检查个人/团队权限与目录隔离")
    p_validate.add_argument("--config", default=str(Path(__file__).with_name("config.json")))
    p_validate.set_defaults(func=cmd_validate)
    p_release = sub.add_parser("release-device", help="执行器已停止后由本机管理员恢复占用")
    p_release.add_argument("device_id")
    p_release.add_argument("--confirm-worker-stopped", action="store_true")
    p_release.set_defaults(func=cmd_release)

    p_rename = sub.add_parser("rename", help="改显示名（即时生效）")
    p_rename.add_argument("app_id")
    p_rename.add_argument("display_name")
    p_rename.set_defaults(func=cmd_rename)

    p_add = sub.add_parser("add-agent", help="新增/覆盖 Agent")
    p_add.add_argument("--json", required=True, help="Agent 配置 JSON")
    p_add.set_defaults(func=cmd_add_agent)

    p_seed = sub.add_parser("seed", help="从配置文件整体灌入")
    p_seed.add_argument("--config", required=True, help="config.json 路径")
    p_seed.set_defaults(func=cmd_seed)

    p_tasks = sub.add_parser("tasks", help="查任务")
    p_tasks.add_argument("--status", help="按状态过滤")
    p_tasks.add_argument("--limit", type=int, default=20)
    p_tasks.set_defaults(func=cmd_tasks)

    args = parser.parse_args()
    if args.cmd != "validate-config":
        init_db()
    args.func(args)


if __name__ == "__main__":
    main()
