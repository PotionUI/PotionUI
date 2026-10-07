---
title: Models
category: Presets / Models
category_order: 70
order: 60
---

# Models

Reference pages for each model family PotionUI drives directly, one page per family: what the architecture is, what files/text-encoder/VAE it needs, and what a preset for it looks like. Each family page also lists which optimization and quality techniques apply to it — see [Techniques](../techniques/) for how each of those works and how to turn it on. This section is about the **models**, not preset-authoring mechanics ([Presets](../presets.md)) or how a downloaded file becomes a selectable, backend-loadable row ([Models and Backend Availability](../models.md)) — read those for the surrounding machinery this section assumes.

Every family below except SDXL runs on the native engine ([Native Engine v2](../native-engine.md)): a shared detection/loading/ops/attention/sampling stack that every native transformer family plugs into, so a technique landing once is a candidate for every family on that stack, subject to per-family eligibility. SDXL is the exception — it runs on a `diffusers`-based pipeline stack with its own, separate quality and performance techniques.

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
- [YuE2](yue2.md) — text-to-music, style tags + tagged lyrics, optional chain-of-thought
- [SDXL](sdxl.md) — text-to-image, image-to-image, inpaint (diffusers pipeline)
- [SeedVR2](seedvr2.md) — image and video upscaling
- [Pixal3D](pixal3d.md) — image to textured 3D mesh, single view or a four-view orbit (TRELLIS.2 with pixel-aligned conditioning)

One preset directory exists that this section does not cover: **Chroma** has loader/generator pipes on disk but no shipped preset and no native-engine detection entry — it is not a working, documentable family.

## Recognition from file headers

PotionUI recognises these families from the tensor names in a file's header, which is how a bare
transformer sitting in a `Stable-diffusion` or `unet` folder ends up as a diffusion model instead
of a checkpoint (see [How a model's type is decided](../models.md#how-a-models-type-is-decided)).
"Full checkpoint listed" means a native diffusion-model picker also offers all-in-one files of that
family, using only their transformer.

| Family | Recognised from headers | Full checkpoint listed |
|---|---|---|
| Flux1 / Flux2 (Klein) | yes | yes |
| Krea-2 | yes | yes |
| Qwen-Image, Qwen-Image 2.1 | yes | yes |
| Z-Image | yes | yes |
| Anima | yes (weights stored under a `net.` prefix are handled) | yes |
| Wan 2.1 / 2.2 | yes | yes, except the VACE, camera, audio and animate add-ons |
| SeedVR2 | yes | yes |
| MiniMax-H3 | yes | yes |
| LTX-2 / 2.3 | yes | no |
| MiniMax-Music3, YuE2 | yes | no |
| SDXL, SD 1.x / 2.x, SD3, Chroma | yes (as checkpoints) | no |

TRELLIS.2 and Pixal3D are not in this table: model indexing does not read their headers, so their
files take their type from the folder they sit in. The Pixal3D bundle uses the same four prefixes
as the TRELLIS.2 bundle. Only the family's own load-time check (`arch/trellis2/detect.py`, which
probes for the `proj_linear` weights) tells the two apart, and the single-view and multi-view
Pixal3D bundles differ only by file name.

bitsandbytes (nf4, fp4) files and GGUF files are never listed as full checkpoints. A plugin can add
recognition for another family; see
[Contributing a model classifier](../plugin-api.md#contributing-a-model-classifier).

See [Native Engine Optimizations](../native-optimizations.md) for how the technique catalog fits together, and [Models and Backend Availability](../models.md) for how a downloaded checkpoint file becomes the row a preset's `model` field picks.
