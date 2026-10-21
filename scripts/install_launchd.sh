#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ROOT_SED="$(printf '%s' "$ROOT" | sed 's/[&|\\]/\\&/g')"
LA="$HOME/Library/LaunchAgents"
LOG="$ROOT/logs"
mkdir -p "$LA" "$LOG"

echo "==> 停止已手动运行的 hub/node 进程"
pkill -f "$ROOT/hub/hub.py" 2>/dev/null || true
pkill -f "$ROOT/node/node.py" 2>/dev/null || true
pkill -f "hub\.py" 2>/dev/null || true
pkill -f "node\.py" 2>/dev/null || true
sleep 1

for name in hub node; do
  LABEL="com.agentcontrol.$name"
  SRC="$ROOT/scripts/com.agentcontrol.$name.plist"
  DST="$LA/$LABEL.plist"

  echo "==> 安装 $LABEL"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  sed "s|__AGENT_CONTROL_ROOT__|$ROOT_SED|g" "$SRC" > "$DST"
  launchctl bootstrap "gui/$(id -u)" "$DST"
done

echo "==> 状态"
for name in hub node; do
  launchctl print "gui/$(id -u)/com.agentcontrol.$name" 2>/dev/null | grep -E "state =|program =" | head -2
done

echo "==> 日志"
sleep 3
tail -3 "$LOG/hub.log" 2>/dev/null || true
echo "---"
tail -3 "$LOG/node.log" 2>/dev/null || true
