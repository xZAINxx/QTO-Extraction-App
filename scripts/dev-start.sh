#!/usr/bin/env bash
# =============================================================================
# Zeconic QTO — Dev Launcher
# =============================================================================
# Mirrors /Users/zain/CPM_APP/scripts/start-zeconic.sh, adapted for the
# two-process dev workflow (Vite hot reload on :5173 + FastAPI auto-reload
# on :8000). For production single-port mode, run:
#   cd frontend && npm run build
#   uvicorn backend.main:app --host 127.0.0.1 --port 8765
#
# Usage:
#   bash scripts/dev-start.sh
#
# What this does:
#   1. Resolves repo root regardless of cwd.
#   2. Activates ./venv if present; otherwise uses system python3.
#   3. Ensures backend deps are installed (pip install -r backend/requirements.txt).
#   4. Ensures frontend deps are installed (npm install in frontend/).
#   5. Boots uvicorn on :8000 (with --reload).
#   6. Boots Vite on :5173 (proxies /api → :8000).
#   7. Streams both logs side-by-side; Ctrl+C tears both down cleanly.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_DIR="${REPO_ROOT}/backend"
FRONTEND_DIR="${REPO_ROOT}/frontend"
VENV_DIR="${REPO_ROOT}/venv"

# Ports default to QTO-specific values that don't clash with the CPM dev
# stack (which uses 8000 + multiple Vite instances on 5173/5174). Override
# with env vars if needed:
#   QTO_BACKEND_PORT=9000 QTO_FRONTEND_PORT=5500 bash scripts/dev-start.sh
BACKEND_PORT="${QTO_BACKEND_PORT:-8042}"
FRONTEND_PORT="${QTO_FRONTEND_PORT:-5142}"
LOG_DIR="${REPO_ROOT}/.dev-logs"
mkdir -p "${LOG_DIR}"

echo "============================================="
echo " Zeconic QTO — Dev Mode"
echo "============================================="
echo " Repo       : ${REPO_ROOT}"
echo " Backend    : http://127.0.0.1:${BACKEND_PORT}"
echo " Frontend   : http://127.0.0.1:${FRONTEND_PORT}  ← open this"
echo " Logs       : ${LOG_DIR}/{backend,frontend}.log"
echo ""

# ── Activate venv if present ─────────────────────────────────────────────────
if [ -f "${VENV_DIR}/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "${VENV_DIR}/bin/activate"
  echo "✅  venv active : ${VENV_DIR}"
else
  echo "ℹ️   No venv at ${VENV_DIR} — using system python3."
  echo "    To create one:"
  echo "      python3 -m venv venv"
  echo "      ./venv/bin/pip install -r requirements.txt"
fi

# ── Install backend deps if FastAPI is missing ──────────────────────────────
if ! python3 -c "import fastapi" >/dev/null 2>&1; then
  echo "📦  Installing backend deps from ${BACKEND_DIR}/requirements.txt …"
  pip install -r "${BACKEND_DIR}/requirements.txt"
fi

# ── Install frontend deps if node_modules is missing ────────────────────────
if [ ! -d "${FRONTEND_DIR}/node_modules" ]; then
  echo "📦  Installing frontend deps via npm install …"
  (cd "${FRONTEND_DIR}" && npm install)
fi

# ── Port collision guard ────────────────────────────────────────────────────
for PORT in "${BACKEND_PORT}" "${FRONTEND_PORT}"; do
  if lsof -i "TCP:${PORT}" -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "❌  Port ${PORT} is already in use."
    echo "    Free it with: lsof -ti:${PORT} | xargs kill"
    exit 1
  fi
done

# ── Cleanup: kill children when this script exits ──────────────────────────
PIDS=()
cleanup() {
  echo ""
  echo "🛑  Shutting down dev servers …"
  for pid in "${PIDS[@]:-}"; do
    if kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
  echo "    Done."
}
trap cleanup EXIT INT TERM

# ── Boot uvicorn ────────────────────────────────────────────────────────────
echo "🚀  Starting FastAPI (uvicorn --reload) on :${BACKEND_PORT} …"
(
  cd "${REPO_ROOT}"
  APP_ENV=development uvicorn backend.main:app \
    --host 127.0.0.1 \
    --port "${BACKEND_PORT}" \
    --reload \
    --log-level info \
    > "${LOG_DIR}/backend.log" 2>&1
) &
PIDS+=($!)

# Wait for the health endpoint to come up before starting Vite — saves the
# user a confusing "fetch failed" toast on first paint.
echo "    Waiting for /api/health …"
for _ in $(seq 1 25); do
  if curl -sf "http://127.0.0.1:${BACKEND_PORT}/api/health" >/dev/null 2>&1; then
    echo "✅  Backend ready."
    break
  fi
  sleep 0.4
done

# ── Boot Vite ───────────────────────────────────────────────────────────────
echo "🚀  Starting Vite on :${FRONTEND_PORT} …"
(
  cd "${FRONTEND_DIR}"
  VITE_API_URL="http://127.0.0.1:${BACKEND_PORT}" npm run dev -- \
    --port "${FRONTEND_PORT}" \
    --strictPort \
    > "${LOG_DIR}/frontend.log" 2>&1
) &
PIDS+=($!)

# Wait for Vite then auto-open the browser. ``open`` is macOS-only;
# Linux falls through to xdg-open if available.
echo "    Waiting for Vite …"
for _ in $(seq 1 25); do
  if curl -sf "http://127.0.0.1:${FRONTEND_PORT}/" >/dev/null 2>&1; then
    echo "✅  Frontend ready."
    if command -v open >/dev/null 2>&1; then
      open "http://127.0.0.1:${FRONTEND_PORT}/"
    elif command -v xdg-open >/dev/null 2>&1; then
      xdg-open "http://127.0.0.1:${FRONTEND_PORT}/" >/dev/null 2>&1 || true
    fi
    break
  fi
  sleep 0.4
done

echo ""
echo "============================================="
echo " Dev servers up. Tail logs to follow output:"
echo "   tail -f ${LOG_DIR}/backend.log"
echo "   tail -f ${LOG_DIR}/frontend.log"
echo " Ctrl+C here tears both down."
echo "============================================="

# Keep the script alive so the trap fires on Ctrl+C.
wait
