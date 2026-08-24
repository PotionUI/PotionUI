# Reference image for the Remote Native worker (`worker.py`,
# `docs/remote-native.md`) - what the runpod-provider plugin
# (`content/plugins/marketplace/runpod-provider/`) expects to find at whatever image
# reference its `worker_image` setting names. See `docker/README.md` for the
# "RunPod worker image" section: build/push instructions and why this is a
# reference image, not something the plugin builds or pushes for you.
#
# Base: python:3.12-slim, not an nvidia/cuda base image, deliberately -
# unlike `docker/Dockerfile.dev`'s rig-simulation harness (which wants a
# matching driver/CUDA userspace for its GPU passthrough), a RunPod Pod
# already provides the CUDA driver/userspace on the host; torch's own PyPI
# wheel carries its CUDA runtime as an ordinary pip dependency (see
# `constraints.txt`'s header - this is the same "not load-bearing for `import
# torch`" fact `docker/README.md` documents for Dockerfile.dev). Pinning
# through `constraints.txt` here is what actually matters: it locks the same
# known-good CUDA-closure version set the main app installs, so a worker pod
# and the process that dispatches to it agree on what "the same torch" means.
FROM python:3.12-slim

LABEL org.opencontainers.image.title="potionui-worker" \
      org.opencontainers.image.description="PotionUI Remote Native worker - runs worker.py, speaks worker protocol v1 over HTTP+SSE, no PotionUI database access."

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

# Same opencv-python/Pillow/kornia runtime libs `docker/Dockerfile.dev`
# installs (CLAUDE.md's documented "libgthread-2.0.so.0 missing" note names
# libglib2.0-0 as the fix) - the worker runs the same native pipes.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        git \
        build-essential \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender-dev \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && python3 --version

WORKDIR /app

# --- Backend dependencies -----------------------------------------------
# Same install contract as `docker/Dockerfile.dev` and `./potionui start`:
#   pip install -r requirements.txt -c constraints.txt
# `constraints.txt` pins the CUDA closure (torch/torchvision/xformers/...) to
# the maintainer's known-good versions - correct here too, not just for the
# main app: a GPU pod needs the exact same CUDA-capable wheels the native
# engine was validated against, and skipping `-c constraints.txt` would let
# pip resolve a torch build the rest of this image was never tested with.
# Copied separately from the rest of the source tree so this (multi-GB, slow)
# layer only rebuilds when the dependency set actually changes.
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt -c constraints.txt

# --- Application source ---------------------------------------------------
# Everything the worker's own composition root imports at boot or during a
# pipeline run: `src/` (bootstrap/platform/features/pipelines/plugin_api),
# `vendor/` (attributed third-party code the native pipes depend on - see
# CLAUDE.md's "Vendored code and licensing"), and `content/` (presets/ - a
# pipeline is reconstructed from a processed preset, not re-fetched;
# automation/; and plugins/ - a plugin whose manifest declares a `remote:
# true` hook must actually be importable here - see docs/remote-native.md
# and `src/pipelines/remote_fingerprint.py`'s worker-compatibility
# handshake). No `frontend/` - the worker serves no UI.
COPY src/ ./src/
COPY vendor/ ./vendor/
COPY content/ ./content/
COPY worker.py ./worker.py

# `POTIONUI_WORKER_PORT` (src/features/remote_execution/worker/config.py)
# defaults to 8100; a RunPod Pod created with a different `worker_port`
# overrides it via env, same as any other deployment target. EXPOSE is
# documentation for `docker run -p`/local testing - RunPod's HTTP proxy
# reaches the container by its actual bound port regardless.
EXPOSE 8100

# `POTIONUI_WORKER_TOKEN` is required and has no default (worker.py refuses
# to start without it) and `POTIONUI_WORKER_HOST` must be set to `0.0.0.0`
# for the RunPod proxy to reach it - both are supplied as Pod env vars by
# the runpod-provider plugin's `provision()` call, never baked into the
# image. See docs/remote-native.md's configuration table for the full list.
CMD ["python", "worker.py"]
