---
type: technique
title: Anisotropic Sharpness Filter (SDXL)
category_group: Quality
status: stable
families: [sdxl]
authors: []
paper: null
reference_impl: {name: "Fooocus", url: "https://github.com/lllyasviel/Fooocus", license: "GPLv3"}
knobs:
  - key: sharpness
    surface: preset
    default: 0.0
    effect: "Strength of the anisotropic edge-enhancement filter applied during denoising (0 = disabled/no-op)"
related: [adm-guidance-sdxl, sag-sdxl]
---

# Anisotropic Sharpness Filter (SDXL)

This is an edge-enhancement filter, ported from Fooocus, that runs during denoising rather than as
a post-processing step on the finished image. At each step it converts the model's noise prediction
into predicted-clean-image ("x0") space, applies an edge-preserving anisotropic filter to it, then
blends the filtered result back in and converts back to noise-prediction space before continuing
the denoising loop. Because it works in x0-space with an edge-aware filter (not a naive unsharp
mask), it sharpens edges while leaving smooth regions alone, avoiding the halos and noise
amplification a traditional post-processing sharpen tends to introduce.

The blend strength ramps up as generation progresses — it barely touches the earliest steps (where
the image's coarse layout is still forming) and applies its full effect closer to the end, when
fine detail is what's actually being resolved. It only modifies the conditional (text) half of the
noise prediction, not the unconditional half.

## When to use it

Use it when you want crisper micro-detail (skin texture, fabric weave, hair strands) without a
separate upscale/detail pass. It's cheap — no extra UNet forward — so there's little reason not to
try a low strength on any generation. Because the default is `0` (fully disabled), it never affects
output unless you explicitly turn it up.

## How to enable it

It's wired into the shipped SDXL preset as the `sharpness/sdxl` pipe in
`content/presets/marketplace/SDXL/realistic/modes/txt2img/pipeline.yml`, gated on the `sharpness` form value
being greater than `0`:

```yaml
- name: "sharpness/sdxl"
  id: "sharpness"
  enabled: "{% if get_form('custom', ['sharpness'], 0)|float > 0 %}true{% else %}false{% endif %}"
  input:
    - ["model", "checkpoint_loader/sdxl", "model"]
  configuration:
    strength: "{{ get_form('custom', ['sharpness'], 0) }}"
```

As an end user, this is the **Sharpness** slider on the generation form (Post-Processing / Advanced
tab of the shipped SDXL preset). It defaults to `0` (off) and accepts values up to `30`; typical
useful values are small (a handful of units) — the filter's internal blend multiplier is scaled
down heavily (`base_multiplier = 0.001`) so the raw strength number doesn't map 1:1 to visual
intensity.

## Tradeoffs and limitations

- SDXL-only: it runs on the separate `diffusers`-based SDXL pipeline stack, not the native engine,
  so there's no equivalent knob for Flux/Krea-2/Qwen-Image/Wan/LTX/Anima/Z-Image/SeedVR2.
- Pushed too high, it can still introduce artifacts — the filter is edge-aware but not immune to
  over-sharpening at large strengths, since strength has an effectively open-ended range (`0` to
  `30`) with no built-in perceptual cap.
- Only affects the conditional half of CFG and requires CFG to be active; it has no effect in a
  CFG-disabled generation.
- Effect is concentrated late in the trajectory by design — if you're using a very low step count,
  there may not be enough "late" steps left for it to visibly act on.
