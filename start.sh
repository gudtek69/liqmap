#!/bin/sh
# Starts the tracker (call logger/grader) and the web fetcher/server in the background. Logs go to logs/.
# Reads optional settings from .env (copy .env.example).
cd "$(dirname "$0")"; ROOT=$PWD
[ -f .env ] && { set -a; . ./.env; set +a; }
mkdir -p logs
pgrep -f "$ROOT/tracker/tracker.py" >/dev/null || nohup python3 "$ROOT/tracker/tracker.py" >> logs/tracker.log 2>&1 &
pgrep -f "$ROOT/web/fetcher.py" >/dev/null || nohup python3 "$ROOT/web/fetcher.py" >> logs/fetcher.log 2>&1 &
echo "tracker + fetcher running; open http://${LIQMAP_HOST:-127.0.0.1}:${LIQMAP_PORT:-8787}/"
echo "stop with: pkill -f \"$ROOT/tracker/tracker.py\"; pkill -f \"$ROOT/web/fetcher.py\""
