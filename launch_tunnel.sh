#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# MCP_Documents — remote testing protocol (Cloudflare Quick Tunnel).
#
# Brings the local Docker deployment up and exposes it through an ephemeral
# *.trycloudflare.com URL — no account, no DNS, no config. One process serves
# both sub-servers under one port: /read/mcp and /edit/mcp.
#
# This makes the server reachable by ANY MCP-compatible harness or AI platform
# (Claude, ChatGPT custom connectors, LM Studio) without deploying to a VPS.
# Prefer this over remote_launch.sh where Docker exists: the image is the only
# thing that guarantees LibreOffice and Tesseract are present, so it is the
# only way convert(to='pdf') and ocr() are actually exercised.
#
# Usage:
#   ./launch_tunnel.sh              # docker compose up -d --build, then tunnel
#   SKIP_BUILD=1 ./launch_tunnel.sh # tunnel only
#   ./launch_tunnel.sh stop         # stop tunnels (leaves containers running)
#
# NOT for production. Quick Tunnels are unauthenticated at the transport level
# — set DOCS_API_KEY / DOCS_TOKENS_FILE in .env before running this so the
# exposed /mcp endpoints still require a bearer token.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# name:host_port pairs — one per compose service.
PORTS=(
  "documents:8850"
)
SUB_SERVERS=(read edit)

LOG_DIR="/tmp/docs-tunnels"
mkdir -p "$LOG_DIR"

if [ "${1:-}" = "stop" ]; then
  pkill -f "cloudflared tunnel --url http://localhost" 2>/dev/null && echo "tunnels stopped" || echo "no tunnels running"
  exit 0
fi

if ! command -v cloudflared &>/dev/null; then
  echo "[launch_tunnel] installing cloudflared..."
  curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
    -o /usr/local/bin/cloudflared
  chmod +x /usr/local/bin/cloudflared
fi

if [ "${SKIP_BUILD:-0}" != "1" ]; then
  echo "[launch_tunnel] docker compose up -d --build"
  docker compose up -d --build
fi

pkill -f "cloudflared tunnel --url http://localhost" 2>/dev/null || true
sleep 1

echo "[launch_tunnel] waiting for services to report healthy..."
for entry in "${PORTS[@]}"; do
  port="${entry##*:}"
  for _ in $(seq 1 60); do
    curl -fsS "http://localhost:${port}/health" >/dev/null 2>&1 && break
    sleep 1
  done
done

echo "[launch_tunnel] starting cloudflared quick tunnels..."
declare -A URLS
for entry in "${PORTS[@]}"; do
  name="${entry%%:*}"
  port="${entry##*:}"
  log="$LOG_DIR/${name}.log"
  : > "$log"
  nohup cloudflared tunnel --url "http://localhost:${port}" > "$log" 2>&1 &
done

echo "[launch_tunnel] waiting up to 30s per tunnel for a public URL..."
for entry in "${PORTS[@]}"; do
  name="${entry%%:*}"
  log="$LOG_DIR/${name}.log"
  url=""
  for _ in $(seq 1 30); do
    url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$log" 2>/dev/null | head -1 || true)
    [ -n "$url" ] && break
    sleep 1
  done
  URLS[$name]="${url:-<not found, check $log>}"
done

url="${URLS[documents]}"
echo ""
echo "  remote endpoints:"
for sub in "${SUB_SERVERS[@]}"; do
  echo "    docs-${sub}  ->  ${url}/${sub}/mcp"
done
echo ""
echo "  health checks:"
echo "    ${url}/health   (aggregate)"
for sub in "${SUB_SERVERS[@]}"; do
  echo "    ${url}/${sub}/health"
done
echo ""
# Without this, both the 401's WWW-Authenticate hint and the SDK's own
# /.well-known/oauth-protected-resource route fall back to the internal bind
# address, which no remote client can reach.
echo "  for an OAuth client, set DOCS_PUBLIC_URL=${url} and re-up, then:"
echo "    DOMAIN=${url} ./remote_smoke_test.sh"
echo ""
echo "  stop tunnels:  ./launch_tunnel.sh stop"
