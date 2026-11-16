#!/bin/bash
# Restart the dashboard daemon in the background (dev helper). Usage: tools/restart_daemon.sh [config]
cd "$(dirname "$0")/.."
CFG="${1:-$HOME/.config/wigix/dashboard.yaml}"
[ -f _work/daemon.pid ] && kill "$(cat _work/daemon.pid)" 2>/dev/null && sleep 1
mkdir -p _work
nohup .venv/bin/wigix -v run -c "$CFG" > _work/run.log 2>&1 &
echo $! > _work/daemon.pid
