---
title: Administration
order: 90
---

# Administration

The **Administration** area is available only to accounts with admin rights. If you have it, you'll find it in the sidebar and linked from **Settings**. This page is a brief tour of what each admin tab does. Regular users won't see any of this, and nothing here is needed for everyday generating.

Administration is organized into tabs:

## System Settings

Global application settings for the server — the top-level configuration that applies to everyone, organized into sections in the left rail: Access, Content Safety, Media storage, Backups, Housekeeping, Search & Tagging, Generation, Diagnostics, External Login, and Logs.

### Media storage

Where generated files are written (local disk or S3) and the thumbnail settings below live together in this section.

Gallery previews are made at the moment a generation is written to disk. Which sizes get made, and how a video's moving preview is encoded, is set here, because a moving preview has no compression between frames and is by far the largest thing an install writes for each generation.

Three named profiles cover the usual choices:

- **Compact** — one small preview and a short, low-frame-rate animation. The smallest on disk.
- **Balanced** — the default. One medium preview and a smoother animation.
- **Full** — all three sizes at the highest frame rate. What earlier versions always did.

You can also set the individual values yourself, which shows as a custom profile. Saving a change leaves what's already on disk alone — it only affects previews made from then on. **Regenerate** re-renders every existing preview to match the current settings, one file at a time, and deletes the sizes the new settings no longer produce. You can cancel it; it stops after the file it is on. The gallery keeps working throughout, because a request for a size that no longer exists falls back to the nearest one that does. The page also shows how much disk space previews currently take — a figure available only when files are stored on local disk, and not reported for S3 storage.

### Housekeeping

Three things grow without a limit unless you set one: the scratch folder, where in-progress previews and intermediate video files are written; the run report kept for each generation; and the call traces recorded for chat. Each has a retention window in days, and 0 keeps everything.

Next to each window is what it would remove right now, so you can see the effect of a change before you save it. A pass runs shortly after the server starts and once a day after that. **Run now** applies all three immediately and reports what it freed; the last pass is shown alongside. Deleting a run report also deletes the images it saved, and nothing outside the scratch folder is ever touched.

### Backups

Where backups go, how many are kept, and what a backup takes. The destination is a directory, relative to the install or absolute; it is created when you save it, and the panel says so if it can't be written to. Retention is a count of archives to keep, and 0 keeps all of them. The media mirror beside them is never pruned.

Three tiers, each a superset of the one before it: **config** is the database, the encryption key, `.env`, your local presets, plugins and automation, and the small storage trees, all as one zip; **media** adds a directory mirror of your uploads and generations; **all** adds a mirror of the models directory.

**Backup now** takes one at the default tier, in the background, and lists it when it finishes. Below the list is a cron line for the same backup on a schedule, ready to copy. Restore is not here: the app has to be stopped for it, so it runs from the command line — see [Backup & Restore](backup-and-restore.md).

### Diagnostics

- **Record a performance profile for each generation** (`profiling_enabled`, off) — replaces the `POTIONUI_PROFILE` environment variable. Each generation writes `profiles/<generation id>/profile.jsonl` and `generation.log` under file storage: stage timings, RAM and VRAM over time, and model events. Admins open a profile from the workbench's **Resource profile** button, which shows the report and offers both files for download; `scripts/profile_report.py <profile.jsonl>` prints the same report as text.
- **Include a memory census at the end of each run** (`profiling_census`, on) — adds a count of every live tensor, on the CPU and the GPU, to each profile. It runs in the background about a second after the generation reports complete, so it doesn't delay results. If another generation starts first, the census is skipped and the profile records a `census.skipped` event instead.

Both take effect on the next generation without a restart. On the first start after updating, profiling is switched on once if `POTIONUI_PROFILE` was set; after that the variable is ignored.

## Models

Server-side model management: reviewing installed models and, together with the **Model Downloader** plugin, adding new ones. This is the admin counterpart to the user-facing **Models** page.

Its **Folders** section is where model files live and how PotionUI finds them. Models don't have
to sit in one place: PotionUI keeps a list of **folders** (the built-in `models/` folder plus any
you add), each of which can hold any number of model types.

**Add folder** takes a path — an existing ComfyUI or A1111 install, a folder on another drive, a
mounted network share — and **Detect** reads it, shows which types it recognises (checkpoints,
LoRAs, VAEs, and so on) with a file count for each, and pre-ticks the ones it found. Tick or
untick types, and turn on **Download new models here** if new downloads for those types should
land in this folder rather than the default. **Add folder** creates it; indexing starts in the
background so its models show up in the picker and the Models list as soon as it finishes.

