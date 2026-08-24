---
type: technique
title: Adaptive Projected Guidance (APG)
category_group: Quality
status: needs-gpu-validation
families: [qwen_image, anima, z_image, wan, ltx]
authors: []
paper: {arxiv: "2410.02416", title: "Eliminating Oversaturation and Artifacts of High Guidance Scales in Diffusion Models"}
reference_impl: null
knobs:
  - key: guidance_options.apg_eta
    surface: preset
    default: 1.0
    effect: "Weight of the guidance delta's component parallel to the positive prediction; 1.0 = plain CFG, lower values reduce oversaturation."
  - key: guidance_options.apg_norm_threshold
    surface: preset
    default: 0.0
    effect: "Caps the magnitude of the guidance delta to this radius; 0 = off."
  - key: guidance_options.apg_momentum
    surface: preset
    default: 0.0
    effect: "Smooths the guidance correction across steps using a running average; 0 = off."
related: [cfg-zero-star]
---

# Adaptive Projected Guidance (APG)

Turning up classifier-free guidance (CFG) scale makes a generation follow the prompt more closely, but past a certain point it also produces oversaturated colors, blown-out contrast, and burnt-looking artifacts. That happens because plain CFG extrapolates the whole difference between the positive and negative predictions, including the part of that difference that's already "in the same direction" as the positive prediction — pushing what's already emphasized even further. Adaptive Projected Guidance addresses this by splitting the guidance correction into two pieces: the part that points in the same direction as the positive prediction (parallel) and the part that points elsewhere (orthogonal). It down-weights the parallel piece — the piece responsible for oversaturation — while keeping the orthogonal piece, which is where the actual prompt-following signal lives.

Three knobs control this. `apg_eta` sets how much of the parallel component survives; `1.0` reproduces plain CFG exactly (APG effectively off), while values in the `0.0`–`0.5` range are what the paper explores for reducing oversaturation at high guidance scales. `apg_norm_threshold` puts a hard cap on the overall size of the guidance correction each step, regardless of direction — useful as a safety valve against runaway corrections. `apg_momentum` blends each step's correction with a running average of previous steps' corrections, smoothing out step-to-step jitter; the paper explores small negative values (e.g. `-0.5`) for this.

APG only has an effect on families that use true classifier-free guidance — the same set as CFG-Zero* (Qwen-Image, Anima, Z-Image on the image side; Wan and LTX on the video side). It composes with CFG-Zero* rather than replacing it: CFG-Zero*'s uncond rescale happens first, then APG's projection and rescale are applied to the resulting delta.

## When to use it

- Reach for `apg_eta` when raising CFG/guidance scale for stronger prompt adherence but the result looks oversaturated, overly contrasty, or "crunchy" — lowering `apg_eta` toward `0.0`–`0.5` counteracts that without having to lower the guidance scale itself.
- Use `apg_norm_threshold` if a generation is producing occasional extreme artifacts (a single step's correction spiking) rather than a uniform oversaturation.
- Use `apg_momentum` if outputs show flicker or jitter from step to step (most relevant for video) rather than a static color/contrast problem.

## How to enable it

Image family (nested `guidance_options`):

```yaml
- name: "generator/z_image"
  configuration:
    steps: 30
    guidance: 6.0
    guidance_options:
      apg_eta: 0.3
      apg_norm_threshold: 15.0
      apg_momentum: -0.5
```

Video family (flat keys, e.g. Wan or LTX):

```yaml
- name: "generator/txt2vid_ltx"
  configuration:
    steps: 30
    apg_eta: 0.3
    apg_norm_threshold: 0.0
    apg_momentum: -0.3
```

## Tradeoffs and limitations

- APG adds negligible compute — it's a vector-math correction on predictions the model already produces, not an extra forward pass.
- Very low `apg_eta` can under-follow the prompt at very high guidance scales, since the parallel component also carries some legitimate prompt-adherence signal — treat it as a tuning dial, not a strict improvement at every setting.
- Only affects families that use true CFG; setting these keys on Flux, Krea-2, or SeedVR2 presets has no effect.
- `apg_norm_threshold` and `apg_momentum` default to off (`0.0`); they need to be explicitly set to do anything.
- The wiring and math are unit-tested, but output quality hasn't been benchmarked against plain CFG on real hardware — compare a few generations with and without it before relying on it for production output.
