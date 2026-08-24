---
title: Techniques
category: Presets / Models
category_order: 70
order: 65
---

# Techniques

Reference pages for each optional generation technique PotionUI ships, one page per technique: what
it does, which model families it applies to, its knobs, and its `status` (`stable`,
`experimental`, or `needs-gpu-validation`). For the grouped map of how these trade off against each
other — and how to validate one on your own hardware — see
[How PotionUI's engine optimizations fit together](../native-optimizations.md). This page is a flat,
complete index of every file in this directory.

## Performance

- [Attention Backends](attention-backends.md) — the shared attention dispatcher (sdpa, sage, sage2, sage3, flash, sparge).
- [First-Block Cache (FBCache)](first-block-cache.md) — skip most of a diffusion step when the model's output barely changed.
- [Native fp8 Matmul](fp8-matmul.md) — run the GEMM itself in fp8 instead of dequantizing first.
- [Prompt Embedding Cache](prompt-embed-cache.md) — skip re-encoding a prompt you've already run.
- [SLA (Sparse–Linear Attention)](sla-attn.md) — block-sparse attention for MiniMax-H3, vendored from thu-ml's SLA.
- [Sol-Attn](sol-attn.md) — block-sparse attention for MiniMax-H3 that scores KV blocks by pooled summary vectors.
- [Spectral Progressive Diffusion](spectral-progressive.md) — run early denoising steps at reduced resolution.
- [Speed Profiles](speed-profiles.md) — a preset-authoring convention for draft/standard/max toggles.
- [Streaming Prefetch Overlap](stream-prefetch.md) — overlap weight transfer with compute on low-VRAM setups.
- [Regional `torch.compile`](torch-compile.md) — regional graph compilation for resident models.

## Memory

- [Temporal-Chunked VAE Decode](chunked-vae-decode.md) — decode long video clips in temporal chunks instead of all at once.
- [Preset-Scoped Model RAM Cache](preset-scoped-ram-cache.md) — how models are kept in or evicted from RAM across preset switches.

## Quality

- [ADM Guidance (Fooocus technique)](adm-guidance-sdxl.md) — Fooocus-style texture enhancement for SDXL.
- [Adaptive Projected Guidance (APG)](apg.md) — reduce oversaturation at high guidance scales.
- [CFG-Zero*](cfg-zero-star.md) — a free correction to classifier-free guidance's early-step behavior.
- [Detail Daemon Schedule Warp](detail-daemon-schedule.md) — bias the noise schedule toward more or less fine detail.
- [FreeInit](freeinit.md) — reduce video flicker by iteratively refining the initial noise.
- [Normalized Attention Guidance (NAG)](nag.md) — negative-prompt steering inside attention, without a second forward pass.
- [Numerics Watchdog (NaN/Inf Guard)](nan-watchdog.md) — catch numerical corruption during sampling before it produces a black image.
- [RIFLEx](riflex.md) — extend video length past a model's trained clip length without looping.
- [Self-Attention Guidance (SAG) for SDXL](sag-sdxl.md) — attention-map-driven guidance for SDXL.
- [Anisotropic Sharpness Filter (SDXL)](sharpness-sdxl.md) — anisotropic edge enhancement for SDXL.
- [Skip-Layer Guidance (SLG)](slg.md) — guide against a degraded, layer-skipped prediction.
- [SVI Pro 2.0 chain continuity](svi-pro-2-0.md) — tune how strongly one Wan 2.2 chained-video segment carries into the next.

## Sampling

- [Samplers and Sigma Schedules](samplers-and-schedules.md) — every sampler algorithm and sigma schedule mode, with guidance on when to use each.
- [Trajectory Warm-Start (Iterate Mode)](trajectory-warm-start.md) — resume a generation from a cached mid-trajectory latent instead of starting from noise.

See [Models](../models/README.md) for per-family reference pages, each listing which techniques above apply to it.
