# PotionUI

A self-hosted studio for generating images, video, and audio with diffusion
models. One app, many model families, no per-model config to hand-wire —
you pick a preset, describe what you want, and watch it come together in
real time.

[![Backend + frontend tests](https://github.com/jtyszkiew/imagine/actions/workflows/backend-tests.yml/badge.svg)](https://github.com/jtyszkiew/imagine/actions/workflows/backend-tests.yml)
[![Onboarding smoke gate](https://github.com/jtyszkiew/imagine/actions/workflows/onboarding-smoke.yml/badge.svg)](https://github.com/jtyszkiew/imagine/actions/workflows/onboarding-smoke.yml)
[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/A3B325D031)
[![Discord](https://img.shields.io/badge/Discord-Join-5865F2?logo=discord&logoColor=white)](https://discord.gg/avR4trp3b8)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)


https://github.com/user-attachments/assets/950415f7-da97-403e-811b-4c9c41d8106f


## What you're looking at

Rather than forcing every model into one generic set of controls, PotionUI is
built around **presets**: each one is tuned for a specific model and renders a
curated form with exactly the options that model understands — resolution,
sampler, camera angle, art style, whatever applies. Presets run on PotionUI's
own in-process **native** engine (`diffusers` pipelines) or against an external
**ComfyUI** server via a plugin, and progress streams back over WebSocket as
it happens: per-step status, live previews, and a gallery that fills in as
results land.

On the native engine alone, presets currently cover:

| Model family    | What it does           | Docs                                       |
| ---------------- | ----------------------- | ------------------------------------------- |
| SDXL             | Image (txt2img, img2img, inpaint) | [docs/models/sdxl.md](docs/models/sdxl.md) |
| Flux (1 / 2 Klein) | Image (txt2img, img2img) | [docs/models/flux.md](docs/models/flux.md) |
| Qwen-Image       | Image (txt2img, img2img) | [docs/models/qwen_image.md](docs/models/qwen_image.md) |
| Krea-2           | Image (txt2img, img2img) | [docs/models/krea2.md](docs/models/krea2.md) |
| Z-Image          | Image (txt2img, img2img) | [docs/models/z_image.md](docs/models/z_image.md) |
| Anima            | Image (txt2img, img2img) | [docs/models/anima.md](docs/models/anima.md) |
| Wan 2.1 / 2.2    | Video                   | [docs/models/wan.md](docs/models/wan.md) |
| LTX-2 / 2.3 / 2.5 | Video (with audio)     | [docs/models/ltx.md](docs/models/ltx.md) |
| MiniMax-H3       | Video                   | [docs/models/minimax_h3.md](docs/models/minimax_h3.md) |
| MiniMax-Music3   | Audio (song)            | [docs/models/minimax_music3.md](docs/models/minimax_music3.md) |
| SeedVR2          | Upscale / restore       | [docs/models/seedvr2.md](docs/models/seedvr2.md) |

All in the same workspace.

## The generate workspace

<!--
SCREENSHOT SLOT — GENERATE / VIDEO PRESET
A video preset selected (Wan or LTX), curated form visible (resolution,
frames, sampler), generation in progress with the live streaming preview
and a step progress bar mid-run.
-->

Every tab in the generate workspace is its own sandbox — its own preset,
mode, prompts, and results — so you can run a few ideas side by side without
losing any of them. Switching presets swaps the form to match the model; the
same tab handles txt2img, img2img, inpainting, or a video/audio mode,
whichever the preset declares.

## History, tags, and collections

https://github.com/user-attachments/assets/f46cde26-0288-4e05-ac90-c3be31f0d2dd

Everything you generate is saved automatically, with the exact parameters
that produced it. Tag generations to group and re-find them, bulk-delete by
tag when you're clearing out throwaway experiments, sort work into
collections, and pull two results up side by side to compare.

## Video Director

<!--
SCREENSHOT SLOT — VIDEO DIRECTOR
Video Director editor open on a Wan or LTX-2 preset, shot/section rail
populated with 2-3 sections (e.g. global + two timed/chain sections).
-->

For native video presets, Video Director replaces per-mode prompt juggling
with one composition surface: build a shot out of global, timed, and chained
sections instead of hand-managing separate prompt fields per segment. It's
the same segment-card editor used everywhere else in PotionUI, just aimed at
a timeline. ComfyUI video presets are unaffected — this is a native-engine
composition tool.

## Music Director

<!--
SCREENSHOT SLOT — MUSIC DIRECTOR
Music Director editor open on the MiniMax-Music3 preset, a few sections
with lyrics/tags filled in.
-->

MiniMax-Music3 gets the same treatment: sections compile straight to tagged
lyrics, so you write verses and choruses instead of hand-authoring bracketed
section tags — the compiler does that part.

## Prompt tooling

<!--
SCREENSHOT SLOT — PROMPTS / PHRASEBOOK
Either the Prompts workspace (Segments tab with a few saved segments) or a
prompt editor mid-type with a Phrasebook autocomplete suggestion popup open
— whichever looks livelier.
-->

Prompts are built from reusable segment cards, not raw text blobs: save a
segment once and drop it into any prompt, group related segments into
templates, and let Phrasebook autocomplete known terms — art styles, camera
angles, lighting — as you type, with per-chip shuffle for quick variation.
Dynamic-prompt syntax (`{a|b}`, weighted choices, `${vars}`) and optional
LLM-assisted prompt enhancement layer on top of the same editor.

## AI assistant

<!--
SCREENSHOT SLOT — CHAT ASSISTANT
LLM chat panel open beside the generate form, showing a pending tool-approval
prompt (e.g. a proposed phrasebook edit awaiting your confirmation).
-->

When an administrator configures a language model, an assistant becomes
available alongside generation: it can brainstorm and rewrite prompts, and —
with your approval on every action that changes something — add or remove
phrasebook values, adjust form state, and more. The same tool surface is also
reachable from outside the app: PotionUI can mint per-user tokens for the
[Model Context Protocol](https://modelcontextprotocol.io), so an external MCP
client (Claude Desktop, an agent, your own tooling) can drive generation
against your instance directly.

## Plugins

Nearly every subsystem is an extension point: providers (credentialed
connections to model marketplaces like CivitAI), backends (configured
instances of an inference engine, e.g. a ComfyUI server), inference pipes
(the individual steps a generation pipeline is built from), form field types,
chat modes, and frontend pages can all be added by a plugin —
`src/plugin_api/` is the one import surface plugin code is allowed to use.
ComfyUI support itself ships as a plugin rather than living in core. See
[docs/plugin-api.md](docs/plugin-api.md) for the authoring reference.

## Quickstart

You need Python 3.12+ and Node.js 18+; GPU generation additionally needs a
CUDA-capable NVIDIA GPU with a matching PyTorch install. The supported floor
is **8 GB VRAM + 16 GB system RAM**, which runs the SDXL family; the larger
native model families (Flux, Krea-2, Qwen-Image, Wan, LTX, Z-Image, Anima,
MiniMax-H3) need more, some considerably more — see
[Hardware Requirements](docs/user/hardware-requirements.md) for the per-family
breakdown.

```bash
git clone https://github.com/jtyszkiew/imagine.git potionui && cd potionui
./potionui doctor    # check prerequisites, with a repair command for anything missing
./potionui start      # create the venv, install deps, launch backend + frontend, print the URL
```

`./potionui start` is idempotent (safe to re-run against an already-running
instance) and supervises both processes so `./potionui stop` or Ctrl-C cleans
them up together. `./potionui status` reports whether an instance is up. If
something's wrong, `./potionui doctor` names the problem and the fix.

On a host with no NVIDIA GPU — a VPS pointed at a remote generation backend,
for instance — `./potionui start --no-gpu` skips installing the multi-GB CUDA
stack; it's for hosting the app against a remote backend, not for running
generation on CPU.

### Supported platforms

| Platform                   | Status                                                                     |
| --------------------------- | --------------------------------------------------------------------------- |
| Linux x86_64 + NVIDIA CUDA | Tested and supported for 0.0.1                                            |
| Windows / WSL2             | Untested — community reports welcome                                      |
| macOS                      | Untested — community reports welcome                                      |
| AMD GPU (ROCm)             | Untested — community reports welcome                                      |
| Docker                     | `./potionui start-docker` runs a containerized dev harness (requires an NVIDIA GPU + [nvidia-container-toolkit](https://github.com/NVIDIA/nvidia-container-toolkit)) — see [`docker/README.md`](docker/README.md); a distribution image is still planned |

### Running backend and frontend separately

Useful when iterating on one side only, or if `./potionui` doesn't fit your
setup:

```bash
# Backend
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt -c constraints.txt
python api.py                     # serves on http://localhost:8005

# Frontend
cd frontend
npm install
npm run dev                       # dev server on http://localhost:3001
```

Or run both together with `./run.sh` (assumes the venv and `node_modules` are
already installed — `./potionui start` does not):

```bash
./run.sh                          # backend :8005, frontend :3001
./run.sh 8005 3001 --lan          # also accept connections from the local network
```

## Documentation

Start with the in-app documentation browser, or read the Markdown directly:

- **[Getting started](docs/user/getting-started.md)** — first login to first
  image, and the rest of the `docs/user/` guide (generating, models, presets and
  forms, history, prompts, admin).
- **Reference** in `docs/`: [presets](docs/presets.md),
  [prompts](docs/prompts.md), [backends](docs/backends.md),
  [providers](docs/providers.md), [native engine](docs/native-engine.md),
  [models](docs/models.md), [video director](docs/video-director.md),
  [music director](docs/music-director.md), and the per-model and
  per-technique docs under `docs/models/` and `docs/techniques/`.

## Contributing

See **[CONTRIBUTING.md](CONTRIBUTING.md)** for dev setup, the test and lint
commands, and PR expectations. `CLAUDE.md` is the deeper architecture
reference — the package layering contracts, the plugin system, and the
per-subsystem docs — and its layering rules are enforced by
`tests/architecture/test_layering.py`, so new code that crosses a package
boundary the wrong way will fail CI.

Found a security issue? See [SECURITY.md](SECURITY.md).

## Support

If PotionUI is useful to you, a [Ko-fi](https://ko-fi.com/A3B325D031)
contribution helps keep it going, and the
[Discord](https://discord.gg/avR4trp3b8) is where development happens in the
open.

## License

PotionUI is licensed under the **GNU General Public License v3.0** — see
[LICENSE](LICENSE).

PotionUI bundles third-party code under `vendor/`, each component keeping its
own upstream license and per-file provenance. That code is imported by core
rather than kept at arm's length; the GPL-3.0 components among it are what set
the project-wide license. See **[vendor/NOTICE.md](vendor/NOTICE.md)** for the
full attribution table.

PotionUI is distributed **without any model weights** — models are downloaded
or installed only at your explicit request, and every model carries its own
license, separate from PotionUI's. See
**[Models & licensing](docs/user/models.md#model-licensing)** for the full
picture and where responsibility sits.

---

[Docs](docs/user/getting-started.md) ·
[Discord](https://discord.gg/avR4trp3b8) ·
[Contributing](CONTRIBUTING.md)
