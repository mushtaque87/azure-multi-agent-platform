#!/usr/bin/env bash
# Build and run the agent + Phoenix (tracing UI) locally with Docker Compose.
# Secrets come from Key Vault (Foundry key) and `az` (short-lived SQL token) - nothing is stored on disk.
#   ./agent/run_agent.sh    # agent + chat UI: http://localhost:8000   traces: http://localhost:6006
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"

FOUNDRY_API_KEY="$(az keyvault secret show --vault-name kv-house-agents -n foundry-api-key --query value -o tsv)"
SQL_ACCESS_TOKEN="$(az account get-access-token --resource https://database.windows.net/ --query accessToken -o tsv)"
export FOUNDRY_API_KEY SQL_ACCESS_TOKEN  # compose passes these through by name (see docker-compose.yml)

docker rm -f house-agent >/dev/null 2>&1 || true  # container from the older single-agent version of this script
# --force-recreate: the SQL token expires after ~1 hour, so containers must always start with the fresh one
# (local dev only - in Azure the managed identity refreshes tokens automatically)
docker compose -f "$DIR/docker-compose.yml" up -d --build --force-recreate
echo "Chat UI (supervisor): http://localhost:8000   Agent Cards: :8001 and :8002/.well-known/agent-card.json   Phoenix: http://localhost:6006"
