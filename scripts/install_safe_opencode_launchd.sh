#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ROOT_SED="$(printf '%s' "$ROOT" | sed 's/[&|\\]/\\&/g')"
LABEL="com.agentcontrol.opencode"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/logs"
sed "s|__AGENT_CONTROL_ROOT__|$ROOT_SED|g" \
  "$ROOT/scripts/$LABEL.plist" > "$DEST"
plutil -lint "$DEST" >/dev/null
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"
launchctl print "gui/$(id -u)/$LABEL" | grep -E 'state =|pid =' | head -2
