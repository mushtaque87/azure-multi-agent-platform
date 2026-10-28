#!/usr/bin/env bash
# Build and run the agent + Phoenix (tracing UI) locally with Docker Compose.
# Secrets come from Key Vault (Foundry key) and `az` (short-lived SQL token) - nothing is stored on disk.
#   ./agent/run_agent.sh    # agent + chat UI: http://localhost:8000   traces: http://localhost:6006
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"

FOUNDRY_API_KEY="$(az keyvault secret show --vault-name kv-house-agents -n foundry-api-key --query value -o tsv)"
SQL_ACCESS_TOKEN="$(az account get-access-token --resource https://database.windows.net/ --query accessToken -o tsv)"
export FOUNDRY_API_KEY SQL_ACCESS_TOKEN  # compose passes these through by name (see docker-compose.yml)

docker rm -f house-agent >/dev/null 2>&1 || true  # container from the older docker-run version of this script
docker compose -f "$DIR/docker-compose.yml" up -d --build
echo "Chat UI: http://localhost:8000   Phoenix traces: http://localhost:6006"
