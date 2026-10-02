<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/media/brand/potionui-mark-light-256.png">
  <img src="docs/media/brand/potionui-mark-dark-256.png" alt="PotionUI" width="96">
</picture>

# PotionUI

**The self-hosted generation studio you can hand to other people.**

Images, video and music on your own GPU · Free and open source · Linux & Windows

[![Latest release](https://img.shields.io/github/v/release/PotionUI/PotionUI?include_prereleases&label=latest&color=orange)](https://github.com/PotionUI/PotionUI/releases)
[![Tests](https://img.shields.io/github/actions/workflow/status/PotionUI/PotionUI/backend-tests.yml?branch=master&label=tests)](https://github.com/PotionUI/PotionUI/actions/workflows/backend-tests.yml)
[![License: GPL v3](https://img.shields.io/badge/license-GPL--3.0-blue)](LICENSE)
[![Stars](https://img.shields.io/github/stars/PotionUI/PotionUI?logo=github&label=stars)](https://github.com/PotionUI/PotionUI/stargazers)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20Windows-lightgrey)](#install)

[![Discord](https://img.shields.io/badge/Discord-Join-5865F2?logo=discord&logoColor=white)](https://discord.gg/avR4trp3b8)
[![Reddit](https://img.shields.io/badge/Reddit-r%2FPotionUI-FF4500?logo=reddit&logoColor=white)](https://www.reddit.com/r/PotionUI/)
[![ko-fi](https://img.shields.io/badge/Ko--fi-Support-FF5E5B?logo=kofi&logoColor=white)](https://ko-fi.com/A3B325D031)

**[Install](#install)** · [Quick start](#60-seconds-to-first-image) · [Website](https://potionui.com) · [Docs](https://potionui.com/docs) · [Changelog](#changelog) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

Run Krea-2, Anima, SDXL, Flux, Wan, LTX, Qwen-Image, MiniMax and YuE2 on one box — then give your team, your household, or your agents their own logins, presets, and limits. No node graphs. No per-model setup. Pick a model, type, watch it render live.

![The generate workspace: a tab per idea, a prompt built from colored segments with variable and phrasebook chips, and the finished render](docs/media/potionui-generate.png)

<details>
<summary>Watch the 60-second tour (video)</summary>

https://github.com/user-attachments/assets/950415f7-da97-403e-811b-4c9c41d8106f

</details>

</div>

**Why PotionUI:**

- **Forms, not wiring** — each model exposes only the controls it actually
  understands.
- **Accounts, groups, admin panel** — one GPU, many users, per-user presets
  and model access.
- **Drive it from Claude Desktop or any MCP client** — per-user tokens; every
  write action needs your approval.
- **Video Director** — compose shots in sections instead of one giant prompt.

*Alpha 0.0.13 · Linux x86_64 + NVIDIA · Windows native (experimental), WSL2 or
Docker ·
[Discord](https://discord.gg/avR4trp3b8) · [Reddit](https://www.reddit.com/r/PotionUI/) ·
[Ko-fi](https://ko-fi.com/A3B325D031)*

## 60 seconds to first image

```bash
git clone https://github.com/PotionUI/PotionUI.git potionui && cd potionui
./potionui doctor    # check prerequisites, with a repair command for anything missing
./potionui start     # create the venv, install deps, launch backend + frontend, print the URL
```

Native Windows (experimental): `.\potionui.cmd doctor` and `.\potionui.cmd start`
from PowerShell or cmd — see [Windows (native)](#windows-native).

Floor: 8 GB VRAM + 16 GB RAM (SDXL). Full requirements below.

## Contents

- [What you're looking at](#what-youre-looking-at)
- [Multi-user, with an admin panel](#multi-user-with-an-admin-panel)
- [AI assistant](#ai-assistant)
- [The generate workspace](#the-generate-workspace)
- [History, tags, and collections](#history-tags-and-collections)
- [Video Director](#video-director)
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
| YuE2               | Audio (song, lyrics + style tags) | [docs/models/yue2.md](docs/models/yue2.md)                     |
| SeedVR2            | Image & video upscale / restore   | [docs/models/seedvr2.md](docs/models/seedvr2.md)               |

## Multi-user, with an admin panel

![Admin preset management: presets grouped by category and engine, with one preset's overview showing its description, example media, metadata, and the recipe that installs it](docs/media/potionui-admin-presets.png)

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

![The generation assistant reads the active tab and proposes a rewrite for each prompt segment, each one applied with one click](docs/media/potionui-generation-assistant.png)

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

![Applying a formula: a preview of the form values it will change, each shown from old to new, before they land in the current tab](docs/media/potionui-formulas.png)

- Every tab is its own sandbox — preset, mode, prompts, and results — so you
  can run several ideas side by side without losing any of them.
- Switch models and the form swaps to match; the same tab handles txt2img,
  img2img, inpainting, or a video/audio mode.
- Like a setup? Save it as a **session** — preset, mode, prompts, and form
  values — and pull it back up any time.

## History, tags, and collections

![History with collections, search, media-type and preset filters, and date-grouped image and video renders](docs/media/potionui-history.png)

![Generation details: the render beside its parameters, seed, and the exact model files it used](docs/media/potionui-generation-details.png)

![The image editor, opened from History: paint, selection, and crop tools, layers, and color adjustments, saved as a new upload so the original stays untouched](docs/media/potionui-image-editor.png)

https://github.com/user-attachments/assets/f46cde26-0288-4e05-ac90-c3be31f0d2dd

- Everything you generate is saved automatically, with the exact parameters
  that produced it.
- Tag generations to group and re-find them; bulk-delete by tag to clear out
  throwaway experiments.
- Sort work into collections, and compare two results side by side.
- Fix a picture without leaving the app: Tools → Edit image adjusts, crops,
  paints, and layers it, then saves a new copy. The same editor opens from
  any media field in a preset form.

## Video Director

![The Video Director on a cloud video model: three chained shots, each starting from the last frame of the one before, with the model's limits listed above them](docs/media/potionui-cloud-video-director.png)

- Build a shot out of **global, timed, and chained sections** instead of
  hand-managing separate prompt fields per segment.
- Same segment-card editor as everywhere else in PotionUI, aimed at a
  timeline.

## Prompt tooling

![The phrasebook: a shot-type category with a preview image for every value, generated from a template prompt on a chosen preset and session](docs/media/potionui-phrasebook.png)

- **Segments** — save a prompt fragment once, drop it into any prompt; group
  related segments into templates.
- **Phrasebook** — autocomplete for known terms (art styles, camera angles,
  lighting) with per-chip shuffle for quick variation.
- **Dynamic prompts** — `{a|b}`, weighted choices, `${vars}`.
- **LLM enhancement** — optional, layered on the same editor.

## Automations

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
> **Linux x86_64 with an NVIDIA GPU** is the tested 0.0.13 matrix. **Native
> Windows is supported experimentally** as of 0.0.8: the installer, the CLI,
> the backend test suite, the frontend checks and the E2E harness all run in
> CI on `windows-latest` — see [Windows (native)](#windows-native) below.
> WSL2 and Docker Desktop work too. Details in
> [Supported platforms](#supported-platforms).

PotionUI can generate on this machine's GPU, dispatch to a remote worker, or
both — the `./potionui` CLI has an install preset for each.

**Pick an install:**

| You have                                              | What gets installed                                                 | Command                             |
| ------------------------------------------------------ | ----------------------------------------------------------------- | -------------------------------------- |
| A GPU in this machine                                 | Full CUDA stack                                                    | `./potionui start`                     |
| A GPU here, plus room to add remote workers later      | Full CUDA stack                                                    | `./potionui start --profile hybrid`    |
| No GPU here (a VPS or laptop) — dispatch to a worker    | CPU-only PyTorch, no CUDA libraries — **not** a CPU-generation mode | `./potionui start --profile remote`    |
| A GPU box that only serves another PotionUI instance    | Full CUDA stack, no frontend                                       | `./potionui worker start`              |

On native Windows every command reads `.\potionui.cmd …` instead of
`./potionui …`; the profiles are the same.

You need:

- **Python 3.12** (3.13 works only by compiling numpy from source; the shim prefers 3.12 when both exist), and **Node.js 18+** for every preset except Worker.
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
| Linux x86_64 + NVIDIA CUDA  | Tested and supported for 0.0.13                                                              |
| Windows via WSL2            | Should work — same Linux CUDA stack, just unverified; a success/failure report would help   |
| Windows native              | Experimental (0.0.8) — installer, CLI, backend suite, frontend checks and E2E harness run in CI on `windows-latest`; see [Windows (native)](#windows-native) |
| macOS                       | No — local generation needs CUDA; the native engine has no MPS support                      |
| AMD GPU (ROCm)              | No — the pinned dependency stack is CUDA-only                                               |
| Docker                      | Supported — see below (on Windows, Docker Desktop runs this via WSL2)                       |

### Windows (native)

Experimental as of 0.0.8. The `windows-latest` GitHub Actions workflow
([`.github/workflows/windows.yml`](.github/workflows/windows.yml)) covers
the install and test matrix below.

**Prerequisites:**

- Python 3.12 from [python.org](https://www.python.org/downloads/windows/)
  (the `py` launcher works too; 3.13 is not yet usable because the pinned
  numpy has no 3.13 wheels).
- Node.js 20.
- Git.
- For GPU generation: an NVIDIA driver version 580 or newer (the floor the
  CUDA 13 torch build in `constraints.txt` needs — see
  [Hardware Requirements](docs/user/hardware-requirements.md)).

```powershell
git clone https://github.com/PotionUI/PotionUI.git potionui
cd potionui
.\potionui.cmd start
```

**Exercised in CI on `windows-latest`:** `doctor`; the `remote` install
profile (CPU-only PyTorch, no CUDA) booting end to end — install, backend
start, health check, owner registration, preset listing, stop — on a fresh
checkout; the full backend pytest suite and the marketplace plugin suites;
the release gate without GPU steps; the frontend type-check, unit, component
and build steps; and the HTTP + Playwright E2E harness.

**Known limitation:** Triton and `torch.compile` have no Windows wheels, so
attention runs on plain SDPA and the compile optimizations stay off.

**Reporting a Windows issue:** open a GitHub issue with:

- The output of `potionui.cmd doctor --json`.
- The files under `logs/`.
- The output of `nvidia-smi`.
- What happened when you generated with the smallest available preset.

```bash
docker run --gpus all -p 26730:26730 ghcr.io/potionui/potionui:latest
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
source venv/bin/activate          # Windows (native, experimental): venv\Scripts\activate
pip install -r requirements.txt -c constraints.txt
python api.py                     # serves on http://localhost:26730

# Frontend
cd frontend
npm install
npm run dev                       # dev server on http://localhost:26731
```

Or run both together with `./run.sh` (assumes the venv and `node_modules` are
already installed — `./potionui start` does not):

```bash
./run.sh                          # backend :26730, frontend :26731
./run.sh 26730 26731 --lan        # also accept connections from the local network
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

The three most recent releases; older history lives in the
[commit log](https://github.com/PotionUI/PotionUI/commits/master).

### 0.0.13 — 2026-09-30

- Model folders: adding a folder detects which tool it belongs to (ComfyUI, A1111/Forge/SD.Next,
  StabilityMatrix, Fooocus, SwarmUI, Pinokio) and maps its subfolders to model types, with an
  override; a type can use several subfolders of one folder (such as Lora and LyCORIS) with one
  of them receiving downloads; folders the tool's own config points to are offered as extra
  folders, including Windows drive paths seen from WSL; any subfolder can be bound to a type by
  browsing or typing a path, in the setup wizard and in Admin → Models → Folders; Detect again
  adds only what is missing, and a single subfolder can be removed.
- Model types: checkpoints and diffusion models sharing one folder, as in Forge and
  StabilityMatrix, are told apart by reading the file header; Flux, Qwen, Z-Image, Krea-2, Wan,
  Anima, SeedVR2 and MiniMax-H3 pickers also list full checkpoints of those models and load only
  their diffusion model weights; files nothing recognises are listed as needing a type, and
  admins can set or reset a model's type from its detail page, kept across rescans; ComfyUI's
  unet and latent_upscale_models folders count as diffusion models and upscalers.
- Content safety: every model can be Allowed, Blurred or Blocked per instance and per user
  group, with a Restricted content group that is always blocked; outputs are rated by the image
  tagger before they are saved or sent, and a blocked generation shows a calm notice with the
  reason.
- Sessions: sessions open in a drawer with search, a current-session card with Save, Save as
  new and New, and a history whose rows show the prompt and what changed; sessions can be
  pinned to the top.
- Admin: Admin → Generations shows every running and queued generation from all users,
  including recipe test renders, with Stop, Remove and Stop all.
- Windows: `./potionui start` falls back to a free port when the default is taken, and
  `doctor` names the process holding a port and checks for the Visual C++ runtime that PyTorch
  needs.
- Fixes: model downloads trust the operating system certificate store on Windows and macOS
  (`SSL_CERT_FILE` overrides it), and a certificate failure stops retrying and explains the fix;
  downloads use the detected provider and appear in lists as soon as they finish; stopping a
  generation really stops it, never reports cancelled when the backend refused, and a stop
  sent just before a run starts is honoured; recipe downloads can be approved when a provider
  has no API key yet; plugin settings and new model folders no longer crash on empty fields;
  the NSFW setting saves again; clearing all chat sessions in Admin works again.
- Upgrading: the default ports change to 26730 (backend) and 26731 (frontend) because Windows
  Delivery Optimization holds 7680; set `BACKEND_PORT`/`FRONTEND_PORT` or pass
  `--backend-port`/`--frontend-port` to keep the old ports, and update bookmarks to
  `localhost:26731`; the database gains model type, folder binding, session pin and content
  policy migrations on first start, so take a backup first.

### 0.0.12 — 2026-09-29

- Model folders: models live in one or more folders you pick in the setup wizard or
  Admin → Models → Folders (an existing ComfyUI or A1111 folder, an external drive, any
  folder), scanned in place without symlinks or copies, with Windows paths supported;
  each model type chooses its download folder and which folder wins when a model is in
  several; folders can be read-only, reordered, relinked or removed, and an offline
  folder fails a generation by name instead of silently; folder names are recognized in
  any case and under a `models/` subfolder.
- Model indexing: runs one at a time with live progress in the setup wizard, Admin →
  Models and Admin → Backends, where every row now has Test connection, Index models and
  Make default; the summary lists skipped same-file copies with the file that was kept,
  models found in more than one folder and name conflicts; a restart finishes an
  interrupted index, so recipes stop reporting installed models as missing.
- Downloads: a CivitAI model page link picks the CivitAI provider and its credentials by
  itself and resolves to the real file; downloads are named after the provider's file
  or the server's filename instead of a number, keep a name you typed, and never
  overwrite another download; the downloads list updates live instead of staying on
  Pending until a reload; the download dialog says when a model type has no folder it
  can write to.
- Generate: closing a tab asks first and warns about a running generation or unsaved
  work; the model picker folds its Suggested downloads into one short list with a
  count, and marks what only admins see.
- Chat: long conversations keep their history instead of being cut to fit the answer;
  Ollama always receives the configured context window.
- Admin: Settings → Storage is split into Media storage, Backups and Housekeeping.
- Fixes: a picker opened from a filter menu or a modal shows above it; CivitAI's Fetch
  prompts result can be closed again; a session no longer shows unsaved changes right
  after saving; framed inputs show one focus ring instead of a white square inside;
  Index models and Test connection spin again while running.
- Upgrading: four database migrations run on first start (model folders, logical model
  references, model file paths moved to folder locations, download filename choice) and
  cannot be undone by downgrading; symlinked model folders are converted into model
  folders on first start and keep downloading to the external drive; the first scan
  reuses existing hashes, so nothing is re-hashed; plugins that read model paths use
  `src.plugin_api.models`, as the old models location API is removed.

### 0.0.11 — 2026-09-25

- Prompts: prompt segments are quieter cards with a pinnable action menu, join with a
  single space instead of a comma, and no longer have BREAK segments; presets can declare
  their own prompt syntax, colored as you type (tones or concrete colors) and inserted
  from a `/` picker, with MiniMax-H3, Qwen-Image and YuE2 shipping their palettes;
  typing `#`, `$`, `@` or `/` opens a raised picker anchored at the caret with preview
  thumbnails and a larger preview of the selected image; Tab or Browse all opens a
  browse modal showing where the value will land; clicking a chip opens the same modal
  to change it, with its behavior, shuffle, exclude-from-shuffles and remove controls in
  the footer; phrasebook values lead with the value text, show their preview images as a
  grid with an in-modal carousel, highlight search matches and search by regular
  expression; typing in long prompts is several times faster.
- Prompt references: presets can map media fields to reference tokens, so `@` in a
  segment points at a reference image, video or audio by name (`<Picture 2>`), keeps
  pointing at the same item when you reorder them, blocks generation when the item is
  gone, and shows on each form tile with its handle and how often the prompt uses it;
  PotionAI's plain tokens become references when you apply its text.
- Video Director: a global prompt panel shown read-only in every shot, a clean header,
  FPS in the form, a single-body shot editor and shot tabs on one card layout
  (keyframe, audio, IC-LoRA); in MiniMax-H3 reference mode each shot uses exactly the
  references its prompt cites, numbered per shot; switching modes keeps each mode's
  shots; finished shots show their video as the thumbnail.
- MiniMax-H3: each LoRA row can leave the audio stream alone ("Affects audio"), which
  keeps a video LoRA from degrading the soundtrack.
- YuE2: the model's ABC transcription is shown as a text artifact you can copy or apply
  back to the form to edit the score; the ABC field is monospaced and checks its header
  lines; lyric section tags are highlighted.
- Chat: `@` references reach a single LoRA row of the form or any model in your
  library, with its trigger words, strength and description; memory has a session scope
  that follows the chat's context tab; generations approved in chat stream into the tab's
  Workbench; prompt variables resolve in chat generations like the Generate button.
- Models: search takes `*` and `?` wildcards and regular expressions, type and tag
  counts follow the other filters, admins can filter by index date and usage, see Uses
  and Last used columns and tag a selection in bulk; model pickers suggest the download
  variant that fits your GPU and model pages list the other variants, with uploader
  attribution; recipe downloads let you pick a variant per model slot.
- Recipes: each recipe shows whether its models and presets are really installed; starting a
  recipe while another runs offers to open or cancel the running one; test generations report
  the real error and feed each model field the right file; recipe steps show inline in the
  run view with a runs table.
- Admin: every tab shares one library, table and detail layout with bulk actions in a
  floating selection bar; generations and chat sessions can be deleted in bulk; lists
  show an error with Retry instead of looking empty when loading fails; presets and
  recipes link to each other; LLM configurations pick the model from the provider's
  live list (Ollama, OpenAI-compatible) with search; every confirm dialog takes Enter
  and Esc.
- Generation errors: users see a safe message, a hint and an error ID; admins get the
  failed pipe, step and traceback, a category filter in Admin → Generations, a
  Generation failed automation trigger and optional admin notifications.
- Reliability: hashing a new model no longer freezes the server; CivitAI fetches retry
  transient errors and report the real reason; finished generations get their system
  tags without a manual run; logs no longer contain session tokens; reloading warns
  before losing changes not saved to the session.
- Fixes: phrasebook chips with a hyphen in their path render as chips; a chip placed
  after a reference no longer corrupts it; raw preset ids no longer appear in titles and
  cards; the History sidebar stays visible while scrolling; folding the left panel gives the prompt a proper width instead of stretching the Workbench; CivitAI's Fetch prompts
  dialog enables and closes again; audio and video references show a proper thumbnail
  instead of a broken image.
- Upgrading: five database migrations run on first start (segment references,
  generation failure detail, pinned segment actions, BREAK segment removal, admin
  failure alerts); install the new `google-re2` dependency (`pip install -r
  requirements.txt`); Docker images are also tagged with the v-prefixed version; log files written by earlier versions may contain session tokens,
  so delete or rotate them; images generated before this version can be tagged once from
  Admin → media index; the Spectral Progressive Diffusion option is gone from the Flux2 and
  Z-Image presets (saved settings that still carry it keep working).

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
open. News, showcases and questions also go to
[r/PotionUI](https://www.reddit.com/r/PotionUI/).

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
