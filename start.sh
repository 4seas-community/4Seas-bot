#!/usr/bin/env bash
#
# Start the 4Seas bot.
#
#   ./start.sh              run in the foreground (Ctrl-C to stop)
#   ./start.sh --bg         run in the background, logging to data/bot.log
#   ./start.sh --stop       stop a background instance
#   ./start.sh --restart    stop cleanly, then start (avoids a 409 overlap)
#   ./start.sh --status     is it running, and what is it doing
#
# Telegram long polling allows exactly one consumer per token, so this refuses to
# start a second instance rather than letting two processes fight over updates.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

PY=".venv/bin/python"
LOG="data/bot.log"
PIDFILE="data/bot.pid"

red()  { printf '\033[31m%s\033[0m\n' "$*"; }
green(){ printf '\033[32m%s\033[0m\n' "$*"; }
dim()  { printf '\033[2m%s\033[0m\n' "$*"; }

running_pid() {
  # A stale pidfile after a crash would otherwise block every future start.
  [ -f "$PIDFILE" ] || return 1
  local pid; pid=$(cat "$PIDFILE" 2>/dev/null || true)
  [ -n "$pid" ] || { rm -f "$PIDFILE"; return 1; }
  kill -0 "$pid" 2>/dev/null || { rm -f "$PIDFILE"; return 1; }

  # PIDs get recycled. Without this check, a stale pidfile whose number has been
  # handed to some unrelated process means `--stop` kills that process instead.
  case "$(ps -p "$pid" -o command= 2>/dev/null || true)" in
    *"-m bot"*) echo "$pid"; return 0 ;;
    *) rm -f "$PIDFILE"; return 1 ;;
  esac
}

rotate_log() {
  # launchd and --bg both append; a week of running would otherwise grow without
  # bound. One generation back is enough to debug a crash.
  [ -f "$LOG" ] || return 0
  local size; size=$(wc -c < "$LOG" 2>/dev/null || echo 0)
  [ "$size" -gt 10485760 ] && mv "$LOG" "$LOG.1" || true
}

case "${1:-}" in
  --stop)
    if pid=$(running_pid); then
      kill "$pid" && rm -f "$PIDFILE"
      green "stopped (pid $pid)"
    else
      dim "not running"
    fi
    exit 0
    ;;
  --restart)
    # launchctl kickstart -k 会先起新进程、旧进程还在收尾，两者短暂重叠就撞
    # Telegram 的单消费者限制（409 Conflict）。先停干净再起。
    # 不要写成 `launchctl list | grep -q ...`：grep -q 一命中就关掉管道，
    # launchctl 收到 SIGPIPE 非零退出，pipefail 会把整条管道判为失败，
    # 于是这个分支永远进不去 —— 实测就是这样，结果起了第二个实例。
    if launchctl list com.4seas.bot >/dev/null 2>&1; then
      launchctl stop com.4seas.bot 2>/dev/null || true
      for _ in $(seq 1 20); do
        pgrep -f "$PWD/.venv/bin/python -m bot" >/dev/null 2>&1 || break
        sleep 0.5
      done
      launchctl start com.4seas.bot
      green "restarted via launchd (old instance drained first)"
    else
      "$0" --stop >/dev/null 2>&1 || true
      exec "$0" --bg
    fi
    exit 0
    ;;
  --status)
    if pid=$(running_pid); then
      green "running (pid $pid)"
      dim "  log:   tail -f $LOG"
      dim "  admin: see the 'Admin UI on' line in the log"
      [ -f "$LOG" ] && grep -a "Admin UI on\|定时任务已调度" "$LOG" | tail -2 || true
    else
      dim "not running"
    fi
    exit 0
    ;;
esac

# ── preflight ─────────────────────────────────────────────────────────
if [ ! -x "$PY" ]; then
  red "No virtualenv at $PY"
  echo "  uv venv --python 3.11 && uv pip install -e \".[dev]\""
  exit 1
fi

if [ ! -f .env ]; then
  red "No .env — copy the template and fill in your token:"
  echo "  cp .env.example .env && \$EDITOR .env"
  exit 1
fi

if pid=$(running_pid); then
  red "Already running (pid $pid)."
  echo "  Telegram allows one long-polling consumer per token; a second instance"
  echo "  would fight the first for updates. Use ./start.sh --stop first."
  exit 1
fi

mkdir -p data

# ── run ───────────────────────────────────────────────────────────────
if [ "${1:-}" = "--bg" ]; then
  rotate_log
  # Remember where this run's output starts. The log is appended to, and when
  # WEB_TOKEN is unset the admin token is regenerated per run — grepping the
  # whole file would hand back a link from a previous run that no longer works.
  mark=0; [ -f "$LOG" ] && mark=$(wc -c < "$LOG")

  nohup "$PY" -m bot >> "$LOG" 2>&1 &
  echo $! > "$PIDFILE"
  sleep 4
  if pid=$(running_pid); then
    green "started in the background (pid $pid)"
    dim "  log: tail -f $LOG"
    tail -c "+$((mark + 1))" "$LOG" | grep -a "Admin UI on" | tail -1 || true
  else
    red "died on startup — this run's output:"
    tail -c "+$((mark + 1))" "$LOG" | tail -20
    exit 1
  fi
else
  green "starting in the foreground — Ctrl-C to stop"
  exec "$PY" -m bot
fi
