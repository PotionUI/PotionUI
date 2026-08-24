---
type: technique
title: Temporal-Chunked VAE Decode
category_group: Memory
status: stable
families: [wan]
authors: []
paper: null
reference_impl: null
knobs: []
related: []
---

# Temporal-Chunked VAE Decode

Decoding a video's latent back into pixels can use a lot of VRAM, since the accumulated
pixel-frame output and intermediate activations grow with clip length. Wan's causal-3D video VAE
already processes one latent frame at a time internally, carrying convolution state forward between
frames. PotionUI reuses that same internal mechanism from the outside: instead of asking the VAE to
decode a whole clip in one call, it builds one persistent decode cache and feeds the clip through in
temporal slices across repeated decode calls. The result is mathematically identical to a single
whole-clip decode — it's a memory-shaping change, not a quality or approximation tradeoff — but peak
decode VRAM is bounded by the chunk size instead of the full clip length.

This is automatic engine behavior on Wan video generations: PotionUI decides at decode time,
based on live free VRAM, whether to decode the whole clip at once, in temporal chunks, or (if even
one frame's spatial decode doesn't fit) fall back to spatial tiling. There is nothing to turn on in
a preset.

## When to use it

Nothing to configure — PotionUI applies chunked decode automatically on Wan video generations
whenever a temporal chunk is needed to fit the decode inside available VRAM, and steps out of the
way (full single-call decode) when there's enough headroom to not need it. Longer clips and
tighter VRAM budgets are where it engages most often.

## How to enable it

Not a preset or environment toggle — it's an automatic part of the Wan video decode path
(`engine.py`'s decode orchestration and the Wan generator pipes' own decode call sites). If a
decode runs low on VRAM mid-clip, PotionUI restarts the whole clip at half the chunk size rather
than resuming partway (resuming would double-count the carried causal state), and falls back to
spatial tiling if even a single-frame chunk doesn't fit.

## Tradeoffs and limitations

- Wan-only: this depends on the Wan causal-3D VAE's internal per-frame decode cache
  (`new_feat_cache`). LTX's video pipes call the same shared decode primitive for consistency, but
  it is a verified no-op there — LTX's VAE has no `new_feat_cache` method, so `causal3d_chunk_frames`
  always returns `None` and LTX falls straight through to a plain single decode call regardless of
  clip length.
- SeedVR2's VAE is excluded by the same detection: it self-normalizes rather than using the Wan-style
  causal feature cache, so it's never routed through temporal chunking either.
- Temporal chunking is not combined with spatial tiling — they're mutually exclusive strategies
  picked based on whether a single frame's full-spatial decode fits in VRAM. If it doesn't, PotionUI
  uses spatial tiling instead, not both together.
- No user-facing knob: you cannot force chunk size or disable this behavior from a preset or setting.