Each folder in the list can be expanded to rename it, relink its path (if a drive letter or mount
point changed — nothing is re-hashed, so this is instant even for a huge library), mark it
**read-only** so PotionUI never writes into it, remove a type it no longer holds, or delete the
folder entirely (its models become unavailable, not deleted — the files stay exactly where they
are). A folder's live state — **Online**, **Offline**, or **Unreadable** — is shown next to its
name, with **Probe now** to recheck it immediately (useful right after plugging in a USB drive).

When more than one folder holds the same model type, **Model type order** lets you set which one
is checked first (so the same LoRA in two folders has one clear winner) and which one is the
**write** folder for that type. **Suggested folders** lists any folder with model files PotionUI
found but that isn't part of a folder entry yet — **Add as root** turns the suggestion into a
proper entry without re-downloading or re-hashing anything already indexed.

## Presets

Manage which presets are installed and **who can use them**. Admins see the list of presets (with installed / not installed status), filter by name and type, and control access by assigning presets to individual **users** and **groups**. This is how you make a preset available to some people but not others.

## Backends

Configure where generations actually run. An admin can add and enable multiple backends and pick the right type for each:

- **ComfyUI** — connect to a ComfyUI server (host, port, HTTPS/SSL options).
- **Remote HTTP** — a remote generation endpoint, with optional authentication.
- **RunPod** — a RunPod-hosted backend.

Each backend can be enabled or disabled, and users can select an available backend when generating.

### Optimizations (native backends)

Every native backend, the local one and each remote native worker, has its own **Optimizations** tab with the engine switches that used to be environment variables. They belong to that backend's configuration, so two backends can be set differently. A remote worker receives its backend's values with every job, so the worker's own environment never decides them. Most apply to the next generation as soon as you save; each row says when it applies.

**Speed**

- **fp8 fast multiply** (`native_fp8_matmul`, off) — run fp8 checkpoints on fp8 tensor cores (RTX 40/50 and newer). Next generation.
- **nvfp4 fast multiply** (`native_nvfp4_matmul`, off) — run nvfp4 checkpoints on fp4 tensor cores (RTX 50 and newer). Next generation.
- **Fuse LoRAs into the model** (`native_lora_fused`, on) — apply a layer's LoRAs in one fused step; off is only for comparison. Next generation.
- **Compile the model (torch.compile)** (`native_torch_compile`, off) — compile the model's repeated block when it is fully on the GPU. Next model load.
- **Qwen3 text encoder in bf16** (`native_qwen3_te_bf16`, off) — run the Qwen3 text encoder in bf16 instead of fp32. Next generation.
- **Sol-Attn implementation** (`native_sol_attn_backend`, Flex) — Flex or Kernel. Applies after a restart; for a remote worker, restart the worker.

**Memory**

- **Prefetch streamed layers** (`native_stream_prefetch`, off) — copy the next streamed layer to the GPU while the current one computes, when a model doesn't fit in VRAM. Next generation.
- **Shrink bf16 models to fp8 at load** (`native_fp8_quantize`, Auto) — Auto converts only when that makes the model fit; Off never does; Always always does. Next model load.
- **VRAM reserve for sampling** (`native_min_inference_memory_gb`, 1.0 GB) — VRAM kept free on top of the model weights for the sampling step. Next generation.
- **LTX decode tile size** / **LTX decode tile length** (`native_ltx_decode_tile_px` / `native_ltx_decode_tile_frames`, 0) — a fixed tile for the LTX video decode, in pixels and frames; 0 sizes it from free VRAM. Next generation.

**Debug logging**

- **Log every Sol-Attn call** (`native_sol_attn_debug`, off) — log the shape and routing of each Sol-Attn call. Applies from the next call.

The tab also keeps the **Attention backend** pin (`native_attention_backend`, auto), which forces one attention kernel instead of letting PotionUI pick the fastest available; for a remote backend it is a plain select. It applies to the next generation.

These switches replace the environment variables `NATIVE_FP8_MATMUL`, `NATIVE_NVFP4_MATMUL`, `NATIVE_LORA_FUSED`, `NATIVE_TORCH_COMPILE`, `NATIVE_QWEN3_TE_BF16`, `NATIVE_SOL_ATTN_BACKEND`, `NATIVE_STREAM_PREFETCH`, `NATIVE_FP8_QUANTIZE`, `NATIVE_MIN_INFERENCE_MEMORY_GB`, `NATIVE_LTX_DIFFUSION_TILE_PX`, `NATIVE_LTX_DIFFUSION_TILE_FRAMES` and `NATIVE_SOL_ATTN_DEBUG`, and `NATIVE_ATTENTION` no longer overrides the attention pin. On the first start after updating, every existing native backend is filled in once from the variables you had set, so nothing changes; old on/off/auto values become plain on/off, with auto as on. Backends you add later start from the defaults. After that the variables are ignored, and each one still set logs a startup line saying where its setting now lives.

## Users

Create and manage user accounts. Admins set the **account type** (Administrator or Regular User), manage credentials, and control what each user can access by assigning **presets** and **LLM configurations** to them individually.

