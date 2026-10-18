#!/bin/sh
# Starts the tracker (call logger/grader) and the web fetcher/server in the background. Logs go to logs/.
# Reads optional settings from .env (copy .env.example). Note: .env is sourced as shell, so only put
# simple KEY=value lines in it and never use a .env file you got from someone else.
# The web server binds to 127.0.0.1 (this machine only) unless you override LIQMAP_HOST, which is risky.
cd "$(dirname "$0")" || exit 1; ROOT=$PWD
[ -f .env ] && { set -a; . ./.env; set +a; }
: "${LIQMAP_HOST:=127.0.0.1}"; export LIQMAP_HOST
case "$LIQMAP_HOST" in
  127.*|localhost|::1) ;;
  *) echo "WARNING: LIQMAP_HOST=$LIQMAP_HOST is not loopback; the map server will be reachable from other machines (risky)." ;;
esac
mkdir -p logs
pgrep -f "$ROOT/tracker/tracker.py" >/dev/null || nohup python3 "$ROOT/tracker/tracker.py" >> logs/tracker.log 2>&1 &
pgrep -f "$ROOT/web/fetcher.py" >/dev/null || nohup python3 "$ROOT/web/fetcher.py" >> logs/fetcher.log 2>&1 &
echo "tracker + fetcher running; open http://${LIQMAP_HOST}:${LIQMAP_PORT:-8787}/"
echo "stop with: pkill -f \"$ROOT/tracker/tracker.py\"; pkill -f \"$ROOT/web/fetcher.py\""
