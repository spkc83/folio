#!/usr/bin/env bash
# Start Folio: API on port 8100, web app on port 3100.
set -euo pipefail
cd "$(dirname "$0")/.."

# Read .env when it exists. A variable that the shell already exports keeps its value.
# Write each line as KEY=value, with no quotes and no "export".
if [ -f .env ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in '' | \#*) continue ;; esac
    key=${line%%=*}
    [ -n "${!key+x}" ] || export "$line"
  done < .env
fi

.venv/bin/python -m uvicorn living_reports.api.app:create_app --factory --port "${LR_PORT:-8100}" &
api=$!
trap 'kill "$api" 2>/dev/null || true' EXIT

cd living_reports/web
[ -d node_modules ] || npm install --no-audit --no-fund
npx vite build --config vite.export.config.ts
npx vite --port 3100
