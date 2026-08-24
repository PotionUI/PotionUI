---
type: technique
title: Self-Attention Guidance (SAG) for SDXL
category_group: Quality
status: experimental
families: [sdxl]
authors: []
paper: {arxiv: "2210.00939", title: "Improving Sample Quality of Diffusion Models Using Self-Attention Guidance"}
reference_impl: {name: "ComfyUI SelfAttentionGuidance node", url: "https://github.com/comfyanonymous/ComfyUI", license: "GPLv3"}
knobs:
  - key: sag_enabled / sag pipe
    surface: preset
    default: "implemented, not wired into the shipped preset's pipeline"
    effect: "Adds an extra self-attention-guided correction pass to sharpen detail"
related: [adm-guidance-sdxl, sharpness-sdxl]
---

# Self-Attention Guidance (SAG) for SDXL

Self-Attention Guidance is a detail-enhancement technique that uses the model's own self-attention
maps to figure out which regions of the image it's currently focusing on, then pushes the
generation away from a deliberately degraded version of those regions. Concretely, during a normal
denoising step it records the self-attention probabilities from one specific layer (the mid-block's
`attn1`) for the unconditional branch, turns that into a blur mask (regions the model is attending
to strongly get blurred, everything else is untouched), re-noises the blurred prediction to the
current noise level, and runs one extra UNet forward on it. The final result is nudged away from
that degraded prediction, in the spirit of classifier-free guidance but driven by attention maps
instead of a text/no-text contrast — similar in spirit to attention-based guidance techniques like
SLG, but SLG works by skipping transformer layers rather than blurring attention-selected image
regions.

This SDXL implementation is a direct, ComfyUI-parity port (`comfy_extras/nodes_sag.py`): it records
attention on the mid-block only, builds a thresholded blur mask, blurs and re-noises in x0-space,
and mixes the correction in after CFG is applied.

## When to use it

Not applicable today — see status below. If it becomes reachable in the future, it targets the same
use case as sharpness: pulling out slightly more fine detail at the cost of extra compute (roughly
15-20% more time per generation, since it adds one extra UNet forward per step).

## How to enable it

**The pipe is fully implemented (`src/pipelines/pipes/sag/sdxl/`) and its form fields already exist** on the
SDXL preset's Advanced tab (`sag_enabled`, `sag_scale`, `sag_sigma`, `sag_threshold` in
`content/presets/marketplace/SDXL/modes/txt2img/tabs/advanced.yml`), but
**`modes/txt2img/pipeline.yml` never instantiates a `sag/sdxl` pipe step**. Toggling "Enable SAG" in
the current UI does not currently affect generation — the form value has nowhere to go.

To wire it in, a preset author would add a pipe entry parallel to the existing ADM guidance one,
after the checkpoint loader and before the generator:

```yaml
- name: "sag/sdxl"
  id: "sag"
  enabled: "{{ get_form('custom', ['sag_enabled'], false) }}"
  input:
    - ["model", "checkpoint_loader/sdxl", "model"]
  configuration:
    scale: "{{ get_form('custom', ['sag_scale'], 0.75) }}"
    sigma: "{{ get_form('custom', ['sag_sigma'], 2.0) }}"
    sag_threshold: "{{ get_form('custom', ['sag_threshold'], 1.0) }}"
```

Note the form field default for `sag_threshold` in the shipped form (`0.5`) does not match the
pipe's own default (`1.0`) — a preset author wiring this in should either update the form default
or pass it through explicitly, since the two currently disagree.

## Tradeoffs and limitations

- **Not wired into any shipped preset today.** The pipe and hook are implemented and match the
  ComfyUI reference, but nothing in `presets/` instantiates the `sag/sdxl` pipe, so it has no effect
  in the shipped app regardless of what the (already-present) form checkbox shows.
- SDXL-only, same as ADM guidance and sharpness — this runs on the separate `diffusers`-based SDXL
  stack, not the native engine.
- Roughly doubles the compute of the affected steps: it always adds one extra UNet forward per step
  when enabled (uncond-only, so cheaper than a full extra CFG pair, but still a real cost).
- Requires CFG to be active (`do_cfg`) and a minimum latent size (`min(H, W) > 4`) to run; it
  silently no-ops below that, matching the ComfyUI reference's own guard.
