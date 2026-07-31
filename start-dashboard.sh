#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

export PATH="$HOME/.local/bin:$PATH"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

PORT="${PORT:-8800}"
echo "▶ SMTInsider Dashboard: http://127.0.0.1:${PORT}"
echo "   LLM_MODEL=${LLM_MODEL:-не задан}  LLM_MOCK=${LLM_MOCK:-0}"
if [ -z "${NEON_DATABASE_URL:-}" ]; then
  echo "   DB: не задана — Publisher/YouTube Scout будут пропущены в pipeline"
else
  echo "   DB: задана; ALLOW_DB_WRITES=${ALLOW_DB_WRITES:-0}"
  if [ "${ALLOW_DB_WRITES:-0}" != "1" ]; then
    echo "   SAFE MODE: действия записи/approve/delete заблокированы"
  fi
fi

exec python3 -m uvicorn dashboard.app:app --host 0.0.0.0 --port "$PORT"
