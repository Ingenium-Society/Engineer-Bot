#!/usr/bin/env bash
# One command to run the game.
#
#   bash run.sh            tunnel mode: opens a cloudflared quick tunnel first
#   bash run.sh --lan      LAN mode: no tunnel, local IP + your own certificate
#   PORT=9000 bash run.sh  run on another port
#
# In tunnel mode the public URL is handed to the app as PUBLIC_URL, so the
# banner prints the addresses the team laptops should open. The tunnel is
# killed when this script exits, so Ctrl-C stops everything.

set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"
MODE="tunnel"

usage() {
  sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
}

for arg in "$@"; do
  case "$arg" in
    --lan) MODE="lan" ;;
    --tunnel) MODE="tunnel" ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $arg" >&2
      usage >&2
      exit 2
      ;;
  esac
done

TUNNEL_LOG=""
TUNNEL_PID=""

cleanup() {
  if [ -n "$TUNNEL_PID" ] && kill -0 "$TUNNEL_PID" 2>/dev/null; then
    kill "$TUNNEL_PID" 2>/dev/null || true
    wait "$TUNNEL_PID" 2>/dev/null || true
  fi
  if [ -n "$TUNNEL_LOG" ]; then
    rm -f "$TUNNEL_LOG"
  fi
}
trap cleanup EXIT INT TERM

if [ "$MODE" = "tunnel" ]; then
  if command -v cloudflared >/dev/null 2>&1; then
    TUNNEL_LOG="$(mktemp -t engineer-tunnel)"
    echo "Membuka tunnel untuk http://localhost:$PORT ..."
    cloudflared tunnel --url "http://localhost:$PORT" >"$TUNNEL_LOG" 2>&1 &
    TUNNEL_PID=$!

    PUBLIC_URL=""
    for _ in $(seq 1 40); do
      PUBLIC_URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" | head -1 || true)"
      if [ -n "$PUBLIC_URL" ]; then
        break
      fi
      if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
        break
      fi
      sleep 0.5
    done

    if [ -n "$PUBLIC_URL" ]; then
      export PUBLIC_URL
      echo "Tunnel siap: $PUBLIC_URL"
    else
      echo "Tunnel gagal dibuka — jatuh ke mode LAN." >&2
      kill "$TUNNEL_PID" 2>/dev/null || true
      TUNNEL_PID=""
      MODE="lan"
    fi
  else
    echo "cloudflared tidak terpasang — mode LAN." >&2
    MODE="lan"
  fi
fi

if [ "$MODE" = "lan" ]; then
  # LAN mode uses the local certificate; tunnel mode must not.
  echo "Mode LAN: laptop tim memakai alamat IP di banner."
fi

# No exec: the trap has to run so the tunnel dies with the server.
uv run python -m engineer_bot --port "$PORT"
