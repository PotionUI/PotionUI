# Docker Configuration for PotionUI

## Distribution image — `Dockerfile`

The single-process production image, published to GHCR on every version tag
by `.github/workflows/docker-publish.yml`:

```bash
docker run --gpus all -p 8005:8005 \
  -v potionui-models:/app/models \
  -v potionui-storage:/app/storage \
  -v potionui-outputs:/app/outputs \
  ghcr.io/potionui/potionui:latest

# open http://localhost:8005
```

Unlike the rig-simulation harness below, this image runs exactly one process
on exactly one port: the backend serves the prebuilt SvelteKit SPA itself
(`src/bootstrap/static_frontend.py`), so there is no node runtime, no dev
server, and nothing to configure beyond the three volumes (`models`,
`storage`, `outputs` — all runtime state lives there; the image contains
none). GPU access works the same way as everything else in this directory:
[nvidia-container-toolkit](https://github.com/NVIDIA/nvidia-container-toolkit)
and `--gpus all`. The container binds `0.0.0.0` internally (a container's
published port is its access control; the bare-metal loopback default would
only make `-p` dead) — scope exposure with the `-p` flag, e.g.
`-p 127.0.0.1:8005:8005` to keep it host-local.

Build locally instead of pulling: `docker build -f docker/Dockerfile -t
potionui .` from the repo root.

## Rig-simulation harness — `Dockerfile.dev` + `docker-compose.yml`

Supported, and the current entry point into this directory. It exists to let
a machine with plenty of RAM/VRAM (the maintainer's box: 96GB RAM / RTX 5090)
exercise onboarding and generation the way a weaker machine actually
experiences them, under real resource caps instead of guesswork — and it
doubles as the head start for a future Docker distribution image.

The blessed way to launch it is `./potionui start-docker` (from the repo
root): it preflights `docker`, `docker compose` (v2), and
nvidia-container-toolkit with doctor-style pass/fail rows and repair hints,
then execs `docker compose -f docker/docker-compose.yml up --build` in the
foreground (Ctrl-C stops it). Pass a service name through to select a
profile, e.g. `./potionui start-docker rig-mid`. Calling `docker compose`
directly still works and is what the CLI wraps:

```bash
# from the repo root
docker compose -f docker/docker-compose.yml up rig-mid    # 32GB RAM / 8 CPU / 16GB "VRAM"
docker compose -f docker/docker-compose.yml up rig-small  # 16GB RAM / 4 CPU /  8GB "VRAM"

# open http://localhost:3001 (rig-mid) or http://localhost:3002 (rig-small)
```

What it does, honestly — no dev shortcuts a real user wouldn't also get:

- **Base image**: `nvidia/cuda:13.0.2-runtime-ubuntu24.04`, matching the CUDA
  userspace pinned in `constraints.txt` (`torch==2.12.1`,
  `cuda-toolkit==13.0.2`, `nvidia-cuda-runtime==13.0.96` → CUDA 13.0). torch's
  own PyPI wheel already carries its CUDA runtime as ordinary pip
  dependencies (see `constraints.txt`'s header), so this base image isn't
  load-bearing for `import torch` — it exists so `nvidia-container-toolkit`
  mounts a driver/CUDA userspace that matches what a bare-metal install of
  the same CUDA version would look like, which is the point of a
  *simulation* harness. **Host prerequisites:** NVIDIA driver version 580 or
  newer, `nvidia-container-toolkit` installed and configured as Docker's GPU
  runtime, Turing-or-newer GPU (sm_75+; GTX 10-series and older are not
  supported). The host needs the driver and toolkit only — no host CUDA
  toolkit install required.
- **Deps installed the documented way**: `pip install -r requirements.txt -c
  constraints.txt` into a `./venv` at image-build time (the same command
  `./potionui start` runs on a bare-metal checkout), plus `npm ci` in
  `frontend/`.
- **Boots the real bootstrap**: the container entrypoint
  (`docker/scripts/entrypoint.sh`) runs `./potionui doctor` first — so a
  misconfigured container fails loudly and visibly, exactly like a bad
  bare-metal checkout would — then launches backend + frontend via `run.sh
  --lan` (0.0.0.0 binding, so the compose-published ports are reachable from
  the host; `./potionui start` itself binds the backend to `127.0.0.1` and
  detaches its children, which is right for an interactive shell but wrong
  for a container's PID 1).
- **`POTIONUI_VRAM_CAP_GB`** (`src/platform/runtime/vram_cap.py`) caps what
  every VRAM placement/admission decision *perceives* as the card's
  total/free memory — the real GPU is still whatever's on the host
  (`--gpus all` isn't partitionable at the Docker level), so this is a
  software knob, not real isolation. It logs loudly at process startup
  (`*** VRAM capped to N GB for rig simulation ***`) specifically so a capped
  run's numbers are never mistaken for the real card's in a bug report.
- **RAM is a real hard cap**: `mem_limit` == `memswap_limit` for each
  service, so there's no swap escape valve — RAM pressure hits the same wall
  a genuinely RAM-constrained box would hit. Inside the container this is
  read correctly via `src/platform/runtime/system_memory.py`
  (cgroup-v2-aware): without it, `psutil.virtual_memory()` reports the
  HOST's RAM even inside a memory-limited container, so every RAM-budgeted
  admission decision (model-lifecycle LRU eviction headroom, the
  standalone-upscale RAM floor, ...) would overshoot the container's real
  ceiling — a bug real users would hit in any memory-limited container, not
  just a test-harness artifact.
- **Models/outputs/settings persist** in named volumes
  (`potionui-models`/`potionui-outputs`/`potionui-storage`) shared across
  both profiles, so a model downloaded under `rig-mid` doesn't need
  re-downloading under `rig-small`. Drop a volume to force a genuinely clean
  onboarding run, e.g. `docker volume rm docker_potionui-storage`.

Requires [nvidia-container-toolkit](https://github.com/NVIDIA/nvidia-container-toolkit)
installed and configured as Docker's GPU runtime (`docker info | grep -i
nvidia` should show it) — GPU access is via `--gpus all` / the compose
`deploy.resources.reservations.devices` block, not baked into the image.

Validate the compose file without a daemon: `docker compose -f
docker/docker-compose.yml config`.

## One-command onboarding sandbox — `docker/onboarding_sandbox.sh`

For "let me click through onboarding by hand on a clean machine, right now" —
the maintainer-facing counterpart to `tests/e2e/harness/onboarding_e2e.py`
(which drives the same journey headlessly over HTTP, no Docker involved).
This reuses the same `Dockerfile.dev` image as the rig-simulation harness
above, but runs a single throwaway container instead of the persistent
`rig-mid`/`rig-small` compose services:

```bash
# from the repo root
./docker/onboarding_sandbox.sh /path/to/models
# or: POTIONUI_MODELS_DIR=/path/to/models ./docker/onboarding_sandbox.sh

# open http://localhost:8065 once it's up (backend API on :8066)
```

- **Builds `potionui-dev:latest` only if it doesn't already exist** (pass
  `--rebuild` to force a rebuild - e.g. after changing `requirements.txt` or
  the Dockerfile). Same image, same entrypoint (`./potionui doctor` -> `run.sh
  --lan`) as the rig-simulation harness - no shortcuts.
- **A brand new container every run** (a timestamped name, never reused).
  Your models directory is bind-mounted **read-only** at `/app/models`;
  `/app/storage` and `/app/outputs` are **anonymous** volumes (not the
  `potionui-storage`/`potionui-outputs` named volumes `docker-compose.yml`
  uses) - deliberately so nothing persists across runs. `docker run --rm`
  removes both the container and its anonymous volumes on exit, so the next
  run starts from a genuinely clean machine: no claimed owner, no database, no
  generated files, same as a maintainer's first-ever install.
- **Ports**: frontend on `POTIONUI_SANDBOX_PORT` (default `8065`), backend API
  on `POTIONUI_SANDBOX_BACKEND_PORT` (default frontend port + 1, i.e. `8066`).
  Both are host-side only - the container's internal ports stay the
  Dockerfile's defaults (`3001`/`8005`).
- **GPU**: same as the rig-simulation harness - requires
  `nvidia-container-toolkit` (`--gpus all`); no CPU-only fallback.
- Ctrl-C stops the container; the `trap`/`--rm` combination is what actually
  removes it and its volumes - closing the terminal instead of Ctrl-C-ing may
  leave it running.

This script is syntax-checked (`bash -n`) and reuses the rig-simulation
harness's proven `docker run`/`docker-compose.yml` flags, but has not been
exercised end-to-end against a live `docker build`/`docker run`.

The legacy pre-SvelteKit Dockerfiles/compose files/build scripts that used to
live here (`docker.sh`, `dockerfiles/`, `compose/`,
`scripts/docker-{build,quick-rebuild,smart-build}.sh`,
`requirements-core.txt`) predated the current `frontend/` and never built or
ran against it; they have been removed. Everything in the directory
structure below is supported; nothing else is.

## Directory Structure

```
docker/
├── Dockerfile             # Supported: distribution image, published to GHCR (see above)
├── Dockerfile.dev         # Supported: rig-simulation image (see above)
├── docker-compose.yml     # Supported: rig-mid / rig-small profiles (see above)
├── worker.Dockerfile      # Supported: RunPod worker image (see below)
├── onboarding_sandbox.sh  # Supported: one-command onboarding sandbox (see above)
└── scripts/
    └── entrypoint.sh          # Dockerfile.dev's ENTRYPOINT: doctor -> run.sh --lan
```

## RunPod worker image — `worker.Dockerfile`

Supported. A reference image for the Remote Native worker
(`worker.py`, [`docs/remote-native.md`](../docs/remote-native.md)) that the
`runpod-provider` plugin points a RunPod GPU Pod at. Unlike `Dockerfile.dev`
above, this image runs no frontend and no PotionUI database - just
`worker.py`, speaking worker protocol v1 over HTTP+SSE.

Published by CI on every version tag, same as the distribution image above:
`ghcr.io/potionui/potionui-worker:latest`. The `runpod-provider` plugin's
worker-image setting defaults to that reference, so a stock install needs no
manual build or push.

Building your own image is still an option - for a fork, or to pin a specific
commit ahead of the next tagged release:

```bash
# from the repo root
docker build -f docker/worker.Dockerfile -t <registry>/<image>:<tag> .
docker push <registry>/<image>:<tag>
```

Then point the provider plugin's worker-image setting (Admin -> Plugins) at
that reference instead of the default. RunPod must be able to pull it: a
public registry needs nothing further; a private one needs a RunPod
Container Registry Auth entry, which this plugin does not manage.

The image itself never bakes in `POTIONUI_WORKER_TOKEN` or any other
per-deployment secret - those are supplied as Pod environment variables at
provision time (see the plugin's README). Rebuild and re-push whenever
`requirements.txt`/`constraints.txt` or the `src/`/`vendor/`/`content/`
trees the worker imports from change; nothing here watches for
that automatically.