---
type: technique
title: First-Block Cache (FBCache)
category_group: Performance
status: stable
families: [flux, krea2, qwen_image, z_image, anima, wan, ltx, minimax_h3]
authors: []
paper: {arxiv: "2411.19108", title: "TeaCache: Timestep Embedding Aware Cache for Accelerating Diffusion Transformers"}
reference_impl: {name: "chengzeyi/ParaAttention", url: "https://github.com/chengzeyi/ParaAttention", license: "Custom (non-OSS; see the repo's LICENSE.md — PotionUI's implementation is re-derived from the public technique description, not copied from this repo)"}
knobs:
  - key: step_cache
    surface: preset
    default: "absent (disabled)"
    effect: "Skips most of a diffusion step when the model's first block barely changed"
related: [torch-compile]
---

# First-Block Cache (FBCache)

Across most of a diffusion trajectory, a diffusion transformer's output changes only a little from
one denoising step to the next. First-Block Cache exploits this: PotionUI checks the output of the
model's very first transformer block as a cheap proxy for how much the whole network's output would
change on this step. If that first-block output barely moved compared to the last step that was
actually computed, PotionUI skips every remaining block and the final projection, and reuses the
previous step's output directly.

Unlike some step-caching schemes that require a per-model calibration pass, this cache is
threshold-based and works out of the box: you set how much drift is tolerable and PotionUI decides
step by step whether to compute or reuse. Skipped steps cost essentially nothing, so the technique
trades a small amount of visual fidelity for a meaningful reduction in wall-clock time on
multi-step generations.

## When to use it

Use it on generations with a reasonable step count (10+) where a small amount of extra artifact
risk is acceptable in exchange for speed — batch previews, iteration during prompt exploration, or
any workflow where you'll inspect and possibly regenerate. Avoid it for final, one-shot renders
where you want the sampler's untouched output, or for very low step counts where there isn't much
trajectory left to skip through.

## How to enable it

Set `step_cache` in the preset's pipeline/generation config. It is a nested dict; the cache is
disabled unless `rel_threshold` is a positive number:

```yaml
step_cache:
  rel_threshold: 0.12       # 0 = disabled (default). ~0.08 conservative, ~0.15 aggressive
  warmup_steps: 4            # force real compute for the first N steps
  max_consecutive_skips: 3   # re-anchor with a real compute after this many skips in a row
```

`rel_threshold` around `0.08` is conservative (roughly 1.3x fewer effective forwards); around
`0.15` is aggressive (roughly 1.8-2.2x). `warmup_steps` keeps the first few steps — where the
image's coarse layout is still forming — fully computed, since caching them tends to be the most
visible mistake. `max_consecutive_skips` bounds how long the cache can coast on one anchor before
being forced to re-probe.

## Tradeoffs and limitations

- Approximate: skipped steps reuse a previous output rather than computing a new one, so aggressive
  thresholds can introduce visible artifacts, especially early in the trajectory or on fast-moving
  content.
- Per-family visual impact has not been benchmarked individually on every supported architecture —
  treat the threshold guidance above as a starting point and check output quality per model/preset
  before relying on aggressive settings for production output.
- Not supported by every native family: it is wired into Flux, Krea-2, Qwen-Image, Z-Image, Anima,
  Wan, LTX, and MiniMax-H3. SeedVR2 does not read the `step_cache` kwarg, and SDXL runs on a separate
  (non-native) diffusers-based stack that this cache does not touch at all. On LTX, it is also
  incompatible with the Quality speed profile's `MultiModalGuider` guidance recipe (see
  `docs/models/ltx.md`) — the two are not integrated together.
- Cache state is per generation and per guidance branch (conditional/unconditional get independent
  caches) — it does not persist or share state across separate generation requests.