## User Groups

Manage groups as a way to assign access in bulk. A group has a name and description and a set of **members**, and admins can assign **presets**, **LLM configurations**, and **models** to the whole group at once — simpler than configuring each user separately.

## LLM Configuration

Set up the language-model providers that power prompt assistance and chat features. For each configuration an admin sets the **type** (such as Ollama or OpenRouter), a **base URL** and **API key** where needed, the **model** name, and tuning options like **max tokens**, **temperature**, a **system message**, whether it **supports vision**, and any pre-chat actions. Configured LLMs can then be assigned to users and groups. See the **LLM Assistant** for how these are used day to day.

A **Native** configuration runs a model in-process instead of through Ollama/OpenRouter. Picking type **Native** replaces the free-text model field with a **Checkpoint** picker listing the HF-layout checkpoints found under `models/llm/` (an unusable one is still listed, disabled, with the reason it can't be used); a checkpoint that was previously selected but has since disappeared from disk stays selected and is called out with a note, rather than being silently swapped or cleared. A **Native Options** section then offers: **Thinking mode** — **model default** (leave the loaded chat template's own behavior alone), **enabled**, or **disabled** — which only takes effect for a checkpoint whose loaded chat template actually exposes an explicit thinking/reasoning switch (the Qwen3 family, for example); every reply reports which mode actually applied, so requesting a mode a checkpoint doesn't support is never silently ignored. **Quantization**, offered only from the modes the selected checkpoint actually supports. **Context window**, the token capacity the context-budget preflight is told to use for this config — not a promise the model actually fits that many tokens. **Top-K**, **Top-P**, **Min-P**, and **Repetition Penalty** are explicit sampling controls, each blank by default; leaving one blank passes nothing for it and the checkpoint's own generation defaults apply. Setting temperature to 0 selects greedy decoding, which ignores Top-K/Top-P/Min-P but still honors an explicit Repetition Penalty. These are manual tuning knobs, not an automatically selected preset, a guaranteed fix for repetitive output, or a throughput optimization — see the LLM chat evaluation pack for measuring their effect on a given model. The selected checkpoint's vision support and whether it's a shared text encoder are shown alongside these as read-only badges.

Ollama configurations expose the same **Top-K**/**Top-P** plus a **Min-P** field (also blank by default) under **Ollama Options**, sent straight through as Ollama's own `top_k`/`top_p`/`min_p` request options. The same section's **Thinking Mode** offers **Automatic**, **Enabled**, **Disabled**, or a named level (**Low**/**Medium**/**High**, model-dependent). Automatic is legacy behavior, not a provider limitation: it turns thinking off whenever a call puts native tool calls on the wire and leaves it on otherwise. Any explicit choice — Enabled, Disabled, or a level — now always applies instead, whether or not that call uses tools; previously an explicit choice was silently discarded on any tool-calling request. Which levels a given model actually honours, and whether it can disable thinking at all, depends on the model.

## Plugins

Install, inspect, enable, and disable plugins. Admins can scan for installed plugins, review each plugin's author, description, and registered hooks, open its settings, and toggle it on or off. See **Plugins** for what the shipped plugins do.

## Developer

A reference and inspection area for people building presets and plugins. It documents the available form field types, template functions, icons, and Jinja2 templating syntax, with live previews of rendered output. It's a lookup tool for authors rather than something you change to run the app.

## Server logs

Everything the server logs to the console is also written to a plain-text file at `storage/logs/potionui.log`, so you have a copy to search or attach to a bug report even after the console scrollback is gone. Each line carries a timestamp, the level, the logger name, and the message, for example:

```
2026-09-09 14:03:11 |     INFO | is | message
```

The file rotates by size: once it passes the size limit it's renamed `potionui.log.1`, older backups shift up one number, and the oldest backup beyond the kept count is deleted. Rotation and console output are independent — the console keeps showing everything regardless of what's happened to the file.

A few environment variables tune this, set before starting the server:

- `POTIONUI_LOG_LEVEL` — the minimum level logged, for both the console and the file. Defaults to `INFO`.
- `POTIONUI_LOG_DIR` — where the log file is written. Defaults to `storage/logs`; setting it to an empty value turns file logging off.
- `POTIONUI_LOG_MAX_BYTES` — the size, in bytes, at which the file rotates. Defaults to 20 MB.
- `POTIONUI_LOG_BACKUP_COUNT` — how many rotated backups are kept. Defaults to 10.
- `POTIONUI_LOG_FILE=off` — turns file logging off outright, for a read-only filesystem or a console-only setup.

If the log directory can't be written to, the server logs a single warning to the console and keeps running with console output only — a logging problem never stops generation.

The most recent lines are also viewable without shell access, at Admin → System Settings → Logs.

---

Note that some plugins add their own tabs to Administration, so your install may show more than the tabs listed here.
