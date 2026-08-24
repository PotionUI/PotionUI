---
title: Models
category: Presets / Models
category_order: 70
order: 60
---

# Models

Reference pages for each model family PotionUI drives directly, one page per family: what the architecture is, what files/text-encoder/VAE it needs, and what a preset for it looks like. Each family page also lists which optimization and quality techniques apply to it — see [Techniques](../techniques/) for how each of those works and how to turn it on. This section is about the **models**, not preset-authoring mechanics ([Presets](../presets.md)) or how a downloaded file becomes a selectable, backend-loadable row ([Models and Backend Availability](../models.md)) — read those for the surrounding machinery this section assumes.

Nine of the ten families below run on the native engine ([Native Engine v2](../native-engine.md)): a shared detection/loading/ops/attention/sampling stack that every native transformer family plugs into, so a technique landing once is a candidate for every family on that stack, subject to per-family eligibility. SDXL is the exception — it runs on a `diffusers`-based pipeline stack with its own, separate quality and performance techniques.

## Families

- [Flux1 / Flux2 (Klein)](flux.md) — text-to-image, image-to-image
- [Krea-2](krea2.md) — text-to-image, image-to-image
- [Qwen-Image](qwen_image.md) — text-to-image, image-to-image
- [Z-Image](z_image.md) — text-to-image, image-to-image
- [Anima](anima.md) — text-to-image, image-to-image
- [Wan 2.1 / 2.2](wan.md) — text-to-video, image-to-video, chained video
- [LTX-2 / 2.3](ltx.md) — text-to-video, video director (keyframes, audio)
- [MiniMax-H3](minimax_h3.md) — text-to-video-and-audio, first/last keyframe anchoring (territorially restricted weights)
- [MiniMax-Music3](minimax_music3.md) — text-to-music, structured caption + tagged lyrics
- [SDXL](sdxl.md) — text-to-image, image-to-image, inpaint (diffusers pipeline)
- [SeedVR2](seedvr2.md) — image and video upscaling

One preset directory exists that this section does not cover: **Chroma** has loader/generator pipes on disk but no shipped preset and no native-engine detection entry — it is not a working, documentable family.

See [Native Engine Optimizations](../native-optimizations.md) for how the technique catalog fits together, and [Models and Backend Availability](../models.md) for how a downloaded checkpoint file becomes the row a preset's `model` field picks.
