---
type: model
title: Z-Image
family_key: z_image
modes: [txt2img, img2img]
spec:
  arch: NextDiT (Alpha-VLLM Lumina-Image-2.0 lineage), hidden dim 3840
  latent: 16-channel, Flux-format 2D VAE latent
  vae: flux_ae
  te: Qwen3-4B (read at the penultimate layer)
  guidance: cfg
  shift: 3.0
  engine: native
files:
  - role: dit
    dir: models/checkpoints
    note: covers turbo, base, and finetune checkpoints — one model spec, different preset defaults
  - role: text_encoder
    dir: models/clip
    note: Qwen3-4B
  - role: vae
    dir: models/vae
---

# Z-Image

Z-Image (`src/platform/runtime/native/arch/z_image/model.py`) is a NextDiT backbone at hidden dimension 3840. One model spec covers turbo, base, and finetuned checkpoints — they're structurally identical; only a preset's steps and CFG scale differ between them. It runs flow-matching with true classifier-free guidance, but at `cfg_scale=1.0` the guidance strategy collapses to a single forward pass (the unconditional pass is skipped) — the operating point the distilled turbo checkpoint targets. Base and finetuned checkpoints run a real conditional/unconditional pair.

## Files & detection

A `z_image_modulation` flag in the checkpoint distinguishes a Z-Image checkpoint from a generic Lumina2 one at the same hidden size. The text encoder is Qwen3-4B, read at the **penultimate layer** — not the final hidden state, a specific contract of this family's text-encoder integration. The VAE is the Flux-style 2D autoencoder (16-channel) — despite the NextDiT lineage, Z-Image's latent space is Flux's, not a video/causal-3D one.

## Presets & modes

The shipped Z-Image preset ships `txt2img`.

## Sampling

Default generation parameters: 8 steps, guidance 1.0 (turbo default — the single-forward collapse point), sampler `euler`, img2img strength 0.55.

## Limitations

Raising guidance above 1.0 on the turbo checkpoint doubles compute (a real unconditional forward pass is now required) without necessarily improving quality — turbo checkpoints are typically distilled specifically for the `cfg_scale=1.0` operating point. Check the specific checkpoint's own documentation before assuming a higher CFG scale helps.

## Hardware

The validated local checkpoints (`zImage_turbo`, the `cyberrealisticZImage_v30` finetune) are **~11.5 GB in bf16** (`docs/native-engine.md`) — the smallest native-engine DiT documented. No GPU end-to-end peak has been measured yet (CPU-validated only), so treat this as **unvalidated but the best native bet on a small card**: its file size alone comfortably fits a 12–16 GB budget once the small Qwen3-4B text encoder and the Flux-style 2D VAE (both far smaller than the causal-3D VAEs the video families use) are added.
