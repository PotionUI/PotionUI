---
title: Install
order: 11
---

# Install

PotionUI runs on **Linux and Windows x86_64 with an NVIDIA GPU**. It can generate on this machine's GPU, dispatch to a remote worker, or both; the `./potionui` CLI has an install preset for each. On native Windows every command reads `.\potionui.cmd ...` instead of `./potionui ...`; the profiles are the same.

## Pick an install

| You have                                              | What gets installed                                                 | Command                             |
| ----------------------------------------------------- | ------------------------------------------------------------------- | ----------------------------------- |
| A GPU in this machine                                 | Full CUDA stack                                                     | `./potionui start`                  |
| A GPU here, plus room to add remote workers later     | Full CUDA stack                                                     | `./potionui start --profile hybrid` |
| No GPU here (a VPS or laptop), dispatch to a worker   | CPU-only PyTorch, no CUDA libraries; **not** a CPU-generation mode  | `./potionui start --profile remote` |
| A GPU box that only serves another PotionUI instance  | Full CUDA stack, no frontend                                        | `./potionui worker start`           |

```bash
git clone https://github.com/PotionUI/PotionUI.git potionui && cd potionui
./potionui doctor    # check prerequisites, with a repair command for anything missing
./potionui start     # create the venv, install deps, launch backend + frontend, print the URL
```

- `./potionui start` is idempotent and supervises both processes; `./potionui stop` or Ctrl-C cleans them up together.
- `./potionui status` reports whether an instance is up; `./potionui doctor` names any problem and its fix.

## Requirements

- **Python 3.12** (3.13 works only by compiling numpy from source; the shim prefers 3.12 when both exist), and **Node.js 22+** (24 LTS recommended) for every preset except Worker.
- Floor: **8 GB VRAM + 16 GB RAM** (runs the SDXL family) on any preset that installs the CUDA stack. Larger families need more, some considerably more; see [Hardware Requirements](hardware-requirements.md).

## Supported platforms

| Platform                    | Status                                                                              |
| --------------------------- | ----------------------------------------------------------------------------------- |
| Linux x86_64 + NVIDIA CUDA  | Supported                                                                           |
| Windows native              | Supported; see [Windows](#windows)                                                  |
| Windows via WSL2            | Same Linux CUDA stack; a success or failure report helps                            |
| Docker                      | Supported; see [Docker](#docker) (on Windows, Docker Desktop runs this via WSL2)    |
| macOS                       | No: local generation needs CUDA, and the native engine has no MPS support           |
| AMD GPU (ROCm)              | No: the pinned dependency stack is CUDA-only                                        |

## Windows

**Prerequisites:**

- Python 3.12 from [python.org](https://www.python.org/downloads/windows/) (the `py` launcher works too; 3.13 is not yet usable because the pinned numpy has no 3.13 wheels).
- Node.js 22+ (24 LTS recommended).
- Git.
- For GPU generation: an NVIDIA driver version 580 or newer (the floor the CUDA 13 torch build in `constraints.txt` needs; see [Hardware Requirements](hardware-requirements.md)).

```powershell
git clone https://github.com/PotionUI/PotionUI.git potionui
cd potionui
.\potionui.cmd start
```

Triton and `torch.compile` have no Windows wheels, so attention runs on plain SDPA and the compile optimizations stay off.

**Reporting a Windows issue:** open a GitHub issue with the output of `potionui.cmd doctor --json`, the files under `logs/`, the output of `nvidia-smi`, and what happened when you generated with the smallest available preset.

## Docker

```bash
docker run --gpus all -p 26730:26730 ghcr.io/potionui/potionui:latest
```

Requires an NVIDIA GPU and [nvidia-container-toolkit](https://github.com/NVIDIA/nvidia-container-toolkit); volumes and details in [`docker/README.md`](../../docker/README.md). (`./potionui start-docker` runs the contributor-facing dev harness instead.)

## Running backend and frontend separately

Useful when iterating on one side only, or if `./potionui` doesn't fit your setup:

```bash
# Backend
python -m venv venv
source venv/bin/activate          # Windows (native): venv\Scripts\activate
pip install -r requirements.txt -c constraints.txt
python api.py                     # serves on http://localhost:26730

# Frontend
cd frontend
npm install
npm run dev                       # dev server on http://localhost:26731
```

Or run both together with `./run.sh` (assumes the venv and `node_modules` are already installed; `./potionui start` does not):

```bash
./run.sh                          # backend :26730, frontend :26731
./run.sh 26730 26731 --lan        # also accept connections from the local network
```

## Logs

Console output is mirrored to a rotating log file at `storage/logs/potionui.log`. Tune it with `POTIONUI_LOG_LEVEL` (default `INFO`), `POTIONUI_LOG_DIR` (default `storage/logs`, empty disables the file), `POTIONUI_LOG_MAX_BYTES` (default 20 MB) and `POTIONUI_LOG_BACKUP_COUNT` (default 10), or turn the file off outright with `POTIONUI_LOG_FILE=off`. Details in [Administration: Server logs](admin.md#server-logs).
