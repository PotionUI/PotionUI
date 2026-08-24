---
type: technique
title: SVI Pro 2.0 chain continuity
category_group: Quality
status: needs-gpu-validation
families: [wan]
authors: []
paper: null
reference_impl: {name: "ComfyUI-Wan-SVI2Pro-FLF", url: "https://github.com/Well-Made/ComfyUI-Wan-SVI2Pro-FLF", license: "GPLv3"}
knobs:
  - key: motion_latent_count
    surface: preset
    default: 1
    effect: "How many temporal latent slots of the previous segment's tail carry into the next segment's conditioning (1 slot ~= 4 pixel frames). Lower keeps a lighter motion hand-off; raise to re-inject more of the tail."
  - key: anchor_latent_strength
    surface: preset
    default: 1.0
    effect: "Mask weight on each segment's anchor (start) frames. 1.0 hard-locks them to the anchor (identity); lower loosens the lock so dynamic scenes can move away from it. End (last-frame) locks are unaffected."
related: [riflex, nag]
---

# SVI Pro 2.0 chain continuity

Stable Video Infinity (SVI) Pro is a recipe for generating arbitrarily long Wan 2.2 video as a
chain of short segments, where each segment continues the motion of the one before it. PotionUI's
`generator/chain_video_wan22` pipe already implements the chain architecture — per-segment expert
routing, tail hand-off, and an anchor frame at position 0. This technique covers the two knobs that
tune how strongly one segment carries into the next, exposed on the Wan preset's **SVI Pro** tab
(director mode only).

The knob semantics were re-derived from the behavior and documentation of the GPL-licensed
`ComfyUI-Wan-SVI2Pro-FLF` reference nodes; no code was copied from that project.

## Motion latents carried (`motion_latent_count`)

When a chain segment continues from the previous one, the previous segment's trailing frames are fed
back in as the new segment's start conditioning. Wan's causal VAE packs 4 pixel frames into one
temporal latent slot, so the amount of motion context that crosses the seam is best measured in
latent slots, not pixel frames.

`motion_latent_count` caps that hand-off to N latent slots. Internally the pipe keeps the last
`(N-1)*4 + 1` pixel frames of the tail — the exact count the i2v conditioning mask packs into
**exactly N fully-provided latent slots** — bounded by the available overlap tail. The SVI Pro
recipe uses a light hand-off, so the default is **1**: a single latent slot (one pixel frame). Raise
it when continuations feel disconnected from the previous shot and you want more of the prior motion
re-injected; a value large enough to cover the whole overlap tail reproduces the pre-SVI-Pro
behavior (the full tail was carried).

## Anchor lock strength (`anchor_latent_strength`)

Every segment is anchored at position 0 — the start frame (a fresh image for an i2v/flf opener, or
the carried motion tail for a continuation) is written into the conditioning and locked via the
i2v concat mask. At full strength the mask marks that anchor as fully given, so the sampler
reproduces it exactly (identity lock).

`anchor_latent_strength` scales the mask weight over the anchor frames only. **1.0** (default) is the
exact hard lock; lowering it toward **0.7–0.9** softens the lock so a dynamic scene is free to move
away from where it was anchored instead of freezing on the first frame. The end (last-frame / FLF)
lock is never touched — only the front anchor is attenuated.

## Recommended recipe

The SVI Pro 2.0 recipe these knobs are tuned for:

- Pair the Wan 2.2 i2v high/low experts with the **SVI Pro 2.0** LoRA (its HighNoise variant on the
  high expert) plus a Wan lightning/lightx2v distill LoRA pair (typical strengths 0.5–1.0).
- Use the **euler** sampler.
- Generate **81-frame** segments as the golden standard — long enough for the model to adapt to a
  new prompt direction after continuing the previous motion, short enough to stay coherent.
- Keep the **same prompt** across segments unless you deliberately want a scene change; if the model
  isn't following a new prompt, prefer "doing X" over "starting to do X" so the action begins sooner.
- **Field-validated starting point** (clean seams, no brightness pop, 2026-07-21):
  `motion_latent_count: 2`, `anchor_latent_strength: 0.75`, overlap 4, **SVI LoRA strength 0.9** —
  now the preset defaults. Raise the anchor toward 1.0 for a harder identity lock on stable
  locked-camera scenes; drop `motion_latent_count` to 1 for the lightest possible hand-off.

## How to enable it

```yaml
- name: "generator/chain_video_wan22"
  configuration:
    motion_latent_count: 1
    anchor_latent_strength: 1.0
```

## Tradeoffs and limitations

- Both knobs affect only director-mode chains; the single-shot t2v/i2v/flf modes ignore them.
- The defaults are a deliberate behavior change from the earlier chain implementation (which carried
  the full overlap tail at a hard anchor lock). A high `motion_latent_count` restores the old
  hand-off exactly; `anchor_latent_strength: 1.0` restores the old anchor lock exactly.
- `anchor_latent_strength` below 1.0 feeds the model a fractional mask weight, which is out of the
  binary distribution Wan's i2v conditioning was trained on; it interpolates in practice but the
  useful range is narrow (roughly 0.7–1.0).
- Output quality of the two knobs on real hardware has not been benchmarked yet — validate on a few
  real generations before relying on non-default values.
