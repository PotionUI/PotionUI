---
type: technique
title: CFG-Zero*
category_group: Quality
status: stable
families: [qwen_image, anima, z_image, wan, ltx]
authors: []
paper: {arxiv: "2503.10554", title: "CFG-Zero*: Improved Classifier-Free Guidance for Flow Matching Models"}
reference_impl: {name: "WeichenFan/CFG-Zero-star", url: "https://github.com/WeichenFan/CFG-Zero-star", license: "Apache-2.0"}
knobs:
  - key: guidance_options.cfg_zero_star
    surface: preset
    default: true
    effect: "Rescales the negative-prompt prediction onto the positive prediction's direction before combining them."
  - key: guidance_options.zero_init_steps
    surface: preset
    default: 0
    effect: "Skips both forward passes for the first N steps and returns zero velocity instead."
related: [apg]
---

# CFG-Zero*

CFG-Zero* is a small correction applied to classifier-free guidance (CFG) — the mechanism that steers a generation away from a negative prompt and toward a positive one. Ordinary CFG can overshoot at the start of a generation, when the model's positive and negative predictions haven't diverged yet, and this overshoot compounds into oversaturated colors and washed-out contrast in the finished image or video. CFG-Zero* fixes this in two independent parts, controlled by two separate knobs.

The first part, `cfg_zero_star`, rescales the negative prediction so its magnitude best matches the positive prediction's direction before the two are combined, instead of combining them at face value. This is a per-image, per-step adjustment computed from the two predictions the model already produces — it adds no extra forward pass and no extra cost. The second part, `zero_init_steps`, is a separate and more aggressive trick: for the first N denoising steps it skips running the model at all and simply returns a zero velocity, effectively delaying the start of guided denoising by N steps. This saves compute on those steps, but it means the earliest steps of the trajectory receive no signal from the model, which can matter for very short schedules.

Both corrections only take effect on families that use true classifier-free guidance (a real positive/negative forward pair) rather than distilled "embedded" guidance or no guidance at all. Setting these knobs on a family that doesn't use CFG (Flux, Krea-2) has no effect, since the guidance strategy that reads them is never instantiated for those families.

## When to use it

- `cfg_zero_star` is safe to leave on for any true-CFG family — it is the default, costs nothing, and generally reduces oversaturation and contrast artifacts, particularly at higher CFG scales. There's rarely a reason to turn it off.
- `zero_init_steps` trades a small amount of denoising fidelity in the first few steps for faster generation. It's most useful on longer step counts (e.g. 30+) where losing 1-2 early steps of signal is negligible relative to the total; avoid it on short schedules (under ~10 steps) where every step's signal matters.

## How to enable it

Both knobs live in a preset's `guidance_options` dict, set on the generator pipe's configuration for image families (Qwen-Image, Anima, Z-Image), or as flat keys on the generator pipe's configuration for the Wan/LTX video pipes.

Image family (nested `guidance_options`):

```yaml
- name: "generator/qwen"
  configuration:
    steps: 50
    guidance: 4.0
    guidance_options:
      cfg_zero_star: true
      zero_init_steps: 0
```

Video family (flat keys, e.g. Wan or LTX):

```yaml
- name: "generator/txt2vid_wan22"
  configuration:
    steps: 30
    cfg_zero_star: true
    zero_init_steps: 2
```

## Tradeoffs and limitations

- `cfg_zero_star` is effectively free — no extra forward pass, no measurable slowdown.
- `zero_init_steps` reduces wall-clock time roughly in proportion to the fraction of steps skipped, but those steps produce no model signal at all; too high a value on a short schedule can visibly degrade output.
- Neither knob does anything on families that don't use true CFG (Flux, Krea-2, SeedVR2) — setting them there is a silent no-op, not an error.
- No published ablation exists for `zero_init_steps` beyond the "kijai zero-init" community trick referenced by the reference implementation; treat it as an experimental speed dial rather than a validated quality technique.
