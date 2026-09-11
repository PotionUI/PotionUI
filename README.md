# PotionUI

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/A3B325D031)
[![Discord](https://img.shields.io/badge/Discord-Join-5865F2?logo=discord&logoColor=white)](https://discord.gg/avR4trp3b8)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Status: Alpha](https://img.shields.io/badge/Status-Alpha-orange.svg)](#)

**The self-hosted generation studio you can hand to other people.**

Run Flux, Wan, LTX, Qwen-Image and MiniMax on one box — then give your team,
your household, or your agents their own logins, presets, and limits. No node
graphs. No per-model setup. Pick a model, type, watch it render live.

![The generate workspace: model tabs, a prompt built from colored segments, and the finished render](docs/media/potionui-generation-page.png)

<details>
<summary>Watch the 60-second tour (video)</summary>

https://github.com/user-attachments/assets/950415f7-da97-403e-811b-4c9c41d8106f

</details>

**Why PotionUI:**

- **Forms, not wiring** — each model exposes only the controls it actually
  understands.
- **Accounts, groups, admin panel** — one GPU, many users, per-user presets
  and model access.
- **Drive it from Claude Desktop or any MCP client** — per-user tokens; every
  write action needs your approval.
- **Video and Music Directors** — compose shots and songs in sections instead
  of one giant prompt.

*Alpha 0.0.7 · Linux x86_64 + NVIDIA · Windows via WSL2 or Docker ·
[Discord](https://discord.gg/avR4trp3b8) · [Ko-fi](https://ko-fi.com/A3B325D031)*

## 60 seconds to first image

```bash
git clone https://github.com/PotionUI/PotionUI.git potionui && cd potionui
./potionui doctor    # check prerequisites, with a repair command for anything missing
./potionui start     # create the venv, install deps, launch backend + frontend, print the URL
```

Floor: 8 GB VRAM + 16 GB RAM (SDXL). Full requirements below.

## Contents

- [What you're looking at](#what-youre-looking-at)
- [Multi-user, with an admin panel](#multi-user-with-an-admin-panel)
- [AI assistant](#ai-assistant)
- [The generate workspace](#the-generate-workspace)
- [History, tags, and collections](#history-tags-and-collections)
- [Video Director](#video-director) · [Music Director](#music-director)
- [Prompt tooling](#prompt-tooling)
- [Automations](#automations)
- [Plugins](#plugins)
- [Install](#install) — [supported platforms](#supported-platforms),
  [manual setup](#running-backend-and-frontend-separately)
- [Documentation](#documentation) · [Changelog](#changelog)
- [Contributing](#contributing) · [Support](#support) · [License](#license)

## What you're looking at

- **Pick a model, get the right controls** — each model ships a form with only
  the controls it understands: resolution, camera angle, art style, whatever
  applies.
- **Watch it happen live** — step-by-step progress, streaming previews, and a
  gallery that fills in as results land.
- **One app for all of it** — images, video, and audio side by side, no
  per-model setup.

| Model family       | What it does                      | Docs                                                           |
| ------------------ | --------------------------------- | -------------------------------------------------------------- |
| SDXL               | Image (txt2img, inpaint)          | [docs/models/sdxl.md](docs/models/sdxl.md)                     |
| Flux (1 / 2 Klein) | Image (txt2img, img2img)          | [docs/models/flux.md](docs/models/flux.md)                     |
| Qwen-Image         | Image (txt2img, img2img, edit)    | [docs/models/qwen_image.md](docs/models/qwen_image.md)         |
| Krea-2             | Image (txt2img, enhance)          | [docs/models/krea2.md](docs/models/krea2.md)                   |
| Z-Image            | Image (txt2img)                   | [docs/models/z_image.md](docs/models/z_image.md)               |
| Anima              | Image (txt2img)                   | [docs/models/anima.md](docs/models/anima.md)                   |
| Wan 2.1 / 2.2      | Video                             | [docs/models/wan.md](docs/models/wan.md)                       |
| LTX-2 / 2.3 / 2.5  | Video (with audio), video upscale | [docs/models/ltx.md](docs/models/ltx.md)                       |
| MiniMax-H3         | Video (with reference images)     | [docs/models/minimax_h3.md](docs/models/minimax_h3.md)         |
| MiniMax-Music3     | Audio (song)                      | [docs/models/minimax_music3.md](docs/models/minimax_music3.md) |
| SeedVR2            | Image & video upscale / restore   | [docs/models/seedvr2.md](docs/models/seedvr2.md)               |

## Multi-user, with an admin panel

![Admin preset management: the catalog of installed and available presets across engines, with a per-preset overview and access tab](docs/media/potionui_admin_preset_overview.png)

PotionUI is built for more than one person on the same box:

- **Users and groups** — create accounts, group them, and assign presets,
  models, and LLM configurations per user or to a whole group at once.
- **Presets under control** — decide who sees which preset, and reshape any
  preset's form per mode: change defaults, lock fields, or hide them
  entirely, no YAML editing required.
- **Backends** — configure where generations run and let users pick between
  enabled backends.
- **LLM setup** — wire up the providers behind the assistant (Ollama,
  OpenRouter, …) and hand them out per user or group.
- **Models, plugins, settings** — manage installed models and downloads,
  toggle plugins, and set global options, all from the same panel.

The full tour: [docs/user/admin.md](docs/user/admin.md).

## AI assistant

![The generation assistant reads the active tab and proposes a segment rewrite you apply with one click](docs/media/potionui_phrasebook_assistant_active.png)

- Configure a language model and get an assistant beside generation: it
  brainstorms and rewrites prompts, edits phrasebook values, adjusts form
  state — **with your approval on every action that changes something**.
- The same tools are reachable from outside the app: PotionUI mints per-user
  [Model Context Protocol](https://modelcontextprotocol.io) tokens, so an MCP
  client (Claude Desktop, an agent, your own tooling) can drive your instance
  directly.

## The generate workspace

<!--
SCREENSHOT SLOT — GENERATE / VIDEO PRESET
A video preset selected (Wan or LTX), curated form visible (resolution,
frames, sampler), generation in progress with the live streaming preview
and a step progress bar mid-run.
-->

- Every tab is its own sandbox — preset, mode, prompts, and results — so you
  can run several ideas side by side without losing any of them.
- Switch models and the form swaps to match; the same tab handles txt2img,
  img2img, inpainting, or a video/audio mode.
- Like a setup? Save it as a **session** — preset, mode, prompts, and form
  values — and pull it back up any time.

## History, tags, and collections

![History with collections, tags, keyword or semantic search, and date-grouped renders](docs/media/potionui_gallery_page.png)

![Generation details: the render with its parameters, seed, segments, and the exact model files it used](docs/media/potionui_gallery_page-details.png)

https://github.com/user-attachments/assets/f46cde26-0288-4e05-ac90-c3be31f0d2dd

- Everything you generate is saved automatically, with the exact parameters
  that produced it.
- Tag generations to group and re-find them; bulk-delete by tag to clear out
  throwaway experiments.
- Sort work into collections, and compare two results side by side.

## Video Director

<!--
SCREENSHOT SLOT — VIDEO DIRECTOR
Video Director editor open on a Wan or LTX-2 preset, shot/section rail
populated with 2-3 sections (e.g. global + two timed/chain sections).
-->

- Build a shot out of **global, timed, and chained sections** instead of
  hand-managing separate prompt fields per segment.
- Same segment-card editor as everywhere else in PotionUI, aimed at a
  timeline.

## Music Director

<!--
SCREENSHOT SLOT — MUSIC DIRECTOR
Music Director editor open on the MiniMax-Music3 preset, a few sections
with lyrics/tags filled in.
-->

- Write verses and choruses as sections; the compiler turns them into
  MiniMax-Music3's tagged lyrics for you.

## Prompt tooling

![The phrasebook: reusable phrase categories with per-value preview images generated from a template prompt](docs/media/potionui_phrasebook_category_page.png)

- **Segments** — save a prompt fragment once, drop it into any prompt; group
  related segments into templates.
- **Phrasebook** — autocomplete for known terms (art styles, camera angles,
  lighting) with per-chip shuffle for quick variation.
- **Dynamic prompts** — `{a|b}`, weighted choices, `${vars}`.
- **LLM enhancement** — optional, layered on the same editor.

## Automations

![The automation editor: a Backend Event trigger wired to a Clear VRAM action, with typed outputs and a run history](docs/media/potionui_admin_automations.png)

- Wire **triggers** (schedule, manual, file watcher, GPU VRAM threshold, app
  events) through **conditions** to **actions** (tag, add to a collection,
  assign models or users, backend actions, notifications, indexing) on a
  visual graph.
- Start from an importable template, export as JSON, and read every run's
  history and logs in the same editor.

## Plugins

Nearly every subsystem is an extension point — even alternate inference
backends are plugins, not core code:

- **Providers** — credentialed connections to model marketplaces (e.g. CivitAI)
- **Backends** — configured instances of an inference engine (e.g. a ComfyUI
  server via the `comfyui-backend` plugin, which also ships image presets for
  SDXL, Qwen-Image, Z-Image, Krea-2, and Flux Klein 9B); you can import your
  own ComfyUI workflows as presets too — see
  [docs/presets/comfyui.md](docs/presets/comfyui.md)
- **Pipes** — the individual steps a generation pipeline is built from
- **Field types, chat modes, frontend pages** — all pluggable

Plugin code imports only from `src/plugin_api/`. Authoring reference:
[docs/plugin-api.md](docs/plugin-api.md).

## Install

> [!WARNING]
> PotionUI is in **alpha**: expect rough edges and breaking changes between
> releases. Back up anything you care about, and report what breaks — issues
> and Discord reports steer what gets fixed next.

> [!IMPORTANT]
> **Runs on Linux x86_64 with an NVIDIA GPU** — that's the tested 0.0.7
> matrix. On Windows, use WSL2 or Docker Desktop (native Windows won't even
> install yet). Details in [Supported platforms](#supported-platforms).

PotionUI can generate on this machine's GPU, dispatch to a remote worker, or
both — the `./potionui` CLI has an install preset for each.

**Pick an install:**

| You have                                              | What gets installed                                                 | Command                             |
| ------------------------------------------------------ | ----------------------------------------------------------------- | -------------------------------------- |
| A GPU in this machine                                 | Full CUDA stack                                                    | `./potionui start`                     |
| A GPU here, plus room to add remote workers later      | Full CUDA stack                                                    | `./potionui start --profile hybrid`    |
| No GPU here (a VPS or laptop) — dispatch to a worker    | CPU-only PyTorch, no CUDA libraries — **not** a CPU-generation mode | `./potionui start --profile remote`    |
| A GPU box that only serves another PotionUI instance    | Full CUDA stack, no frontend                                       | `./potionui worker start`              |

You need:

- **Python 3.12+**, and **Node.js 18+** for every preset except Worker.
- Floor: **8 GB VRAM + 16 GB RAM** (runs the SDXL family) on any preset that
  installs the CUDA stack. Larger families need more, some considerably more
  — see [Hardware Requirements](docs/user/hardware-requirements.md).

```bash
git clone https://github.com/PotionUI/PotionUI.git potionui && cd potionui
./potionui doctor    # check prerequisites, with a repair command for anything missing
./potionui start     # create the venv, install deps, launch backend + frontend, print the URL
```

- `./potionui start` is idempotent and supervises both processes; `./potionui stop`
  or Ctrl-C cleans them up together.
- `./potionui status` reports whether an instance is up; `./potionui doctor`
  names any problem and its fix.

### Supported platforms

| Platform                    | Status                                                                                      |
| --------------------------- | ------------------------------------------------------------------------------------------- |
| Linux x86_64 + NVIDIA CUDA  | Tested and supported for 0.0.7                                                              |
| Windows via WSL2            | Should work — same Linux CUDA stack, just unverified; a success/failure report would help   |
| Windows native              | No — the install pulls Linux-only packages (e.g. `uvloop`); use WSL2 or Docker Desktop      |
| macOS                       | No — local generation needs CUDA; the native engine has no MPS support                      |
| AMD GPU (ROCm)              | No — the pinned dependency stack is CUDA-only                                               |
| Docker                      | Supported — see below (on Windows, Docker Desktop runs this via WSL2)                       |

```bash
docker run --gpus all -p 7680:7680 ghcr.io/potionui/potionui:latest
```

Requires an NVIDIA GPU +
[nvidia-container-toolkit](https://github.com/NVIDIA/nvidia-container-toolkit);
volumes and details in [`docker/README.md`](docker/README.md).
(`./potionui start-docker` runs the contributor-facing dev harness instead.)

### Running backend and frontend separately

Useful when iterating on one side only, or if `./potionui` doesn't fit your
setup:

```bash
# Backend
python -m venv venv
source venv/bin/activate          # Windows: run inside WSL2 (native isn't supported yet)
pip install -r requirements.txt -c constraints.txt
python api.py                     # serves on http://localhost:7680

# Frontend
cd frontend
npm install
npm run dev                       # dev server on http://localhost:7681
```

Or run both together with `./run.sh` (assumes the venv and `node_modules` are
already installed — `./potionui start` does not):

```bash
./run.sh                          # backend :7680, frontend :7681
./run.sh 7680 7681 --lan          # also accept connections from the local network
```

### Logs

Console output is mirrored to a rotating log file at `storage/logs/potionui.log`.
Tune it with `POTIONUI_LOG_LEVEL` (default `INFO`), `POTIONUI_LOG_DIR` (default
`storage/logs`, empty disables the file), `POTIONUI_LOG_MAX_BYTES` (default
20 MB) and `POTIONUI_LOG_BACKUP_COUNT` (default 10), or turn the file off
outright with `POTIONUI_LOG_FILE=off`. Details in
[Administration → Server logs](docs/user/admin.md#server-logs).

## Documentation

Start with the in-app documentation browser, or read the Markdown directly:

- **[Getting started](docs/user/getting-started.md)** — first login to first
  image, plus the rest of the `docs/user/` guide.
- **Reference** in `docs/`: [presets](docs/presets.md),
  [prompts](docs/prompts.md), [backends](docs/backends.md),
  [providers](docs/providers.md), [native engine](docs/native-engine.md),
  [models](docs/models.md), [video director](docs/video-director.md),
  [music director](docs/music-director.md), and per-model / per-technique
  docs under `docs/models/` and `docs/techniques/`.

## Changelog

The two most recent releases; older history lives in the
[commit log](https://github.com/PotionUI/PotionUI/commits/master).

### 0.0.7 — 2026-09-11

- Generate: the Anima preset ships rendered previews for all fifty of its styles, so
  the Styles picker shows what each one looks like on the same potion scene instead
  of placeholder tiles.

### 0.0.6 — 2026-09-11

- Generate: presets can ship curated styles — a Styles button in the Prompt panel opens
  a picker with category filters, a text filter and Small/Big previews, and applying a
  style wraps the prompt with the style's opening and closing segments plus its negative,
  with a second pick replacing the first; Anima ships fifty styles; prompt weights like `(red hair:1.3)` are honored by the Qwen3, Qwen3-VL and
  Qwen2.5-VL text encoders (Klein, Krea-2, Qwen-Image, Anima, Z-Image); phrasebook chips
  animate when a value is shuffled or picked; the chat's Suggested change preview shows
  phrasebook and variable references as chips.
- Generate: the page stays smooth during a generation — status and preview updates are
  applied once per frame instead of once per sampling step, the presets list and plugin
  catalogs are fetched once at boot instead of twice, and the collapsed workbench is a
  labelled rail in both layouts; the Prompt panel toolbar reads at the app's text size.
- History and Library: drag a marquee, Shift-click a range, Ctrl-click to toggle and
  Ctrl+A to select the page, on both grids; Delete by criteria replaces Delete by tags —
  tags, older than N days or a date range, failed/cancelled only, without media files,
  keep favorites — with a live count; Compare gets Overlay with an opacity slider and
  Wipe with a draggable divider on full-resolution images plus a full-screen viewer;
  Compare, Stitch and Download work from the Library too, plugins can scope their
  tools to History, Library or both, and the Library can export a zip; Stitch results
  can be saved to the Library; grids resize without remounting their thumbnails.
- Chat: memory reflection saves to the preset a generation chat is about and to the
  plugin mode a plugin chat runs in, and only to global when you ask; long sessions
  open on their latest messages and load earlier ones as you scroll up, streamed
  replies are applied once per frame, and the transcript only follows the reply while
  you are at the bottom; Admin → Chat Sessions can clear every chat session.
- Admin: Housekeeping deletes generations by criteria across all users with a
  preview count; the Add Download modal picks the model type from a chip row and the
  destination from your real depot subfolders, nested ones included, with an inline
  New folder option, and the Downloads to hint follows the type; the model picker's
  download shows a spinner; Hugging Face downloads carry the provider's token.
- Recipes: starter recipes for Flux2 Klein, Krea-2, Qwen-Image, Anima, Wan 2.2, LTX-2,
  LTX-2.5, MiniMax-H3, MiniMax-Music3, SeedVR2 and TRELLIS.2 with official repositories;
  gated models are marked with their licence link; the first-generation step shows
  sampling progress and the result inline (image, video, audio or mesh); an installed
  model is recognised by hash even when it was filed under another type or folder.
- Native engine: the text encoder stays on the GPU after encoding when it fits, so
  back-to-back generations skip the reload; rotary tables are computed once per run
  for MiniMax-H3 and Krea-2; Krea-2 gains ER-SDE, DPM++ 2M SDE, DPM++ 3M and RES
  multistep samplers and a Beta schedule, and samplers and schedules are a plugin
  extension point; long-prompt attention stays on the memory-efficient kernel instead
  of running out of memory.
- Fixes: the model scanner no longer stops on an indexed model without a file path or
  when the models location is unset; a recipe download could land under a doubled
  models folder and go unfound by the first-generation step; tag popovers inside
  modals no longer stretch the modal; long option lists in chip popovers scroll; the
  phrasebook preview poll pauses while the tab is hidden; superseded model searches
  are cancelled instead of racing.
- Upgrading: style previews are rendered with `python scripts/preset_styles_render.py
  <preset directory>` — a preset's `styles.yml` declares the styles and a shared
  preview scene, and the script writes the previews into the preset's `public/styles/`
  folder.

## Contributing

- **[CONTRIBUTING.md](CONTRIBUTING.md)** — dev setup, test and lint commands,
  PR expectations.
- `CLAUDE.md` — the deeper architecture reference; its package-layering rules
  are enforced by `tests/architecture/test_layering.py`, so boundary-crossing
  code fails CI.
- Security issue? See [SECURITY.md](SECURITY.md).

## Support

If PotionUI is useful to you, a [Ko-fi](https://ko-fi.com/A3B325D031)
contribution helps keep it going, and the
[Discord](https://discord.gg/avR4trp3b8) is where development happens in the
open.

## License

- PotionUI is **GPL-3.0** — see [LICENSE](LICENSE).
- Third-party code is bundled under `vendor/`, each component keeping its own
  upstream license; the GPL-3.0 components among it set the project-wide
  license. Full attribution: [vendor/NOTICE.md](vendor/NOTICE.md).
- **No model weights are distributed** — models download only at your explicit
  request, each under its own license, separate from PotionUI's. See
  [Models & licensing](docs/user/models.md#model-licensing).

---

[Docs](docs/user/getting-started.md) ·
[Discord](https://discord.gg/avR4trp3b8) ·
[Contributing](CONTRIBUTING.md)
