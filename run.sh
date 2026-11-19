#!/usr/bin/env bash
# simple-web-scraper control script.
#
#   ./run.sh start     start the resident in the background
#   ./run.sh stop      stop the resident and the browser worker
#   ./run.sh restart   stop then start
#   ./run.sh status    show resident + worker state
#   ./run.sh logs      follow the resident log (Ctrl-C to stop)
#   ./run.sh fg        run the resident in the foreground (Ctrl-C to stop)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PID_FILE="$ROOT/runtime/resident.pid"
OUT_LOG="$ROOT/runtime/logs/resident.out"
RESIDENT_LOG="$ROOT/runtime/logs/resident.log"

_running() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null)"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

start() {
  if _running; then
    echo "resident already running (pid $(cat "$PID_FILE"))"
    return 0
  fi
  mkdir -p "$ROOT/runtime/logs"
  echo "starting resident…"
  nohup uv run simple-web-scraper >>"$OUT_LOG" 2>&1 &
  echo "$!" >"$PID_FILE"
  sleep 1
  if _running; then
    echo "resident started (pid $(cat "$PID_FILE")) — logs: $RESIDENT_LOG"
  else
    echo "resident failed to start; see $OUT_LOG" >&2
    tail -n 20 "$OUT_LOG" >&2 2>/dev/null || true
    return 1
  fi
}

# Kill a pid gracefully (TERM, then KILL after a grace period).
_kill_pid() {
  local pid="$1"
  kill "$pid" 2>/dev/null || return 0
  for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || return 0; sleep 0.5; done
  kill -9 "$pid" 2>/dev/null || true
}

stop() {
  local pid=""
  [[ -f "$PID_FILE" ]] && pid="$(cat "$PID_FILE" 2>/dev/null)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "stopping resident (pid $pid)…"
    _kill_pid "$pid"
  else
    echo "resident not running via pid file"
  fi
  rm -f "$PID_FILE"
  # Stop the browser worker by its own pid file (precise — never matches
  # unrelated processes the way `pkill -f browser_daemon` would).
  local f dpid n=0
  for f in "$ROOT"/runtime/browser-*.pid; do
    [[ -e "$f" ]] || continue
    dpid="$(cat "$f" 2>/dev/null)"
    if [[ -n "$dpid" ]] && kill -0 "$dpid" 2>/dev/null; then
      echo "stopping worker (pid $dpid)…"; _kill_pid "$dpid"; n=$((n + 1))
    fi
    rm -f "$f"
  done
  [[ "$n" -gt 0 ]] && echo "stopped $n browser worker(s)"
  rm -f "$ROOT"/runtime/browser-*.port "$ROOT"/runtime/browser-*.lock 2>/dev/null || true
  echo "stopped."
}

status() {
  if _running; then
    echo "resident: RUNNING (pid $(cat "$PID_FILE"))"
  else
    echo "resident: stopped"
  fi
  local f dpid alive=0
  for f in "$ROOT"/runtime/browser-*.pid; do
    [[ -e "$f" ]] || continue
    dpid="$(cat "$f" 2>/dev/null)"
    if [[ -n "$dpid" ]] && kill -0 "$dpid" 2>/dev/null; then
      echo "browser worker: RUNNING (pid $dpid) [$(basename "$f" .pid)]"; alive=$((alive + 1))
    fi
  done
  # Plain `[[ ... ]] && echo` as the last statement would make `status` exit 1
  # whenever a worker IS alive — monitoring scripts read that as a failure.
  if [[ "$alive" -eq 0 ]]; then echo "browser worker: none"; fi
  return 0
}

logs() { mkdir -p "$ROOT/runtime/logs"; touch "$RESIDENT_LOG"; tail -n 50 -f "$RESIDENT_LOG"; }

fg_run() { exec uv run simple-web-scraper; }

usage() {
  cat <<EOF
simple-web-scraper control script.

Usage: $(basename "$0") <command>

Commands:
  start      start the resident in the background (pid -> runtime/resident.pid)
  stop       stop the resident and the browser worker, clean port/lock/pid files
  restart    stop then start
  status     show resident + browser worker state
  logs       follow the resident log (Ctrl-C to stop)
  fg         run the resident in the foreground (Ctrl-C to stop)
  help       show this help

Config is read from .env (copied from .env.example on first run).
EOF
}

case "${1:-}" in
  start)            start ;;
  stop)             stop ;;
  restart)          stop; start ;;
  status)           status ;;
  logs)             logs ;;
  fg)               fg_run ;;
  -h|--help|help)   usage ;;
  "")               usage >&2; exit 2 ;;
  *) echo "unknown command: $1" >&2; echo >&2; usage >&2; exit 2 ;;
esac
