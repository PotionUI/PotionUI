# PotionUI

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/A3B325D031)
[![Discord](https://img.shields.io/badge/Discord-Join-5865F2?logo=discord&logoColor=white)](https://discord.gg/avR4trp3b8)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Status: Alpha](https://img.shields.io/badge/Status-Alpha-orange.svg)](#)

**The self-hosted generation studio you can hand to other people.**

Run Krea-2, Anima, SDXL, Flux, Wan, LTX, Qwen-Image, MiniMax and YuE2 on one box — then give your team, your household, or your agents their own logins, presets, and limits. No node
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
- **Video Director** — compose shots in sections instead of one giant prompt.

*Alpha 0.0.11 · Linux x86_64 + NVIDIA · Windows native (experimental), WSL2 or
Docker ·
[Discord](https://discord.gg/avR4trp3b8) · [Ko-fi](https://ko-fi.com/A3B325D031)*

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
> **Linux x86_64 with an NVIDIA GPU** is the tested 0.0.11 matrix. **Native
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
| Linux x86_64 + NVIDIA CUDA  | Tested and supported for 0.0.11                                                              |
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
source venv/bin/activate          # Windows (native, experimental): venv\Scripts\activate
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

The three most recent releases; older history lives in the
[commit log](https://github.com/PotionUI/PotionUI/commits/master).

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

### 0.0.10 — 2026-09-23

- Models: Qwen-Image-2.1 runs natively with a preset, resolutions and a starter
  recipe, for text-to-image and for edits guided by one or more reference images;
  it keeps real transparency, so transparent outputs stay transparent in previews,
  thumbnails and downloads, with a checkerboard behind them in the gallery; text and
  reference rows are computed once per run instead of every step; the colored
  borders along image edges are gone; checkpoints that store the feed-forward
  weights split load as well as the single-file release.
- MiniMax-H3: a Fast variant, a latent upscale mode with its own Enhance tab whose
  refine LoRAs add to the base ones, an int8 transformer option, gate compression and
  a start percent.
- Prompts: the Prompt Library is a set of card grids with filters for prompts,
  segments, templates and categories, with detail views, a picker that applies a
  saved prompt, and usage and collections on each prompt; prompt variables can carry
  conditional options that are only offered when another variable rolled one of the
  given values, take any number of options, copy and paste as JSON, and are kept with
  saved prompts; director segments expand variables; the variables manager is wider,
  with Copy and Paste in its footer and Cancel undoing the edits made since opening.
- Admin: Presets, Recipes and Plugins are library pages with a category sidebar, one
  shared filter bar and panes that fill the screen; presets show as poster cards with
  their cover; every other admin list searches, filters and sorts through the same
  filter bar and keeps its filters in the address; a recipe's category is required
  and uses the preset categories.
- CivitAI: model prompts can be fetched in bulk into the prompt library, including
  the prompts of community images; downloads prefer the provider's own source over a
  checksum match.
- Plugins: the new A1111 metadata export marketplace plugin downloads selected
  generations as a ZIP of PNGs with A1111/Forge-style parameters embedded; plugins
  open dialogs through a shared modal and can add actions to the generation details
  header; plugin tools on the History and Library pages load again; shipped preset
  examples are smaller WebP images.
- Interface: every page header uses the same title with its count, detail views put
  Back on the left, and the sidebar folds pages that do not fit its height into a
  More menu.
- Reliability: a failed generation releases its GPU placement; a text encoder
  skipped because its conditioning was cached stays in memory instead of being
  reloaded from disk on the next prompt change; prompt duplicate detection runs in
  bounded memory without blocking the server and reports a partial scan; admin polls
  pause in hidden tabs.
- Fixes: Kohya LoRAs with text-encoder weights load on SDXL again; SDXL's base model
  field is required and a request without it is refused with a field error; page
  buttons on the History and Library grids respond again next to drag-select; the
  form overrides tab keeps model pickers for hidden or pinned fields.
- Upgrading: two database migrations run on first start, one storing variables with
  saved prompts and one recording whether a file has transparency; the A1111 export
  left the CivitAI plugin, so enable the A1111 metadata export plugin to keep it.

### 0.0.9 — 2026-09-18

- Login: a generic OpenID Connect provider ships as the `oidc-auth` marketplace
  plugin, so any OIDC identity provider such as Keycloak, Authentik or Entra can sign
  users in with a "Continue with" button; accounts it creates carry an email only when
  the provider verified it, see a notice on the settings page instead of a password
  form they cannot use, and the login page says so when an attempt is refused.
- Admin: model cards get a per-model fetch button that pulls the description,
  preview media and trigger words from CivitAI and updates the card in place, filling
  only what is still empty; model details list the model's mirrors with a link to the
  provider page; plugins can add their own actions to a model card; the global Index
  Models and bulk provider fetch buttons are gone from the Models header, unindexed
  files now link to Backends; plugin settings forms can show info notices, and the
  plugin detail pane refreshes after a scan.
- Generate: models added by a local download, a recipe's index step or a model job
  are registered on the local native backend right away, so they no longer disappear
  from the pickers once a backend has been indexed.
- History: tile actions are separate buttons with tooltips, like model cards.
- Performance: the built frontend is served gzip-compressed with hashed assets cached
  and the shell always revalidated; model and prompt lists load their providers,
  tags, files and segments in batches instead of per row; stats, library and session
  requests run their database work off the event loop; generation progress ticks no
  longer wake the active tab, session tracking or tab persistence unless something
  they read changed; new indexes speed up run-report retention, the favorites page,
  model path lookups and newest-first prompt search.
- Reliability: media index queue claims are a single atomic statement, so
  overlapping workers never take the same item.
- Fixes: boolean settings submitted as the string "false" are stored as false;
  CivitAI trigger words come from the model's trained words instead of being mixed
  into its tags.
- Upgrading: two database migrations run on first start, one adding a local-password
  flag to accounts and one adding the new indexes while dropping twelve that only
  duplicated a unique constraint; admins who relied on the header-level Index Models
  button now index from Admin → Backends.

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
