# PotionUI distribution image.
#
# The single-process production deployment: the backend serves the built
# SvelteKit SPA itself (src/bootstrap/static_frontend.py), so the container
# runs exactly one process (`python api.py`) on exactly one port (8005).
# Unlike Dockerfile.dev — which runs the dev servers under `run.sh` to
# simulate a contributor's machine — this image ships a prebuilt frontend
# and no node runtime at all.
#
# Build (from the repo root):
#   docker build -f docker/Dockerfile -t potionui .
#
# Run (same GPU story as Dockerfile.dev: nvidia-container-toolkit, --gpus all):
#   docker run --gpus all -p 8005:8005 \
#     -v potionui-models:/app/models \
#     -v potionui-storage:/app/storage \
#     -v potionui-outputs:/app/outputs \
#     ghcr.io/potionui/potionui:latest
#
# CI builds and publishes this to GHCR on version tags — see
# .github/workflows/docker-publish.yml.
#
# Base image: matches the CUDA userspace `constraints.txt` pins (see
# Dockerfile.dev's header for the full rationale — torch's PyPI wheel carries
# its own CUDA runtime; the base exists so nvidia-container-toolkit mounts a
# driver userspace consistent with a bare-metal install of the same CUDA).

# --- Frontend build stage -------------------------------------------------
# Node lives only here; the final image never gets a node runtime.
FROM node:20-bookworm-slim AS frontend-builder

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Runtime image --------------------------------------------------------
FROM nvidia/cuda:13.0.2-runtime-ubuntu24.04

LABEL org.opencontainers.image.title="PotionUI" \
      org.opencontainers.image.description="Self-hosted AI image and video generation. Single-process distribution image: backend + built frontend on one port." \
      org.opencontainers.image.source="https://github.com/PotionUI/PotionUI" \
      org.opencontainers.image.licenses="GPL-3.0"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

# Ubuntu 24.04's system python3 is 3.12 (potionui's MIN_PYTHON). The lib*
# set is the opencv-python / Pillow / kornia runtime closure Dockerfile.dev
# already proved out. curl stays for the HEALTHCHECK.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-venv \
        python3-pip \
        curl \
        ca-certificates \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender-dev \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Same install contract as a bare-metal checkout (`pip install -r
# requirements.txt -c constraints.txt` into ./venv). build-essential is
# installed, used, and purged inside this one layer so sdist fallbacks can
# compile without the toolchain being shipped in the image.
COPY requirements.txt constraints.txt ./
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && python3 -m venv venv \
    && venv/bin/pip install --no-cache-dir --upgrade pip \
    && venv/bin/pip install --no-cache-dir -r requirements.txt -c constraints.txt \
    && apt-get purge -y build-essential \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Application source (respecting .dockerignore: models/outputs/storage are
# runtime volumes, never image content), then the built SPA where
# static_frontend.py looks for it.
COPY . .
COPY --from=frontend-builder /frontend/build ./frontend/build

RUN mkdir -p storage models outputs

# Containers publish ports explicitly, so the loopback default that protects
# a bare-metal install (see api.py) would only make the published port dead.
ENV POTIONUI_HOST=0.0.0.0

EXPOSE 8005

# First boot runs migrations and builds caches — give it a generous grace
# period before health flapping restarts anything.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8005/health || exit 1

CMD ["venv/bin/python", "api.py"]
