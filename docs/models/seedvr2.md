---
type: model
title: SeedVR2
family_key: seedvr2
modes: [upscale]
spec:
  arch: ByteDance NaDiT, one-step APT (adversarial post-training) upscaler
  latent: causal-video VAE latent, self-normalizing
  vae: seedvr2
  te: "none (fixed prompt embedding)"
  guidance: "none"
  shift: null
  engine: native
files:
  - role: dit
    dir: models/checkpoints
    note: two shapes — seedvr2_3b and a wider/deeper seedvr2_7b
  - role: vae
    dir: models/vae
---

# SeedVR2

SeedVR2 (`src/platform/runtime/native/arch/seedvr2/model.py`, `arch/seedvr2_7b/model.py`) is a native-resolution restoration model — a **one-step** upscaler, not a multi-step diffusion model. The generator pipe runs a single forward pass at a fixed timestep rather than a denoising loop. It has no text encoder and no classifier-free guidance: it's an upscaler, not a text-to-image/video generator.

## Files & detection

Two checkpoint shapes exist: `seedvr2_3b` and a wider/deeper `seedvr2_7b` (plain-MLP blocks, video-only pixel RoPE, no output-norm head). Neither has a live text encoder — the model loader injects fixed prompt embeddings from bundled tensor files instead of encoding a prompt. The paired VAE is a causal-video VAE that is self-normalizing: it folds its own scaling inside encode/decode, so it skips the per-channel mean/std transform every other causal-3D-VAE family in this app needs.

## Presets & modes

The shipped SeedVR2 preset is an upscale pipe, not a generation mode — it takes an input image or video and produces an upscaled one.

## Sampling

Not applicable in the conventional sense — no steps/sampler/schedule. Default parameters: scale 2.0, target short side unset, color correction `wavelet`, tile size 1024, tile overlap 128. `latent_noise_scale` and `input_noise_scale` default to 0 (the augmentation is off, matching ByteDance's one-step APT inference script). Video-only knobs (temporal overlap, prepend frames, uniform batch size) are ignored on the image path.

The video path's **batch size** (frames per temporal batch, on the VAE's 4n+1 lattice) defaults to `0` = **auto**: it is sized from live free VRAM to ~72% of the card (the upstream ComfyUI node's fixed default of 5 leaves a large card mostly idle). A batch that exceeds real VRAM is halved and re-run by the generator's shrink-on-OOM ladder, so the estimate is safe; any explicit value is honoured. One batch is on the GPU at a time, so peak VRAM tracks the batch size, not the clip length.

## Limitations

Video-only knobs exist on the same config surface as the image path but have no effect when upscaling a still image.

## Hardware

Four DiT sizes ship as picker recommendations (`content/presets/marketplace/SeedVR2/modes/upscale/tabs/generation.yml`): 3B fp8 **3.39 GB**, 3B fp16 6.78 GB, 7B fp8 **8.24 GB**, 7B fp16 16.48 GB (the "sharp" 7B variants match the plain 7B sizes), plus a ~501 MB VAE. This is the one native family that **scales down cleanly to small cards** rather than hitting a hard floor: the 3B fp8 checkpoint fits comfortably on an 8 GB card, and the video path's batch size auto-sizes to ~72% of free VRAM and halves-and-retries on OOM rather than failing outright. 7B fp8 is the realistic choice at 12 GB; the fp16 variants want 16 GB+.
