---
category: Presets / Models
category_order: 70
order: 50
---

# How PotionUI's engine optimizations fit together

PotionUI's native engine ships a growing set of optional techniques that trade off speed, memory, and output quality against each other. Each one gets its own reference page under [Techniques](techniques/) — what it does, when to use it, and exactly how to turn it on. This page is the map: what exists, grouped by what it trades off, and how to check whether a given technique is safe to rely on for your hardware.

Every technique is opt-in unless its own page says otherwise. Turning nothing on gets you the same behavior PotionUI has always had; each technique changes something only once you set its knob.

## Performance

Techniques that make generation faster without changing what comes out (or with a clearly-stated approximation).

- [Attention backends](techniques/attention-backends.md) — the shared attention dispatcher (sdpa, sage, sage2, sage3, flash, sparge).
- [First-block cache](techniques/first-block-cache.md) — skip most of a diffusion step when the model's output barely changed.
- [Spectral progressive diffusion](techniques/spectral-progressive.md) — run early denoising steps at reduced resolution.
- [`torch.compile`](techniques/torch-compile.md) — regional graph compilation for resident models.
- [Native fp8 matmul](techniques/fp8-matmul.md) — run the GEMM itself in fp8 instead of dequantizing first.
- [Streaming prefetch](techniques/stream-prefetch.md) — overlap weight transfer with compute on low-VRAM setups.
- [Prompt embedding cache](techniques/prompt-embed-cache.md) — skip re-encoding a prompt you've already run.
- [Speed profiles](techniques/speed-profiles.md) — a preset-authoring convention for draft/standard/max toggles.

## Memory

Techniques that reduce VRAM or RAM pressure.

- [Chunked VAE decode](techniques/chunked-vae-decode.md) — decode long video clips in temporal chunks instead of all at once.
- [Preset-scoped RAM cache](techniques/preset-scoped-ram-cache.md) — how models are kept in or evicted from RAM across preset switches.

## Quality

Techniques that change what comes out of a generation.

- [CFG-Zero*](techniques/cfg-zero-star.md) — a free correction to classifier-free guidance's early-step behavior.
- [Adaptive Projected Guidance (APG)](techniques/apg.md) — reduce oversaturation at high guidance scales.
- [Skip-Layer Guidance (SLG)](techniques/slg.md) — guide against a degraded, layer-skipped prediction.
- [Normalized Attention Guidance (NAG)](techniques/nag.md) — negative-prompt steering inside attention, without a second forward pass.
- [RIFLEx](techniques/riflex.md) — extend video length past a model's trained clip length without looping.
- [FreeInit](techniques/freeinit.md) — reduce video flicker by iteratively refining the initial noise.
- [Detail daemon schedule](techniques/detail-daemon-schedule.md) — bias the noise schedule toward more or less fine detail.
- [NaN watchdog](techniques/nan-watchdog.md) — catch numerical corruption during sampling before it produces a black image.
- [ADM guidance (SDXL)](techniques/adm-guidance-sdxl.md) — Fooocus-style texture enhancement for SDXL.
- [Self-Attention Guidance (SDXL)](techniques/sag-sdxl.md) — attention-map-driven guidance for SDXL.
- [Sharpness filter (SDXL)](techniques/sharpness-sdxl.md) — anisotropic edge enhancement for SDXL.

## Sampling

Techniques that change how the denoising trajectory itself is walked.

- [Samplers and schedules](techniques/samplers-and-schedules.md) — every sampler algorithm and sigma schedule mode, with guidance on when to use each.
- [Trajectory warm-start (Iterate mode)](techniques/trajectory-warm-start.md) — resume a generation from a cached mid-trajectory latent instead of starting from noise.

## Validating on your hardware

Some techniques above are marked `status: needs-gpu-validation` on their own page — implemented and unit-tested, but not yet benchmarked or correctness-checked against real output on real hardware. Treat that status as "verify before you rely on it," not "don't use it." A quick way to validate any of them:

1. **Baseline first.** Generate the same seed and prompt with the technique off. Keep the image or video.
2. **Turn on one technique at a time.** Enable it per its own page's "How to enable it" section, generate the same seed/prompt again, and compare.
3. **Check output, not just speed.** For an approximate technique (attention backends other than sdpa/sage2/sage3, native fp8 matmul, FreeInit's frequency blend), look at the actual pixels — a faster generation that looks visibly different isn't a win unless you're fine with the tradeoff. For a purely mechanical technique (chunked VAE decode, streaming prefetch, `torch.compile`), output should be identical or near-identical; a visible difference means something is wrong, not just approximate.
4. **Check wall-clock separately.** Some techniques only pay off under specific conditions — streaming prefetch only matters when a model doesn't fully fit in VRAM, `torch.compile` pays a warm-up cost on the first generation after it's enabled, first-block cache's benefit scales with step count.
5. **Use the built-in benchmark where one exists.** Admin → Backends → Optimizations has a benchmark action that times every installed attention backend on identical tensors and reports relative speed — run that before doing a manual A/B, then confirm the winner with a real generation.

If a technique's page states a specific caveat (an approximate attention kernel's `topk` parameter, a similarity-ladder threshold pending calibration, a cost multiplier for extra sampling passes), that caveat is the first thing to check when validating it — it's usually the actual tradeoff you're accepting by turning the technique on.

## See also

- [Native Engine v2](native-engine.md) — the base loading/detection/ops/VAE/sampling/attention/memory-tiering stack every technique above layers on top of.
- [Preset Authoring Guide](presets.md) — `speed_profiles:` and general preset config surface for the knobs described above.
- [Backends and Engines](backends.md) — `native` is one of the two registered engines; unrelated to the attention *backends* (sage/flash/sdpa/sparge) discussed above, which are an in-process dispatcher inside the `native` engine, not a `backend` row in the admin sense.
- [Models](models/README.md) — per-family reference pages, each listing which of the techniques above apply to it.
