#!/usr/bin/env bash
# Run an infra script inside the tools container (Python + ODBC Driver 18).
# Your Mac fetches a short-lived Entra token for Azure SQL with `az`; the container only sees that token.
#   ./infra/scripts/run_tools.sh sql_setup.py --load
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

docker build -q -t house-agents-tools -f "$ROOT/infra/docker/tools.Dockerfile" "$ROOT" >/dev/null
TOKEN="$(az account get-access-token --resource https://database.windows.net/ --query accessToken -o tsv)"

docker run --rm -e SQL_ACCESS_TOKEN="$TOKEN" -v "$ROOT:/work" -w /work/infra/scripts \
  house-agents-tools python "$@"
