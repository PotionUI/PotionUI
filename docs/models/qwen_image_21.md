---
type: model
title: Qwen-Image-2.1
family_key: qwen_image21
modes: [txt2img, edit]
spec:
  arch: single-stream block-causal MMDiT
  latent: 64-channel, RGBA causal-3D VAE latent (16x downscale, image-only, single frame)
  vae: qwen_image21 (dedicated causal-3D VAE, RGBA in/out)
  te: Qwen3-VL-8B
  guidance: cfg
  shift: dynamic mu 0.5..0.9 (≈2.0 multiplicative at 1024²)
  engine: native
files:
  - role: dit
    dir: models/checkpoints
  - role: text_encoder
    dir: models/text_encoders
    note: Qwen3-VL-8B
  - role: vae
    dir: models/vae
---

# Qwen-Image-2.1

Qwen-Image-2.1 (`src/platform/runtime/native/arch/qwen_image21/model.py`) is a single-stream 32-layer MMDiT: text and image tokens share one sequence and one set of attention projections per block, with a single modulation MLP shared across every block (unlike Qwen-Image 1.0's dual-stream design). Attention is **block-causal**: the joint sequence attends causally (`q_idx >= kv_idx`), except every image block (each reference image, and the target image) is internally bidirectional. Text and reference-image tokens are modulated from `t = 0` rather than the sampled timestep — a prefix K/V cache is a documented extension point on the checkpoint but is not implemented yet. It runs flow-matching with true classifier-free guidance — a real conditional/unconditional pair, unlike Flux's embedded guidance. Unlike 1.0, the DiT consumes VAE latents **unpatched** (no internal 2x2 packing): `in_channels` is the raw 64 latent channels.

## Files & detection

The text encoder is Qwen3-VL-8B (not Qwen2.5-VL — a different encoder than 1.0's, no CLIP-L). The VAE is a dedicated causal-3D VAE (NOT the Wan 2.1 VAE 1.0 reuses): 64 latent channels, 16x spatial downscale, per-channel latent mean/std (like Krea-2's), and **RGBA** pixel space (4 channels) rather than RGB. A plain RGB input is auto-padded with a full-opacity alpha channel before encoding.

## Presets & modes

The shipped Qwen-Image-2.1 preset ships `txt2img` and `edit`. `edit` reuses the SAME checkpoint set as `txt2img` — `model_loader/qwen_image21` loads the Qwen3-VL-8B text encoder's vision tower (`vision: true`) so the text conditioning is grounded on up to 10 reference images, and `generator/qwen_image21`'s `GeneratorQwenImage21Pipe.maybe_edit` VAE-encodes each reference (its own area-target aspect, snapped to the 16px granularity) into `ref_latents`. Each reference is spliced INTO the text run at the `image_slots` position the text encoder recorded (`QwenImage21TextEncoder._encode_with_images`), not simply appended after all the text — `QwenImage21DiT.build_sequence` honours those slots (falling back to "after the text" for any reference without one, which keeps a caller that never passes `image_slots` — e.g. a plain txt2img forward — byte-identical to the pre-edit layout).

**Output is always opaque RGB, in both modes**: `generator/qwen_image21` drops the VAE's alpha channel after decode (`Image.convert("RGB")`, a straight channel truncation, not a composite-over-white). True RGBA output would be a further follow-up.

## Sampling

Default generation parameters: 40 steps, guidance 4.0 (true CFG scale), sampler `euler`, resolution-dynamic sigma shift: mu interpolated from 0.5 at 256 image tokens to 0.9 at 8192 (diffusers `base_shift`/`max_shift`, the same curve ComfyUI's FLUX-type `ModelSamplingFlux` applies to its `shift: 0.69` = mu at 1024²), mapped through `exp(mu)`; a manual `shift` override in the Advanced tab is a plain multiplicative shift (2.0 ≈ the 1024² default).

The preset's **Sampler** and **Schedule** pickers (`type: "sampler"` / `type: "schedule"`, family `qwen_image21`) are registry-driven: they list whatever this instance has registered, core entries plus any a plugin contributes, rather than a list written into the preset. See [Samplers and sigma schedules](../techniques/samplers-and-schedules.md).

## Limitations

- No img2img mode; only `txt2img` and `edit`.
- Output is always opaque RGB; the VAE's alpha channel is dropped, never composited, even for `edit`'s reference images.
- `edit` accepts up to 10 reference images; the first sets the output's size and aspect and is the image being edited, the rest condition the generation without affecting output sizing.
- Qwen3-VL has a different text-encoder architecture than CLIP-family encoders — there is no CLIP-skip concept for this family. A `clip_skip` setting carried over from an SDXL-style preset has no effect here.
- No distilled/Lightning LoRA exists yet for Qwen-Image-2.1; the preset's "Turbo" speed profile (20 steps) is a proposed fast profile that expects one to be paired in on the LoRA tab for acceptable quality, same as Qwen-Image 1.0's turbo profile.

## Licence

The Comfy-Org repack ships under the **Qwen Research License** (not Apache-2.0) — see `https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE`. Commercial use requires accepting Alibaba's separate terms; this differs from Qwen-Image 1.0's Apache-2.0 files.

## Hardware

The bf16 checkpoint set is **~33 GB on disk** together (14.23 GB DiT + 17.53 GB text encoder + 0.68 GB VAE) — no fp8/quantized repack is published yet. `recommended_vram_gb: 24` on the preset; a 24 GB card needs the model-lifecycle cache's text-encoder offload between the encode and denoise phases to fit, which is automatic. No `min_vram_gb` until a lower tier is GPU-validated.
