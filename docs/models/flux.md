---
type: model
title: Flux1 / Flux2 (Klein)
family_key: flux
modes: [txt2img, img2img]
spec:
  arch: MMDiT — per-block modulation (Flux1) or global dual/single-stream modulation (Flux2/Klein)
  params: "12B (Flux1) / 9B (Flux2/Klein)"
  latent: "16-channel (Flux1) or 128-channel (Flux2/Klein), 2D"
  vae: flux_ae (Flux1) / flux2_ae (Flux2/Klein)
  te: T5-XXL + CLIP-L (Flux1) or Qwen3 (Flux2/Klein)
  guidance: embedded (distilled)
  shift: "1.15 (Flux1) / 2.02 (Flux2/Klein)"
  engine: native
files:
  - role: dit
    dir: models/checkpoints
    note: single-file .safetensors, Flux1 or Flux2/Klein shape
  - role: text_encoder
    dir: models/text_encoders
    note: T5-XXL + CLIP-L for Flux1, Qwen3 for Flux2/Klein
  - role: vae
    dir: models/vae
---

# Flux1 / Flux2 (Klein)

One model class (`src/platform/runtime/native/arch/flux/model.py`) covers both variants — the same approach ComfyUI uses, because they differ only in configuration, not structure. The checkpoint's `image_model` field (`"flux"` vs `"flux2"`) picks which shape applies.

Flux1 (`image_model: "flux"`) is the original per-block-modulation MMDiT: `img_mod`/`txt_mod` per block, a GELU MLP, biases on, a pooled-CLIP `vector_in`, distilled `guidance_in` on dev checkpoints, patch size 2, 3 RoPE axes, 16-channel latents. Flux2/Klein (`image_model: "flux2"`) shares `double_stream_modulation_{img,txt}` and `single_stream_modulation` globally instead of per-block, uses a SiLU-gated MLP, drops biases and `vector_in`, has no `guidance_in`, patch size 1, 4 RoPE axes (text tokens on axis 3), and 128-channel latents. Both run flow-matching with embedded (distilled) guidance — neither uses a classifier-free guidance pass.

## Files & detection

One model-loader pipe handles both variants, and Krea-2 checkpoints share its text-encoder bridge (T5-XXL+CLIP-L for Flux1, or Qwen3 for Klein/Flux2). Each heavy component (text encoder, VAE, DiT) is cached under its own key, so a shared T5-XXL/Qwen3 encoder is reused across presets and swapping a LoRA re-loads only the DiT.

## Presets & modes

The shipped Flux preset ships `txt2img`. img2img is available through the shared img2img mixin used by every image-family generator.

## Sampling

Default generation parameters: 20 steps, guidance 3.5 (embedded/distilled — this is the CFG-equivalent knob for Flux, not a true-CFG scale), sampler `euler`, sigma shift unset (uses the per-variant default: 1.15 for Flux1, 2.02 for Flux2, unless a preset overrides it).

## Limitations

Flux1 needs both T5-XXL and CLIP-L; Klein/Flux2 needs only Qwen3 — mixing up which text-encoder file a preset expects for which variant is the most common setup error, since the loader pipe accepts either shape and only the detected checkpoint variant decides which one it actually reads.

## Hardware

Flux2/Klein's 9B DiT is roughly 18 GB on disk in bf16 (params × 2, since the checkpoint's own on-disk size isn't independently documented); the real fp8 checkpoint (`flux-2-klein-9b-fp8.safetensors`) is smaller. **Measured peak (32 GB-card ceiling):** fp8 28.7 GB and bf16 28.55 GB, both including the Qwen3 text encoder, 1024²/20 steps (`docs/native-engine.md`). Both precisions fit fully resident on a 32 GB card; below 16 GB this family relies on the native engine's fp8 quantize-at-load and partial-residency streaming, which is unmeasured at that tier — expect **viable but slow** on a 12 GB card, not recommended below that. Flux1 (T5-XXL + CLIP-L, 12B) has no local checkpoint and is untested end-to-end. See [Hardware Requirements](../user/hardware-requirements.md) for the cross-family comparison.
