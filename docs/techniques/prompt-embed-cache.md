---
type: technique
title: Prompt Embedding Cache
category_group: Performance
status: stable
families: [flux, krea2, qwen_image, wan, ltx, z_image, anima, minimax_h3]
authors: []
paper: null
reference_impl: null
knobs: []
related: [attention-backends]
---

# Prompt Embedding Cache

Encoding a prompt through a text encoder is real compute — for large text encoders it can be a
meaningful fraction of a generation's total time, especially in an iterate/seed-exploration loop
where the prompt itself doesn't change between requests. PotionUI caches the *output* of the text
encoder — the actual embeddings a prompt produces, not the encoder's weights — keyed by a stable
fingerprint of the text encoder's identity, the prompt text, and the encoding options used. When a
later request reuses the same prompt against the same text encoder with the same options, PotionUI
returns the cached embeddings directly and skips the encode pass entirely, including the GPU
placement work that normally comes with moving the encoder onto the device.

This sits alongside a separate, more general layer: PotionUI also keeps a loaded text encoder's
*weights* resident/cached in memory across calls under its normal model-lifecycle eviction policy,
independent of this embedding cache — that's ordinary model caching, not specific to prompts, and
exists regardless of whether embedding caching applies.

## When to use it

There is nothing to turn on — this is automatic. It benefits any workflow that re-encodes the same
prompt text repeatedly against the same model: seed exploration, batch variations where only the
seed changes, or a session where you generate the same prompt across a few settings tweaks that
don't touch the prompt itself. It provides no benefit when every request uses a different prompt.

## How to enable it

No configuration is needed or available. The cache activates automatically whenever the active text
encoder has a stable identity fingerprint it can key on; it is currently wired into the CLIP-loading
pipes for Flux, Krea-2, Qwen-Image, Wan, LTX, Z-Image, Anima, and MiniMax-H3. Encoders or code paths without a stable fingerprint
(for example, image-conditioned encodes) skip the cache automatically and simply run the normal
encode path — that fallback is an implementation detail, not a setting you need to manage.

## Tradeoffs and limitations

- No user-facing on/off switch exists; if you need to rule out stale-cache behavior while debugging
  a prompt-related issue, that isn't something you can toggle from a preset or the admin panel today.
- Cached values are detached CPU copies of the embeddings, so a hit does not pin GPU memory — but it
  also means a hit still pays a CPU-to-GPU materialization cost, just not the encoder forward pass or
  the encoder's own GPU residency dance.
- Only wired into the families listed above; other native families' text encoders (SDXL, SeedVR2) do
  not benefit from this cache.
- Caching is scoped to prompt text plus encode options and text-encoder identity — any change to any
  of those (including reloading the same text encoder into a new instance) is a cache miss, not a
  stale hit.
