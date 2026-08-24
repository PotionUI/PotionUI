#!/bin/bash
set -euo pipefail

BACKEND_PORT="${BACKEND_PORT:-8005}"
FRONTEND_PORT="${FRONTEND_PORT:-3001}"
LAN_MODE=false
POTIONUI_PROFILE=1

positional=()
for arg in "$@"; do
    if [[ "$arg" == "--lan" ]]; then
        LAN_MODE=true
    else
        positional+=("$arg")
    fi
done
if [[ ${#positional[@]} -ge 1 ]]; then
    BACKEND_PORT="${positional[0]}"
fi
if [[ ${#positional[@]} -ge 2 ]]; then
    FRONTEND_PORT="${positional[1]}"
fi

BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
    echo ""
    echo "Shutting down..."
    if [[ -n "$FRONTEND_PID" ]]; then kill "$FRONTEND_PID" 2>/dev/null || true; fi
    if [[ -n "$BACKEND_PID" ]]; then kill "$BACKEND_PID" 2>/dev/null || true; fi
    wait 2>/dev/null || true
    echo "Done."
    exit 0
}

trap cleanup SIGINT SIGTERM

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -f ./venv/bin/activate ]]; then
    echo "error: ./venv not found. Run ./potionui start first — it creates the venv and installs dependencies." >&2
    exit 1
fi
if [[ ! -d ./frontend/node_modules ]]; then
    echo "error: frontend/node_modules not found. Run ./potionui start first — it installs the frontend dependencies." >&2
    exit 1
fi

source ./venv/bin/activate

if [[ "$LAN_MODE" == true ]]; then
    BACKEND_HOST="0.0.0.0"
    VITE_HOST="true"
    export ALLOWED_ORIGINS="*"
    echo "=== LAN mode: accepting connections from local network ==="
else
    BACKEND_HOST="127.0.0.1"
    # Numeric loopback, not the DNS name "localhost": a resolver that returns
    # ::1 ahead of 127.0.0.1 makes Vite bind IPv6-only, which anything probing
    # the IPv4 loopback (e.g. ./potionui start) would never see as ready.
    VITE_HOST="127.0.0.1"
    export ALLOWED_ORIGINS="http://localhost:${FRONTEND_PORT},http://127.0.0.1:${FRONTEND_PORT}"
    echo "=== Localhost mode: accepting connections from localhost only ==="
fi

echo "Backend:  http://${BACKEND_HOST}:${BACKEND_PORT}"
echo "Frontend: http://${VITE_HOST}:${FRONTEND_PORT}"
echo ""

uvicorn api:app \
    --host "$BACKEND_HOST" \
    --port "$BACKEND_PORT" \
    --workers 1 \
    --log-level info \
    --limit-concurrency 1000 \
    --limit-max-requests 10000 \
    --timeout-keep-alive 30 \
    --timeout-graceful-shutdown 30 &
BACKEND_PID=$!

export BACKEND_PORT
export FRONTEND_PORT
export VITE_HOST
export POTIONUI_PROFILE

cd frontend
npm run dev &
FRONTEND_PID=$!
cd ..

wait
