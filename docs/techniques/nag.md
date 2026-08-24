---
type: technique
title: Normalized Attention Guidance (NAG)
category_group: Quality
status: needs-gpu-validation
families: [wan, ltx]
authors: []
paper: {arxiv: "2505.21179", title: "Normalized Attention Guidance"}
reference_impl: null
knobs:
  - key: nag_scale
    surface: preset
    default: 1.0
    effect: "Strength of negative-prompt steering applied inside cross-attention; 1.0 = off."
  - key: nag_tau
    surface: preset
    default: 3.5
    effect: "Norm-clamp threshold that limits how far the guided attention output can deviate from the positive one."
  - key: nag_alpha
    surface: preset
    default: 0.5
    effect: "Blend-back weight toward the unclamped positive attention output."
related: [cfg-zero-star, apg]
---

# Normalized Attention Guidance (NAG)

Normalized Attention Guidance enforces a negative prompt without paying for a second full model pass. Ordinary classifier-free guidance runs the whole model twice per step — once conditioned on the positive prompt, once on the negative — and combines the two outputs. NAG instead applies the negative-prompt influence *inside* the cross-attention layers themselves: it attends the same queries against both the positive and negative text separately (reusing the same attention weights), then blends the two attention outputs with a formula that extrapolates away from the negative result, clamps the result so it doesn't wander too far from the positive one, and blends a fraction of it back in. The net effect is negative-prompt steering that rides along inside a single overall forward pass rather than requiring two.

In practice this means NAG is a speed technique wearing a quality technique's clothes: it lets a preset run with `cfg=1.0` (i.e. skip the CFG scale entirely — a single forward pass per step) while still getting meaningful negative-prompt steering from `nag_scale`, instead of needing a full second CFG pass to get the same effect. `nag_tau` controls how tightly the guided result is clamped back toward the positive prediction (lower values are more conservative), and `nag_alpha` controls how much of that clamped correction is actually blended in versus falling back to the plain positive output.

## When to use it

- Use NAG when generation speed matters and the preset can tolerate `cfg=1.0` (single forward pass) but still needs the negative prompt to have visible effect — this is the main reason to reach for it over plain CFG.
- Typical `nag_scale` values are `1.1`–`1.5`; `1.0` is off. Push higher for stronger negative-prompt suppression, but watch for artifacts as it climbs.
- Not a substitute for APG/CFG-Zero* if the actual problem is oversaturation from a high CFG scale — NAG doesn't touch that axis; it's about getting negative-prompt influence cheaply.

## How to enable it

```yaml
- name: "generator/txt2vid_wan22"
  configuration:
    steps: 30
    cfg: 1.0
    nag_scale: 1.3
    nag_tau: 3.5
    nag_alpha: 0.5
```

Also available on LTX generator pipes (`generator/video_ltx`, `generator/txt2vid_ltx`) with the same three keys.

A preset must ALSO mirror `nag_scale` onto its `prompt_encoder` pipe:

```yaml
- name: "prompt_encoder"
  configuration:
    guidance_scale: 1.0
    nag_scale: 1.3
```

`prompt_encoder._do_cfg()` encodes the negative pass when `guidance_scale > 1.0`
**or** `nag_scale > 1.0`. Without the mirror, a preset at `cfg: 1.0` never encodes
a negative conditioning at all, `_attach_nag`'s `uncond is None` guard returns the
cond dict untouched, and the control silently does nothing — in exactly the
distilled/turbo case NAG exists for. Mirror it onto every generator stage that
sets `nag_scale`, including a stage-2 refine pass whose own `cfg` is pinned to 1.0.

## Tradeoffs and limitations

- Available on Wan and LTX generator pipes; not wired into any image family or SeedVR2.
- There are **two** `_attach_nag` helpers and they build different payloads. Wan and LTX pipes share the one in `generator/txt2vid_wan22/main.py`, which sets only `nag_context`/`nag`; the flow-matching families use the one in `pipes/_shared/generation/flow_generator_pipe.py`, which additionally sets `nag_attention_mask`. Both no-op identically on `nag_scale <= 1.0 or uncond is None`.
- So on LTX the negative context's padding mask never reaches the kernel — the key is never even produced, and `LTXAVModel.forward`'s `nag_attention_mask` keeps its `None` default. The NAG branch therefore attends the negative context including any padding, which is the same treatment LTX already gives the positive context (its pipes call the DiT with `attention_mask=None` unconditionally). The upside: NAG costs no attention-backend downgrade on LTX. A non-`None` mask *would* force `sdpa` on the `attn2`/`audio_attn2` calls and lose sage (`native/attention.py:486`), so if `nag_attention_mask` is ever wired up for LTX, that trade-off has to be weighed then. Today the only cost is one extra cross-attention pass per block.
- The speed win only materializes if the preset actually drops CFG scale to `1.0` — setting `nag_scale` alongside a full CFG pass just adds extra attention compute for a redundant negative-prompt path.
- Very high `nag_scale` can produce visible attention-guidance artifacts, similar in kind (though not identical) to over-driving CFG scale; `nag_tau`/`nag_alpha` exist specifically to keep this in check.
- The wiring is unit-tested, but output quality hasn't been benchmarked on real hardware — validate on a few real generations before relying on it for production output.
