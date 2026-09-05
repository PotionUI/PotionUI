---
title: Hardware Requirements
order: 72
---

# Hardware Requirements

PotionUI runs on a single CUDA-capable NVIDIA GPU. What you can actually generate — and how well — depends on that GPU's VRAM and, just as much, on the host's system RAM. This page gives honest numbers instead of "a GPU with enough VRAM."

## GPU generation and driver

PotionUI requires **Turing or newer GPUs** (sm_75 and above). CUDA 13 dropped support for Maxwell, Pascal, and Volta architectures, so **GTX 10-series and older cards cannot run PotionUI at all** — RTX 20xx / GTX 16xx and newer are supported.

The **NVIDIA driver must be version 580 or newer** on your host machine. Older r550 or r570 drivers fail immediately with "CUDA driver version is insufficient for CUDA runtime version." Within the CUDA 13.x series, the 580+ driver remains compatible across minor versions, so a 580.65+ driver will continue to work as CUDA 13 updates ship.

For Docker deployments, the host needs only the NVIDIA driver and `nvidia-container-toolkit` — the torch PyPI wheel carries the CUDA 13 userspace runtime, so a separate host CUDA toolkit install is not required.

## The floor

**8 GB VRAM + 16 GB system RAM** is the supported minimum. On that floor, **SDXL** is the model family PotionUI guarantees works — it runs on `diffusers` pipelines with its own VRAM-tier memory policy (aggressive sequential offload + max attention slicing below 8 GB, model offload 8–12 GB, no offload 12–16 GB, fully GPU-resident at 16 GB+ — `src/platform/runtime/model_lifecycle/memory_policy.py`). Every other native-engine family (Flux, Krea-2, Qwen-Image, Wan, LTX, Z-Image, Anima, MiniMax-H3) needs more, some considerably more — see the per-family table below.

**Recommended: 16–24 GB VRAM + 32 GB system RAM.** This is what the native engine's low-VRAM design targets as "first class" (`docs/native-engine.md`), and it's comfortably above the RAM-cache floor (below) for realistic multi-model sessions.

The native engine (`src/platform/runtime/native/`) is explicitly designed for small GPUs, not just the 32 GB card it was developed on: `memory/tiering.py` places each component (DiT / text encoder / VAE) by its *actual* measured size, and a component that doesn't fit fully resident streams the overflow from pinned host RAM (partial residency) or gets quantized to fp8 on load instead of failing outright. That's what makes several families runnable — slowly — below their "comfortable" VRAM tier.

## System RAM is not optional headroom

PotionUI reserves system RAM the same way it reserves VRAM, and this bites on small boxes:

- **Before a load, the model RAM cache tries to reclaim free system RAM up to a reserve of `min(max(8 GiB, 10% of total RAM), 25% of total RAM)`** (`host_ram_reserve_gb()`, `src/platform/runtime/model_lifecycle/lifecycle.py`, applied in `_make_room_for_ram`). That's 2 GiB on an 8 GiB box, 4 GiB at 16 GiB, 8 GiB at 32 and 64 GiB, and 10% of total once total RAM exceeds 80 GiB (12.8 GiB at 128 GiB) — it scales down on small machines instead of reserving a fixed 8 GiB regardless of total RAM. It is a retention/eviction policy, not a guarantee that any particular model fits: cached entries are evicted until the reserve is met, and if pressure persists (for example when every remaining entry is leased by an active generation) the lifecycle warns and continues, so don't count on cross-generation caching surviving a genuinely tight box.
- **Low-VRAM streaming (partial residency) needs `streamed_gb + 2 GB` of free host RAM up front**, and refuses to start rather than risk an OS OOM-kill if it isn't there (`HostMemoryExhaustedError`, `_guard_host_ram_for_streaming` / `_STREAM_HOST_RESERVE_GB = 2.0`, `src/platform/runtime/native/engine.py:271,353-375`). A big model streaming several GB of overflow on a 16 GB RAM host can hit this guard even when VRAM itself would have been fine.

Practical takeaway: **16 GB system RAM is the bare minimum that keeps the app from refusing to load, not a comfortable amount.** 32 GB+ is what lets the RAM cache actually do its job (reuse a loaded checkpoint across generations instead of re-reading it from disk every time) and gives streaming headroom for the larger native families.

## Per-family verdicts

