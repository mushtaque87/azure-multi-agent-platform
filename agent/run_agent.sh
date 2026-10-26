#!/usr/bin/env bash
# Build and run the agent locally in Docker.
# Secrets come from Key Vault (Foundry key) and `az` (short-lived SQL token) - nothing is stored on disk.
#   ./agent/run_agent.sh            # http://localhost:8000
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"

docker build -q -t house-agent "$DIR" >/dev/null
FOUNDRY_API_KEY="$(az keyvault secret show --vault-name kv-house-agents -n foundry-api-key --query value -o tsv)"
SQL_ACCESS_TOKEN="$(az account get-access-token --resource https://database.windows.net/ --query accessToken -o tsv)"
export FOUNDRY_API_KEY SQL_ACCESS_TOKEN

docker rm -f house-agent >/dev/null 2>&1 || true
# -e NAME (no value) passes the variable from this shell without putting it on the command line
docker run -d --name house-agent -p 8000:8000 -e FOUNDRY_API_KEY -e SQL_ACCESS_TOKEN \
  ${ML_ENDPOINT_URL:+-e ML_ENDPOINT_URL -e ML_ENDPOINT_KEY} house-agent >/dev/null
echo "Agent starting on http://localhost:8000 (logs: docker logs -f house-agent)"
