---
type: technique
title: Skip-Layer Guidance (SLG)
category_group: Quality
status: needs-gpu-validation
families: [wan]
authors: []
paper: null
reference_impl: null
knobs:
  - key: slg_scale
    surface: preset
    default: 0.0
    effect: "Push-away strength from a degraded (blocks-skipped) prediction; 0 = off."
  - key: slg_layers
    surface: preset
    default: null
    effect: "Comma-separated transformer block indices to skip in the degraded pass; empty = off even if slg_scale > 0."
  - key: slg_sigma_start
    surface: preset
    default: 1.0
    effect: "Upper (earlier-trajectory) sigma bound of the window SLG is active in."
  - key: slg_sigma_end
    surface: preset
    default: 0.0
    effect: "Lower (later-trajectory) sigma bound of the window SLG is active in."
related: [cfg-zero-star, apg]
---

# Skip-Layer Guidance (SLG)

Skip-Layer Guidance adds a second kind of guidance on top of ordinary classifier-free guidance. Where CFG pushes the output away from a negative-prompt prediction, SLG pushes it away from a "degraded" prediction: the same model, same prompt, but with a chosen set of transformer blocks bypassed (passed through unchanged instead of processed). Because those skipped blocks would normally refine details, structure, and coherence, the degraded prediction is a rougher, less-detailed version of the same generation — and pushing away from it sharpens detail and structure in the final result, similar in spirit to CFG but contrasting against a worse version of the same model instead of an unconditioned one.

This costs one extra forward pass per step where it's active — the model has to run twice: once normally, once with the chosen blocks skipped. To keep this bounded, SLG only runs inside a sigma window (`slg_sigma_start` down to `slg_sigma_end`), so it can be limited to, say, the middle portion of the trajectory rather than the whole thing.

This technique is re-derived from a public description of the SD3.5 / ComfyUI `SkipLayerGuidanceDiT` concept — it is not backed by a formal published paper, and it is not ported from an existing GPL-licensed implementation; the formula (`final = out + slg_scale * (out - degraded)`) is reimplemented from that public concept description.

SLG only has an effect on Wan, because Wan's architecture is the only one in the native engine whose transformer forward pass actually honors a `skip_layers` argument. The config keys are wired generically, but setting them on any other family (including LTX, which shares much of the same video-pipe plumbing) does nothing, since there's no matching skip-layers implementation for the model to route the request to.

## When to use it

- Use SLG when Wan output looks structurally soft or under-detailed even at reasonable CFG scales — it targets structural sharpness specifically, not color/saturation (that's APG's territory).
- Narrow the sigma window (`slg_sigma_start`/`slg_sigma_end`) to control cost: applying it only in the mid-trajectory range (where structure is still being resolved) gets most of the benefit for a fraction of the full extra-forward-pass cost across the whole schedule.
- Skip it if generation time is already tight — every step inside the active window doubles that step's model compute.

## How to enable it

```yaml
- name: "generator/txt2vid_wan22"
  configuration:
    steps: 30
    slg_scale: 3.0
    slg_layers: "8,9,10"
    slg_sigma_start: 1.0
    slg_sigma_end: 0.4
```

`slg_layers` takes comma-separated transformer block indices (as strings); leaving it empty disables SLG even if `slg_scale` is nonzero.

## Tradeoffs and limitations

- Adds one full extra forward pass per active step — meaningfully slower than CFG-Zero*/APG, which are essentially free.
- Wan-only: has no effect on LTX or any image family, even though the config keys are accepted there without error.
- No formal published paper or golden reference backs the exact formulation used here; treat block-index and scale choices as empirical rather than paper-recommended.
- Choosing which blocks to skip (`slg_layers`) is model- and use-case-specific; there's no universally correct index set.
- The wiring is unit-tested, but output quality hasn't been benchmarked on real hardware — validate on a few real generations before relying on it for production output.
