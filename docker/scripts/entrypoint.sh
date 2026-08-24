#!/usr/bin/env bash
# Entrypoint for docker/Dockerfile.dev (rig-simulation harness).
#
# Deliberately mirrors what a human does on a fresh checkout: run the doctor
# checks so a misconfigured container fails LOUDLY and visibly (never a
# silent hang), then launch backend + frontend the same way `run.sh` does for
# every other local dev session. No shortcuts: nothing here disables a
# doctor check, fakes readiness, or swaps in different process-supervision
# logic than what a bare-metal contributor gets.
#
# `run.sh --lan` (not `./potionui start`) is the launcher because
# `./potionui start` binds the backend to 127.0.0.1 and detaches its
# children before exiting (correct for an interactive shell where a human
# runs `./potionui stop` later - wrong for a container's PID 1, which must
# stay in the foreground and own signal handling). `run.sh --lan` binds
# 0.0.0.0 (see BACKEND_HOST/VITE_HOST in run.sh) so the compose-published
# ports are reachable from the host, and already `trap`s SIGINT/SIGTERM into
# a clean shutdown of both child processes, which is exactly what `docker
# stop` needs.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

echo "================================================================"
echo " PotionUI rig-simulation container - environment check"
echo "================================================================"
./potionui doctor
doctor_status=$?
if [[ "$doctor_status" -ne 0 ]]; then
    echo ""
    echo "doctor reported blocking issue(s) above. Continuing to launch anyway -"
    echo "run.sh will fail loudly (and the container will exit non-zero) if a"
    echo "blocking problem actually stops the backend/frontend from starting."
fi

if [[ -n "${POTIONUI_VRAM_CAP_GB:-}" ]]; then
    echo ""
    echo "*** POTIONUI_VRAM_CAP_GB=${POTIONUI_VRAM_CAP_GB} - VRAM will be capped for"
    echo "*** rig simulation. Debug-only knob - never mistake this run's numbers"
    echo "*** for a real card. See src/platform/runtime/vram_cap.py."
fi

echo "================================================================"
echo " Starting PotionUI (run.sh --lan: binds 0.0.0.0 so the container's"
echo " published ports are reachable from the host)"
echo "================================================================"

exec ./run.sh "${BACKEND_PORT:-8005}" "${FRONTEND_PORT:-3001}" --lan
