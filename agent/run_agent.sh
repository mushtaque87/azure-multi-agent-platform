#!/usr/bin/env bash
# Build and run the multi-agent stack + Phoenix locally with Docker Compose.
# Secrets come from Key Vault (Foundry key) and `az` (short-lived SQL token) - nothing secret is committed.
#   ./agent/run_agent.sh    # chat UI: http://localhost:8000   traces: http://localhost:6006
#
# SQL token: local containers have no managed identity, so they borrow YOUR identity via `az`. Tokens expire
# after ~60-90 min, so a background refresher rewrites agent/.tokens/sql_token every 2 min; the file is
# mounted read-only into the containers and db.py reads it on every connection (no restart needed).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
TOKEN_DIR="$DIR/.tokens"          # git-ignored
mkdir -p "$TOKEN_DIR"

refresh_token() {
  # Write to a temp file then rename, so a container never reads a half-written token
  az account get-access-token --resource https://database.windows.net/ --query accessToken -o tsv \
    > "$TOKEN_DIR/sql_token.tmp" && chmod 644 "$TOKEN_DIR/sql_token.tmp" && mv "$TOKEN_DIR/sql_token.tmp" "$TOKEN_DIR/sql_token"
}

# Home IPs change on every reconnect, and Azure SQL's firewall only admits listed IPs. Keep the `my-ip`
# rule in sync with this Mac's current public IP (local dev only - Azure apps use the Azure-services rule).
sync_firewall() {
  local ip rule
  ip="$(curl -s -m 10 https://api.ipify.org)" || return 0
  rule="$(az sql server firewall-rule show -g rg-house-agents -s sql-house-agents -n my-ip --query startIpAddress -o tsv 2>/dev/null)"
  if [ -n "$ip" ] && [ "$ip" != "$rule" ]; then
    az sql server firewall-rule update -g rg-house-agents -s sql-house-agents -n my-ip \
      --start-ip-address "$ip" --end-ip-address "$ip" -o none && echo "$(date) firewall my-ip: $rule -> $ip"
  fi
}

refresh_token
sync_firewall

# Background refresher (restarted on each run). Stop it with: kill "$(cat agent/.tokens/refresher.pid)"
# Every 2 min, not 30: `az` returns its CACHED token until shortly before expiry, and a sleeping Mac pauses
# `sleep` timers - frequent checks catch the moment az renews the token and recover within 2 min after wake.
if [ -f "$TOKEN_DIR/refresher.pid" ]; then kill "$(cat "$TOKEN_DIR/refresher.pid")" 2>/dev/null || true; fi
( while sleep 120; do refresh_token || echo "$(date) token refresh failed (az login expired?)" >&2; sync_firewall; done ) \
  > "$TOKEN_DIR/refresher.log" 2>&1 &
echo $! > "$TOKEN_DIR/refresher.pid"

FOUNDRY_API_KEY="$(az keyvault secret show --vault-name kv-house-agents -n foundry-api-key --query value -o tsv)"
export FOUNDRY_API_KEY  # compose passes it through by name (see docker-compose.yml)

docker rm -f house-agent >/dev/null 2>&1 || true  # container from the older single-agent version of this script
docker compose -f "$DIR/docker-compose.yml" up -d --build --force-recreate
echo "Chat UI (supervisor): http://localhost:8000   Agent Cards: :8001 and :8002/.well-known/agent-card.json   Phoenix: http://localhost:6006"
echo "SQL token auto-refresh: every 2 min (pid $(cat "$TOKEN_DIR/refresher.pid"))"
