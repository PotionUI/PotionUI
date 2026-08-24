---
title: Hardware Requirements
order: 72
---

# Hardware Requirements

PotionUI runs on a single CUDA-capable NVIDIA GPU. What you can actually generate — and how well — depends on that GPU's VRAM and, just as much, on the host's system RAM. This page gives honest numbers instead of "a GPU with enough VRAM."

## The floor

**8 GB VRAM + 16 GB system RAM** is the supported minimum. On that floor, **SDXL** is the model family PotionUI guarantees works — it runs on `diffusers` pipelines with its own VRAM-tier memory policy (aggressive sequential offload + max attention slicing below 8 GB, model offload 8–12 GB, no offload 12–16 GB, fully GPU-resident at 16 GB+ — `src/platform/runtime/model_lifecycle/memory_policy.py`). Every other native-engine family (Flux, Krea-2, Qwen-Image, Wan, LTX, Z-Image, Anima, MiniMax-H3) needs more, some considerably more — see the per-family table below.

**Recommended: 16–24 GB VRAM + 32 GB system RAM.** This is what the native engine's low-VRAM design targets as "first class" (`docs/native-engine.md`), and it's comfortably above the RAM-cache floor (below) for realistic multi-model sessions.

The native engine (`src/platform/runtime/native/`) is explicitly designed for small GPUs, not just the 32 GB card it was developed on: `memory/tiering.py` places each component (DiT / text encoder / VAE) by its *actual* measured size, and a component that doesn't fit fully resident streams the overflow from pinned host RAM (partial residency) or gets quantized to fp8 on load instead of failing outright. That's what makes several families runnable — slowly — below their "comfortable" VRAM tier.

## System RAM is not optional headroom

PotionUI reserves system RAM the same way it reserves VRAM, and this bites on small boxes:

- **The model RAM cache keeps a floor of `max(8 GB, 10% of total RAM)` free at all times** (`_MIN_FREE_RAM_GB` / `_MIN_FREE_RAM_FRACTION`, `src/platform/runtime/model_lifecycle/manager.py:53-54`, enforced in `_make_room_for_ram`). On a 16 GB RAM machine that floor is 8 GB — half the box — so the cache that normally keeps a checkpoint warm between generations gets evicted aggressively; effectively, don't count on cross-generation caching with 16 GB of system RAM.
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

## See also

- [Models](models.md) — the installed-models inventory and how models connect to presets.
- [Administration](admin.md) — backends and where generations actually run.
- Per-family reference docs under `docs/models/` (in the repository) for full architecture and sampling details — this page only covers the memory/disk numbers.