Peak-VRAM figures below marked **(32 GB-card ceiling)** are measured GPU runs on a 32 GB development card under fit-first placement (`docs/native-engine.md`) — they are *not* minimums, since a smaller card places the same model differently (more offload/streaming, lower peak, slower). Where no real-weight run exists yet, that's stated explicitly rather than guessed.

| Family | DiT size (bf16 unless noted) | Measured peak (32 GB-card ceiling) | 8 GB card | 12 GB card | Recommended |
|---|---|---|---|---|---|
| SDXL | few GB, single-file checkpoint | — (diffusers pipeline, not native engine) | **Works** — the supported floor | Works comfortably | 8 GB+ |
| Z-Image | ~11.5 GB bf16 (`zImage_turbo`, `docs/native-engine.md`) | CPU-validated only; no GPU e2e peak measured yet | Unvalidated | Unvalidated but the best native bet at this tier (smallest native DiT) | 12–16 GB |
| Krea-2 | 26 GB bf16 on disk (~12B DiT, `memory_policy.py` comment); fp8 quantize-at-load brings it to **~12.5 GB** (`docs/native-engine.md`) | 26 GB (streaming manual_cast tier, 1024² turbo) | No | **Viable via fp8 quantize-at-load** | 16 GB+ bf16, 12 GB fp8 |
| Flux2 / Klein | 9B params → ~18 GB bf16 estimated (params×2); fp8 checkpoint measured smaller | fp8: 28.7 GB · bf16: 28.55 GB (both include the Qwen3 TE, `docs/native-engine.md`) | No | **Viable but slow** — partial residency + fp8-auto, unmeasured at this tier | 16 GB+ |
| Qwen-Image | fp8 DiT file ~20 GB (`qwen_image_2512_fp8_e4m3fn`) | 19.37 GB (1024², true-CFG, `docs/native-engine.md`) | No | Marginal — close to the fp8 file size itself | 16 GB+ |
| Anima | not separately measured; small Qwen3-0.6B TE keeps total footprint below other families | 20.7 GB (1024², 24 steps, incl. causal-3D fp32 decode spike, `docs/native-engine.md`) | No | Marginal | 24 GB |
| Wan 2.2 (14B, dual-expert) | 14B per resident expert (`memory_policy.py`: `wan22 = 14.0`) | 15.2 GB (33 frames, 832×480, fp8 expert pair — short low-res clip, `docs/native-engine.md`) | No | No | 16 GB+; 5B ti2v variant is the realistic small-card option |
| Wan 2.2 (5B ti2v) | single dense model, coarser spatial granularity | not separately measured | Unvalidated | Realistic candidate — smallest Wan variant | 12 GB+ |
| LTX-2 / 2.3 / 2.5 | ~27 GB on-disk all-in-one AV checkpoint (19–22B params, `memory_policy.py`: `ltx2 = 27.0`) | Not GPU-validated end-to-end — DiT/TE built, AV forward + golden validation still open (`docs/native-engine.md`) | No | No | Not yet recommended at any tier — validation pending |
| MiniMax-H3 | 20B (pruned) / 33B (full) params, per its own doc front matter | **No local weights, no GPU validation performed at all** (`docs/models/minimax_h3.md`) | No | No | Not recommended — unvalidated, and weights are territorially license-restricted (excludes EU/UK/US/KR) |
| SeedVR2 (upscaler) | 3B fp8 **3.39 GB** / 3B fp16 6.78 GB / 7B fp8 **8.24 GB** / 7B fp16 16.48 GB (`content/presets/marketplace/SeedVR2/modes/upscale/tabs/generation.yml`) | Batch size auto-sizes to ~72% of free VRAM; halves and retries on OOM | 3B fp8 works | 7B fp8 works | Scales down cleanly to small cards — no fixed floor |

## Disk space

Budget for the checkpoint files themselves plus their text encoders and VAEs — native-engine families ship DiT, text encoder, and VAE as separate files, all of which sit on disk simultaneously once downloaded:

