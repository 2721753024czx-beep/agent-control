#!/usr/bin/env python3
"""飞书 lark-cli 自动化配置脚本。

用法：
  python3 scripts/feishu_cli_setup.py bind    # 从 hub/config.json 为每个 Agent 绑定命名配置
  python3 scripts/feishu_cli_setup.py send    # 发送测试消息（需先 auth login）
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "hub" / "config.json"


def load_config():
    path = CONFIG_PATH if CONFIG_PATH.exists() else ROOT / "hub" / "config.example.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def is_placeholder(value):
    return not value or any(mark in value for mark in ("your_", "change-me", "xxxx"))


def lark_command(args):
    executable = shutil.which("lark-cli")
    if not executable:
        return None
    command = [executable] + list(args)
    if os.name == "nt" and executable.lower().endswith((".cmd", ".bat")):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c",
                subprocess.list2cmdline(command)]
    return command


def ensure_cli():
    command = lark_command(["--version"])
    if command and subprocess.run(command, capture_output=True).returncode == 0:
        return
    if os.name == "nt":
        raise RuntimeError("lark-cli 未安装或不可执行，请先确认 lark-cli --version 正常")
    else:
        print("lark-cli 未安装，正在安装 npx @larksuite/cli@latest install ...")
        subprocess.run(["npx", "--yes", "@larksuite/cli@latest", "install"], check=True)


def cmd_bind(_args):
    cfg = load_config()
    ensure_cli()
    bound = 0
    for a in cfg.get("agents", []):
        if is_placeholder(a.get("app_id")) or is_placeholder(a.get("app_secret")):
            print("[skip] %s 仍是占位值，请先填 hub/config.json 的真实 App ID/Secret" % a.get("agent_name", "?"))
            continue
        profile = a.get("agent_name") or a["app_id"]
        print("[bind] profile=%s app_id=%s" % (profile, a["app_id"]))
        command = lark_command(
            ["config", "init", "--app-id", a["app_id"],
             "--app-secret-stdin", "--name", profile, "--brand", "feishu"])
        proc = subprocess.run(
            command,
            input=a["app_secret"],
            capture_output=True, text=True)
        if proc.returncode != 0:
            print(proc.stderr)
            sys.exit(1)
        bound += 1
        print("       " + (proc.stdout or "").strip().splitlines()[0] if proc.stdout else "")
    print("\n完成，下一步对每个 profile 做一次浏览器登录：")
    for a in cfg.get("agents", []):
        profile = a.get("agent_name") or a["app_id"]
        print("  lark-cli auth login --profile %s --recommend" % profile)
    return bound


def cmd_send(args):
    cfg = load_config()
    ensure_cli()
    profile = args.profile
    cmd = ["im", "+messages-send", "--chat-id", args.chat_id, "--text", args.text]
    if profile:
        cmd[0:0] = ["--profile", profile]
    command = lark_command(cmd)
    print("运行:", " ".join(cmd))
    proc = subprocess.run(command, capture_output=True, text=True)
    print(proc.stdout)
    if proc.returncode != 0:
        print(proc.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="飞书 lark-cli 自动化配置")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_bind = sub.add_parser("bind", help="从 hub/config.json 为每个 Agent 绑定命名配置")
    p_bind.set_defaults(func=cmd_bind)

    p_send = sub.add_parser("send", help="发送测试消息")
    p_send.add_argument("--profile", help="命名配置名（不填用默认）")
    p_send.add_argument("--chat-id", required=True, help="聊天 ID oc_xxx")
    p_send.add_argument("--text", required=True, help="消息内容")
    p_send.set_defaults(func=cmd_send)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
