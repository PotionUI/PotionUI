---
type: technique
title: RIFLEx
category_group: Quality
status: needs-gpu-validation
families: [wan]
authors: []
paper: {arxiv: "2502.15894", title: "RIFLEx: A Free Lunch for Length Extrapolation in Video Diffusion Transformers"}
reference_impl: null
knobs:
  - key: riflex
    surface: preset
    default: false
    effect: "Enables RoPE frequency clamping so longer-than-trained videos don't visibly loop."
  - key: riflex_trained_frames
    surface: preset
    default: null
    effect: "Overrides the model's known trained latent-frame count; only used when riflex is true."
related: [nag]
---

# RIFLEx

Video diffusion models are trained on clips of a fixed length, and that length gets baked into the positional encoding (rotary position embeddings, or RoPE) the model uses to track "where in time" each frame sits. Ask the model to generate a clip meaningfully longer than what it was trained on, and the positional encoding starts repeating itself — which shows up as the generated video visibly looping or repeating motion partway through, rather than producing genuinely new content for the extra length.

RIFLEx fixes this without retraining the model. It identifies the one specific frequency component in the temporal rotary embedding that's responsible for the periodic repetition (the "intrinsic frequency"), and clamps just that component so its period no longer lines up with the generation length. Every other frequency component is left untouched, so short generations that already fit within the trained length are unaffected — the fix only changes behavior once frame counts are stretched past the model's original training length.

The trained-length figure this clamp is computed relative to defaults to Wan's known trained latent-frame count, but can be overridden per preset via `riflex_trained_frames` if a different checkpoint variant was trained on a different clip length.

## When to use it

- Enable `riflex` whenever a Wan preset is asked to generate video substantially longer than the model's native trained length and the output shows visible looping or repeated motion partway through the clip.
- Leave it off for generations at or near the trained length — there's nothing to fix there, and the clamp is a targeted correction rather than a general quality improvement.
- Only set `riflex_trained_frames` if generating from a Wan checkpoint variant whose trained clip length differs from the family default; otherwise leave it unset and let it inherit the family's known value.

## How to enable it

```yaml
- name: "generator/txt2vid_wan22"
  configuration:
    steps: 30
    frames: 121
    riflex: true
    riflex_trained_frames: 21
```

## Tradeoffs and limitations

- Wan-only: the RoPE mechanism this technique patches doesn't exist in the same form in any other native family (LTX included), so it has no effect there even though the config keys are accepted.
- Adds no measurable compute cost — it's a one-time frequency clamp applied during RoPE setup, not a per-step operation.
- It corrects looping caused specifically by exceeding the trained temporal length; it does not address other forms of long-video degradation (drift, quality falloff) that can also appear on extended generations.
- The frequency-clamp math is unit-tested, but output quality on real extended-length generations hasn't been benchmarked on real hardware — validate on a few real generations before relying on it for production output.
