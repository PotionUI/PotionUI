---
type: technique
title: ADM Guidance (Fooocus technique)
category_group: Quality
status: stable
families: [sdxl]
authors: []
paper: null
reference_impl: {name: "Fooocus", url: "https://github.com/lllyasviel/Fooocus", license: "GPLv3"}
knobs:
  - key: adm_guidance_enabled
    surface: preset
    default: true
    effect: "Turns the ADM guidance hook on or off"
  - key: adm_positive_scale
    surface: preset
    default: 1.5
    effect: "Scales up the resolution values fed to the positive branch during early steps"
  - key: adm_negative_scale
    surface: preset
    default: 0.8
    effect: "Scales down the resolution values fed to the negative branch during early steps"
  - key: adm_scaler_end
    surface: preset
    default: 0.3
    effect: "Fraction of the trajectory (by noise-schedule progress) during which the scaling applies"
related: [sharpness-sdxl, sag-sdxl]
---

# ADM Guidance (Fooocus technique)

ADM guidance is a texture-enhancement technique for SDXL, ported from Fooocus. SDXL's UNet
receives extra conditioning beyond the text prompt — `add_time_ids`, a small vector that carries
the image's original height/width, crop offsets, and target height/width (the "ADM" embedding).
During the early, structure-forming steps of generation, this technique scales just the
height/width components of that vector up on the positive (conditional) branch and down on the
negative (unconditional) branch. The effect reads as slightly stronger, more defined texture and
structure without touching color or overall composition.

Only the resolution values are touched — the pooled CLIP text embeddings (`add_text_embeds`) are
left completely alone. Scaling those instead (a mistake the original implementation avoided)
distorts the text conditioning itself and tends to produce oversaturated, burnt-looking output,
which is especially visible on anime-style checkpoints whose pooled vector carries heavy
quality-tag conditioning.

The scaling only applies while the noise schedule is still early: it's gated on actual
noise-schedule progress (via each timestep's alpha_cumprod), not step index, so it behaves
consistently whether the sampler uses a uniform or a Karras-style non-uniform sigma schedule.

## When to use it

It is on by default in the shipped SDXL preset (`content/presets/marketplace/SDXL/realistic`) and is meant to
be left on for most generations — it's a small, early-step nudge toward more defined texture with
no extra UNet forward passes, so the performance cost is negligible. Turn it off if you want the
checkpoint's completely untouched output for comparison, or if you notice it fighting with a
LoRA/style that already pushes hard on structure.

## How to enable it

In the shipped SDXL preset it's already wired into `modes/txt2img/pipeline.yml` as the
`adm_guidance/sdxl` pipe, driven by form fields on the Advanced tab
(`content/presets/marketplace/SDXL/modes/txt2img/tabs/advanced.yml`). As an end user you
toggle it from the generation form: **Advanced → Adaptive Diffusion Model (ADM) Guidance →
Enable ADM Guidance**, with sliders for **ADM Positive Scale**, **ADM Negative Scale**, and **ADM
End Point** underneath.

For preset authors, the underlying pipe config accepts:

```yaml
- name: "adm_guidance/sdxl"
  id: "adm_guidance"
  enabled: "{{ get_form('custom', ['adm_guidance_enabled'], true) }}"
  input:
    - ["model", "checkpoint_loader/sdxl", "model"]
  configuration:
    positive_scale: "{{ get_form('custom', ['adm_positive_scale'], 1.5) }}"
    negative_scale: "{{ get_form('custom', ['adm_negative_scale'], 0.8) }}"
    scaler_end: "{{ get_form('custom', ['adm_scaler_end'], 0.3) }}"
```

If none of `positive_scale`/`negative_scale`/`scaler_end` are explicitly set away from their class
defaults (`1.5`/`0.8`/`0.3`), the pipe auto-tunes from the loaded checkpoint's detected model type.
For a checkpoint flagged as not recommending ADM (anime-style models, whose pooled text embedding
already carries strong style conditioning), it substitutes neutral scales (`1.0`/`1.0`) and a
`scaler_end` of `0.0`, which makes the hook a no-op for that generation. This auto-tuning only
kicks in when you haven't touched the sliders; moving any slider away from the default disables
auto-tuning and uses your explicit values instead.

## Tradeoffs and limitations

- SDXL-only: this runs on the separate `diffusers`-based SDXL pipeline stack
  (`src/pipelines/pipes/adm_guidance/sdxl/`), not the native engine, so it has no equivalent for
  Flux/Krea-2/Qwen-Image/Wan/LTX/Anima/Z-Image/SeedVR2.
- It is a resolution-conditioning trick, not a general sharpening filter — its visible effect is
  subtle and concentrated in the early steps; it will not rescue a generation with fundamentally
  wrong composition.
- Auto-tuning is silent: if you haven't touched the sliders, the effective scale depends on the
  checkpoint's detected model type, so two different checkpoints under the same preset settings can
  produce different actual ADM behavior.
- `scaler_end` only accepts values in `[0.0, 1.0]`; values outside that range raise an error rather
  than clamping.
