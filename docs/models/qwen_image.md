---
type: model
title: Qwen-Image
family_key: qwen_image
modes: [txt2img, img2img, edit]
spec:
  arch: dual-stream joint-attention MMDiT
  latent: 16-channel, Wan21-format causal-3D VAE latent (image-only, single frame)
  vae: qwen_image (Wan 2.1 causal-3D VAE)
  te: Qwen2.5-VL-7B
  guidance: cfg
  shift: 1.15
  engine: native
files:
  - role: dit
    dir: models/checkpoints
  - role: text_encoder
    dir: models/text_encoders
    note: Qwen2.5-VL-7B
  - role: vae
    dir: models/vae
---

# Qwen-Image

Qwen-Image (`src/platform/runtime/native/arch/qwen_image/model.py`) is a dual-stream joint-attention MMDiT: image and text tokens each carry their own modulation/MLP, and every block runs one joint attention over the concatenated text-and-image sequence. It runs flow-matching with true classifier-free guidance — a real conditional/unconditional pair, unlike Flux's embedded guidance.

## Files & detection

The text encoder is Qwen2.5-VL-7B. The VAE is the Wan 2.1 causal-3D VAE, with per-channel scale/shift living in the VAE rather than a scalar. Two checkpoint variants exist: a plain build that ships bare fp8 with no sidecar tensors, and an "edit" variant that ships fp8-scaled with scale/quantization sidecar tensors — both load correctly.

## Presets & modes

The shipped Qwen-Image preset ships `txt2img`.

## Sampling

Default generation parameters: 20 steps, guidance 4.0 (true CFG scale), sampler `euler`, img2img strength 0.55.

## Limitations

Qwen2.5-VL has a different text-encoder architecture than CLIP-family encoders — there is no CLIP-skip concept for this family. A `clip_skip` setting carried over from an SDXL-style preset has no effect here.

## Hardware

The validated local checkpoint (`qwen_image_2512_fp8_e4m3fn`, bare fp8) is **~20 GB on disk**, plus the `qwen_2.5_vl_7b_fp8_scaled` text encoder. **Measured peak (32 GB-card ceiling):** 19.37 GB at 1024², 20 steps, true-CFG (`docs/native-engine.md`). That peak already sits close to a 20 GB card's ceiling, so a **12 GB card is marginal at best** — the fp8 DiT file alone nearly exceeds that budget before the text encoder and VAE are counted. 16 GB+ is the realistic floor for this family.
