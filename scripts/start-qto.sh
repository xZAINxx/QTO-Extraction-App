#!/usr/bin/env bash
# =============================================================================
# Zeconic QTO — Production single-port launcher (mirrors CPM start-zeconic.sh)
# =============================================================================
# Builds the SPA bundle, then starts uvicorn serving both /api/* and the
# bundled frontend/dist on one port. Used for local production-shape
# testing; cloud deploys use the Dockerfile entrypoint instead.
#
# Usage:
#   bash scripts/start-qto.sh
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
FRONTEND_DIR="${REPO_ROOT}/frontend"
DIST_DIR="${FRONTEND_DIR}/dist"

PORT="${QTO_PROD_PORT:-8765}"
HOST="${QTO_PROD_HOST:-127.0.0.1}"
URL="http://${HOST}:${PORT}"

echo "============================================="
echo " Zeconic QTO — Production single-port mode"
echo "============================================="
echo " Repo : ${REPO_ROOT}"
echo " URL  : ${URL}"
echo ""

# Build the SPA if missing or out of date.
if [ ! -d "${DIST_DIR}" ] || [ "${FRONTEND_DIR}/src" -nt "${DIST_DIR}" ]; then
  echo "📦  Building frontend bundle…"
  (cd "${FRONTEND_DIR}" && npm install && npm run build)
fi
echo "✅  frontend/dist ready."

# Activate venv if present.
if [ -f "${REPO_ROOT}/venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/venv/bin/activate"
  echo "✅  venv active."
fi

# Apply migrations if a DATABASE_URL is set.
if [ -n "${DATABASE_URL:-}" ]; then
  echo "📦  alembic upgrade head…"
  alembic -c "${REPO_ROOT}/backend/alembic.ini" upgrade head
fi

# Boot uvicorn.
echo "🚀  uvicorn on ${URL}"
cd "${REPO_ROOT}"
exec uvicorn backend.main:app \
  --host "${HOST}" \
  --port "${PORT}" \
  --workers 1 \
  --log-level info
