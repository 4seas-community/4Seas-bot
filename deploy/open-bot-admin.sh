#!/usr/bin/env bash
set -euo pipefail

readonly local_port="${BOT_ADMIN_LOCAL_PORT:-8477}"
readonly remote_host="${BOT_ADMIN_SSH_HOST:-}"
readonly remote_user="${BOT_ADMIN_SSH_USER:-$(id -un)}"

if [[ -z "${remote_host}" ]]; then
  printf 'Set BOT_ADMIN_SSH_HOST to the host running the bot, e.g.\n' >&2
  printf '  BOT_ADMIN_SSH_HOST=bot.internal %s\n' "$0" >&2
  printf '\nThis relay is only needed when the host disables SSH TCP forwarding.\n' >&2
  printf 'On a tailnet, bind the console to the host address instead —\n' >&2
  printf 'see docs/HOST-MIGRATION.md.\n' >&2
  exit 2
fi
readonly admin_url="http://127.0.0.1:${local_port}/"
readonly script_path="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"

ssh_relay() {
  exec ssh \
    -T \
    -o BatchMode=yes \
    -o IdentitiesOnly=yes \
    -o IdentityFile="${BOT_ADMIN_SSH_KEY:-${HOME}/.ssh/id_rsa}" \
    -o StrictHostKeyChecking=yes \
    -o ConnectTimeout=10 \
    -o ServerAliveInterval=15 \
    -o ServerAliveCountMax=2 \
    "${remote_user}@${remote_host}" \
    "exec /usr/bin/nc 127.0.0.1 8477"
}

if [[ "${1:-}" == "--connect" ]]; then
  ssh_relay
fi

if [[ ! "${local_port}" =~ ^[0-9]+$ ]] || ((local_port < 1024 || local_port > 65535)); then
  printf 'BOT_ADMIN_LOCAL_PORT must be a number from 1024 to 65535.\n' >&2
  exit 1
fi

if ! command -v socat >/dev/null 2>&1; then
  printf 'socat is required. Install it with: brew install socat\n' >&2
  exit 1
fi

if lsof -nP -iTCP:"${local_port}" -sTCP:LISTEN >/dev/null 2>&1; then
  printf 'Local port %s is already in use.\n' "${local_port}" >&2
  printf 'Try: BOT_ADMIN_LOCAL_PORT=18477 %s\n' "$0" >&2
  exit 1
fi

socat \
  "TCP4-LISTEN:${local_port},bind=127.0.0.1,reuseaddr,fork" \
  "EXEC:${script_path} --connect,nofork" &
relay_pid=$!

cleanup() {
  kill "${relay_pid}" 2>/dev/null || true
  wait "${relay_pid}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for _ in {1..20}; do
  if curl -sS --connect-timeout 2 --max-time 4 -o /dev/null "${admin_url}" 2>/dev/null; then
    if [[ "${BOT_ADMIN_NO_OPEN:-0}" != "1" ]]; then
      open "${admin_url}"
    fi
    printf 'Bot admin relay is open at %s\n' "${admin_url}"
    printf 'Keep this terminal open; press Ctrl-C to close the relay.\n'
    wait "${relay_pid}"
    exit $?
  fi
  sleep 0.25
done

printf 'SSH relay opened, but the admin page did not become ready.\n' >&2
exit 1
