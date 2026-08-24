---
type: technique
title: Trajectory Warm-Start (Iterate Mode)
category_group: Sampling
status: needs-gpu-validation
families: [flux, krea2, qwen_image, z_image, anima]
authors: []
paper: null
reference_impl: null
knobs:
  - key: iterate_mode
    surface: preset
    default: false
    effect: "Resume a follow-up generation from a cached mid-trajectory latent instead of pure noise"
related: [spectral-progressive]
---

# Trajectory Warm-Start (Iterate Mode)

In a normal working session, successive generations are usually edits of each other — the same seed
with a tweaked prompt, a nudged CFG value — not unrelated requests. Early denoise steps mostly fix
the global layout (driven by seed and coarse conditioning); later steps refine detail. Iterate mode
takes advantage of this by caching latent checkpoints partway through a trajectory. When a follow-up
generation's conditioning is close enough to a cached run, PotionUI resumes denoising from the
deepest still-valid checkpoint instead of starting over from pure noise, skipping the steps that
would have reproduced work already done.

Checkpoints are captured at 25%, 50%, and 75% of the schedule. How deep a follow-up request resumes
depends on how similar its conditioning is to the cached run's, measured by cosine similarity of the
pooled conditioning: very close (cosine >= 0.995, e.g. a small CFG or detail tweak) resumes at 75%
depth; moderately close (>= 0.98) resumes at 50%; somewhat close (>= 0.95) resumes at 25%; anything
less similar falls back to a normal cold start. When conditioning is genuinely unchanged, a resumed
run reproduces the same output as the equivalent cold run bit-for-bit — resuming isn't an
approximation of the cold run's tail, it *is* the cold run's tail.

## When to use it

Turn this on for iterative, exploratory workflows — nudging a prompt, adjusting CFG, trying small
variations on the same seed — where you want each follow-up generation to feel faster without
changing the final image when nothing meaningfully changed. It has no benefit for one-off,
unrelated generations, since there's nothing to resume from on the first request in a chain.

## How to enable it

Set `iterate_mode: true` in the preset's generation config:

```yaml
iterate_mode: true
```

This only takes effect when all of the following hold; otherwise PotionUI silently falls back to a
normal cold run:

- Sampler is `euler` (other samplers carry step history or randomness a single cached checkpoint
  can't reproduce).
- The request is text-to-image, not img2img (img2img already starts from an image latent, not
  noise).
- APG momentum is off (`apg_momentum == 0`) — APG's running average would make a resumed tail differ
  from the cold run's.
- Model identity, seed, resolution, sampler, step count, schedule, and guidance mode all match the
  cached run exactly; a reloaded checkpoint or a changed setting outside that set forces a cold
  start.

It is not available on video pipes (Wan, LTX) — only on the shared image-family generator base.

## Tradeoffs and limitations

- The resume-depth similarity thresholds (0.995 / 0.98 / 0.95) are first-pass defaults, not yet
  calibrated against a perceptual image-difference metric across a range of real edit sizes — treat
  the behavior as reasonable but unverified until validated on real hardware.
- `cfg_scale` is deliberately excluded from the trajectory's cache key, so a CFG-only nudge can
  resume deep even though it does change the output somewhat — this is an intentional tradeoff
  favoring speed, not an oversight.
- Only applies to `euler` sampling and text-to-image; any other sampler, img2img, or a changed model
  identity/seed/resolution/schedule falls back to a cold start with no error.
- Mutually exclusive with spectral progressive diffusion on the same request.