- SDXL: a few GB per checkpoint.
- Z-Image: ~11.5 GB DiT (bf16) + a Qwen3-4B text encoder.
- Krea-2: 26 GB DiT (bf16) + a Qwen3-VL-4B text encoder + the shared Wan 2.1 causal-3D VAE.
- Flux2/Klein: ~18 GB DiT (bf16 estimate) or a smaller fp8 file + an 8B or 4B Qwen3 text encoder.
- Qwen-Image: ~20 GB DiT (fp8) + a Qwen2.5-VL-7B text encoder.
- Wan 2.2 14B: **two** DiT files (high/low-noise experts, 14B each) + UMT5-XXL + VAE — the largest native disk footprint short of LTX/MiniMax.
- LTX-2/2.3: ~27 GB all-in-one file (DiT + both VAEs + vocoder + text-embedding projection). LTX-2.5 splits into separate DiT/TE/VAE files instead.
- MiniMax-H3: four separate files (DiT, text encoder, video VAE, audio VAE) — no confirmed sizes since no local weights exist yet.
- SeedVR2: 3.39–16.48 GB per DiT variant (see table above) + a ~501 MB VAE.

None of these are downloaded by PotionUI itself unless you ask — see [Models](models.md) for how installation works.

## Three different numbers, three different sources

The per-family table above, a backend's configured VRAM cap, and a per-request memory estimate answer three different questions, and PotionUI never blends them into one verdict:

- **A preset's `requires:` badge** (in the picker, sourced from this page's per-family table) is static, author-supplied guidance about the *model family* — it says nothing about the specific request you're about to submit (resolution, frame count, which optional components — LoRAs, a second text encoder — you've added).
- **A backend's configured VRAM budget** (its `gpu_max_vram` setting, Admin → Backends) is the ceiling the native engine plans model placement against. It's physical device evidence bounded by an admin-set cap, not a prediction for any one request.
- **A request's memory estimate** (`POST /api/generations/memory-preview`, same payload as `/start` plus an optional `backend_id`) is a non-blocking, **checkpoint-based estimate** for the specific form you're about to submit — never called a lower bound or "at least" anywhere in the response, since runtime use can land on either side of it: it sums the on-disk size of every model reference on an active loader (checkpoint, LoRA, VAE, text encoder alike — never a component a preset pins in its own config outside a form picker, and never a Video/Music Director document's raw wire shape — the same normalized document a real generation would build from, so the active checkpoint set matches), applies a small weight-load margin, and adds a resolution/frame-count-scaled activation term. It never allocates GPU memory, never loads a model, and never enqueues anything — it only inspects the form and the model index.

The estimate is deliberately never a fit guarantee. Its response carries the evidence behind the number rather than a verdict:

- `coverage.known` / `coverage.unknown` — which referenced models had an indexed on-disk size and which didn't; an unresolvable size is skipped, so the estimate only gets more precise, never less, as more models get indexed. When the request itself couldn't be resolved (an in-progress or invalid Video/Music Director document, or any other pipeline-build failure), `coverage.active_set_resolved` is `false` and the estimate reports nothing counted — displayed as "the request could not be resolved," never "no model references."
- `coverage.uncertainty` — plain-language caveats: the activation term is a heuristic, not a measurement; quantization/dtype settings on an active loader may lower real residency below the summed weight sizes; a component a preset pins outside a form picker is never counted.
- `device` — what the routed backend's hardware actually reports, read from the backend's own declared `execution_device` (never guessed from its driver name): a local native backend's free/total VRAM from this host's own GPU monitor (`local`), `null` numbers for a remote worker whose hardware isn't this process's to read (`remote`), `none` when this host genuinely has no GPU, or `unknown` when the backend hasn't declared where it executes at all (e.g. a `comfyui` backend, whose server has its own hardware this host doesn't see).
- `budget` — the configured loading budget for the backend as a whole (its `gpu_max_vram`, bounded by the device's reported free VRAM when there is one) — configuration, not a promise about this request. Reported separately in `budget.pipe_hints_gb`: each active pipe's own per-stage `vram_limit_gb` hint (e.g. a tiled detailer's own tile budget), composed against that same backend cap individually — a preset can declare several with different values, so none of them is presented as if it were the whole request's budget.

Reading the estimate against the device/budget evidence is a judgment call for whoever is looking at it — PotionUI reports the numbers, not a pass/fail.

## See also

- [Models](models.md) — the installed-models inventory and how models connect to presets.
- [Administration](admin.md) — backends and where generations actually run.
- Per-family reference docs under `docs/models/` (in the repository) for full architecture and sampling details — this page only covers the memory/disk numbers.
