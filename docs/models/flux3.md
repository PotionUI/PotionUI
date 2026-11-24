---
type: model
title: FLUX 3 Image Klein
family_key: flux3
modes: [txt2img, edit]
spec:
  arch: FLUX 3 Image Klein DiT (5 per-mode blocks followed by 28 joint blocks)
  latent: 128-channel FLUX 2 autoencoder latent (16x downscale, 2D)
  vae: flux2_ae (FLUX 3 ae.safetensors)
  te: Qwen3-VL-4B (hidden states of four layers concatenated)
  guidance: "base: true CFG; distilled: flow-map sampling, no CFG"
  shift: "6.93, multiplicative"
  engine: native
files:
  - role: dit
    dir: models/checkpoints
    note: distilled or base
  - role: text_encoder
    dir: models/text_encoders
    note: Qwen3-VL-4B
  - role: vae
    dir: models/vae
---

# FLUX 3 Image Klein

FLUX 3 Image Klein (`src/platform/runtime/native/arch/flux3/model.py`) is Black Forest Labs' open 5.6B image model. Text, reference-image and target-image tokens share one sequence, and each token kind has its own embedder. The first blocks attend within a token kind only; the rest attend across all of them. References get their own RoPE position along an extra axis, so up to 10 reference images condition an edit without any vision tower.

## Files & detection

Two checkpoints share the architecture and are told apart from the weights:

- **Base** (`flux3_klein_base`): 50 steps, true CFG (default 3.5), empty prompt as the unconditional pass, no CFG-Zero\*.
- **Distilled** (`flux3_klein`): a flow-map model, 16 steps, no CFG. Each step jumps to the next sigma, so it only runs with the `euler` sampler.

The text encoder is Qwen3-VL-4B, encoded one prompt per pass with the hidden states of four layers concatenated. It is the same file the Krea-2 preset uses; the loader caches it under its own key so the two families never share an embedding. The VAE is the FLUX 3 `ae.safetensors`, which loads as the FLUX 2 autoencoder.

## Presets & modes

<!-- modes:start -->
_Generated from the preset definitions by `scripts/model_modes_reference.py`; edit the presets, not this list._

- `txt2img`: Text to Image
- `edit`: Edit

<!-- modes:end -->

The shipped **FLUX 3** preset has `txt2img` and `edit`, each with a LoRA tab (up to 10 LoRAs, strength -10 to 10; the DiT takes LoRAs keyed per layer, including ones that split q/k/v), with a Turbo (16 steps, no CFG), a Base (50 steps, CFG 3.5, the default) and a Custom speed profile. The pipes (`model_loader/flux3`, `generator/flux3`) pick their behaviour from the loaded checkpoint, not from the profile: a distilled checkpoint ignores CFG and the step cache and refuses any sampler but Euler, a base checkpoint runs two-pass CFG. The profile only sets steps and CFG: a Base profile on a distilled checkpoint still runs, and a Turbo profile on a base checkpoint runs without guidance (the log says so).

`edit` takes 1 to 10 reference images (`generator/flux3/references.py`):

- **One image:** fitted under 4 megapixels without upscaling, with the longest side capped at 4096 px, rounded up to a multiple of 16 and reflect-padded. When Output size is left on "Match first reference", the padding is cropped off the result.
- **Several images:** each fitted under 1 megapixel and centre-cropped to a multiple of 16. The output defaults to the first image's size.
- Picking an Output size on the Generation tab overrides both and skips the crop.

## Sampling

Resolutions are multiples of 16. The sigma shift is 6.93 unless the Advanced tab overrides it. The preset's Sampler and Schedule pickers (family `flux3`) list whatever the instance has registered; see [Samplers and sigma schedules](../techniques/samplers-and-schedules.md).

## Limitations

- No img2img and no inpaint: FLUX 3 Klein changes an existing picture only through `edit`, which conditions on 1 to 10 reference images and has no denoise strength or mask. For that reason the preset ships no Upscale or Fix faces mode either, since both are low-denoise img2img passes.
- No bounding boxes and no prompt upsampler.
- The distilled checkpoint runs only with Euler and ignores CFG.
- NVFP4 checkpoints are not supported.

## Licence

The weights are under the FLUX Kommunity License, a non-commercial license; see the model repository's `LICENSE.md`. The reference code is Apache-2.0.

## Hardware

The preset recommends 16 GB of VRAM. The DiT (about 11 GB) and the text encoder (about 9 GB in bf16) are swapped between the encode and denoise phases.
