#!/usr/bin/env bash
# One-command Docker sandbox: build
# (if needed) the existing dev/rig-simulation image and run a completely
# fresh container so onboarding can be clicked through by hand on a "clean
# machine" - no leftover database, no leftover generated files, no leftover
# claimed owner account - every single run.
#
# This is NOT the fast iteration loop (that's
# tests/e2e/harness/onboarding_e2e.py, which drives the same journey over
# HTTP with no Docker involved at all).
# This is the slower, higher-fidelity check: the real image build, the real
# entrypoint (`./potionui doctor` -> `run.sh --lan`), a real browser tab.
#
# Usage:
#   ./docker/onboarding_sandbox.sh /path/to/models
#   POTIONUI_MODELS_DIR=/path/to/models ./docker/onboarding_sandbox.sh
#   POTIONUI_SANDBOX_PORT=9000 ./docker/onboarding_sandbox.sh /path/to/models
#
# What it does:
#   - builds docker/Dockerfile.dev as `potionui-dev:latest` on EVERY run, so
#     the sandbox always tests the current source (cached layers make
#     unchanged builds take seconds)
#   - runs a brand new container (a timestamped, never-reused name) with:
#       * the models directory bind-mounted READ-ONLY at /app/models
#       * ANONYMOUS volumes for /app/storage and /app/outputs - not the named
#         `potionui-storage`/`potionui-outputs` volumes docker-compose.yml
#         uses, specifically so nothing persists across runs
#       * the frontend published on a host port (default 8065) and the
#         backend API on the next port up (default 8066), for anyone who
#         wants to poke the API directly instead of the UI
#   - prints the URL to open, then streams the container's logs
#   - on Ctrl-C (or when the container exits on its own), `--rm` removes the
#     container AND its anonymous volumes - the next run starts from nothing,
#     same as a maintainer's first ever install
#
# Requires a real Docker daemon and (for a real generation) the
# nvidia-container-toolkit GPU runtime - see docker/README.md.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
IMAGE_NAME="potionui-dev:latest"
DOCKERFILE="$REPO_ROOT/docker/Dockerfile.dev"

MODELS_DIR="${POTIONUI_MODELS_DIR:-}"

for arg in "$@"; do
    case "$arg" in
        -h|--help)
            grep '^#' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            MODELS_DIR="$arg"
            ;;
    esac
done

if [[ -z "$MODELS_DIR" ]]; then
    echo "ERROR: no models directory given." >&2
    echo "Usage: $0 <models-dir>   (or set POTIONUI_MODELS_DIR)" >&2
    exit 2
fi
if [[ ! -d "$MODELS_DIR" ]]; then
    echo "ERROR: models directory does not exist: $MODELS_DIR" >&2
    exit 2
fi
MODELS_DIR="$(cd "$MODELS_DIR" && pwd)"  # absolute path - docker -v needs one

if ! command -v docker &>/dev/null; then
    echo "ERROR: docker is not on PATH. Install Docker first." >&2
    exit 2
fi
if ! docker info &>/dev/null; then
    echo "ERROR: could not reach the Docker daemon (\`docker info\` failed)." >&2
    echo "Is Docker running, and does this user have permission to use it?" >&2
    exit 2
fi

FRONTEND_PORT="${POTIONUI_SANDBOX_PORT:-8065}"
BACKEND_PORT="${POTIONUI_SANDBOX_BACKEND_PORT:-$((FRONTEND_PORT + 1))}"
CONTAINER_NAME="potionui-onboarding-sandbox-$(date +%s)-$$"

# The models depot is a runtime VOLUME - it must never end up in the image.
# Weight files (*.safetensors etc.) are excluded from the build context by
# docker/.dockerignore wherever they live, but warn loudly if the depot sits
# inside the repo so a surprise multi-GB "transferring context" is explained.
case "$MODELS_DIR" in
    "$REPO_ROOT"/*)
        echo "NOTE: your models directory is inside the repository. That's fine -"
        echo "      weight files are excluded from the image build and the depot is"
        echo "      attached as a read-only volume at runtime, never copied."
        ;;
esac

# Always build: docker's layer cache makes this take seconds when nothing
# changed, and it guarantees the sandbox always runs the CURRENT source -
# an iteration tool that silently reuses a stale image defeats its purpose
# (caught live: a recipe fix was invisible until the image was rebuilt).
echo "=== Building $IMAGE_NAME from $DOCKERFILE (cached layers make unchanged builds fast) ==="
docker build -f "$DOCKERFILE" -t "$IMAGE_NAME" "$REPO_ROOT"

echo ""
echo "================================================================"
echo " PotionUI onboarding sandbox - a completely fresh container"
echo "================================================================"
echo " Container:     $CONTAINER_NAME"
echo " Models (RO):   $MODELS_DIR -> /app/models"
echo " Storage/outputs: fresh anonymous volumes, discarded on exit"
echo ""
echo " Open once ready:  http://localhost:${FRONTEND_PORT}"
echo " Backend API:       http://localhost:${BACKEND_PORT}"
echo ""
echo " Ctrl-C stops and removes the container and its anonymous volumes -"
echo " the next run starts from a genuinely clean machine again."
echo "================================================================"
echo ""

# Best-effort "it's up" ping, running alongside the foregrounded `docker run`
# below so Ctrl-C still lands on the container (not this loop). Purely a
# convenience printout - `docker logs` from run.sh/vite already show the real
# readiness banners.
(
    frontend_up=false
    for _ in $(seq 1 180); do
        if [ "$frontend_up" = false ] && curl -fsS -o /dev/null "http://localhost:${FRONTEND_PORT}" 2>/dev/null; then
            frontend_up=true
            echo ""
            echo ">>> Frontend is up - backend still starting (first-boot migrations"
            echo ">>> take a while). Wait for the READY line before opening the app."
            echo ""
        fi
        # The BACKEND is what matters: opening the app before it answers used
        # to land on a misleading login page instead of the claim screen.
        if curl -fsS -o /dev/null "http://localhost:${BACKEND_PORT}/health" 2>/dev/null; then
            echo ""
            echo ">>> READY - open the app now: http://localhost:${FRONTEND_PORT}"
            echo ""
            break
        fi
        sleep 2
    done
) &
WATCHER_PID=$!
trap 'kill "$WATCHER_PID" 2>/dev/null || true' EXIT

# A models directory may contain symlinked subdirectories pointing elsewhere
# on the host (e.g. checkpoints -> /path/to/models/checkpoints). Those links
# would dangle inside the container, so every first-level symlink target is
# also mounted read-only at its own absolute path, letting the links resolve.
SYMLINK_MOUNTS=()
declare -A SEEN_TARGETS=()
for entry in "$MODELS_DIR"/*; do
    [ -L "$entry" ] || continue
    target="$(readlink -f "$entry")" || continue
    [ -e "$target" ] || continue
    if [ -z "${SEEN_TARGETS[$target]:-}" ]; then
        SEEN_TARGETS[$target]=1
        SYMLINK_MOUNTS+=(-v "${target}:${target}:ro")
        echo " Symlink (RO):  $(basename "$entry") -> $target"
    fi
done

docker run \
    --rm \
    --name "$CONTAINER_NAME" \
    --gpus all \
    -e HF_HUB_OFFLINE=0 \
    -e TRANSFORMERS_OFFLINE=0 \
    -p "${FRONTEND_PORT}:3001" \
    -p "${BACKEND_PORT}:8005" \
    -v "${MODELS_DIR}:/app/models:ro" \
    "${SYMLINK_MOUNTS[@]}" \
    -v "/app/storage" \
    -v "/app/outputs" \
    "$IMAGE_NAME"
