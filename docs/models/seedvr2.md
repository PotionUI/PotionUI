---
type: model
title: SeedVR2
family_key: seedvr2
modes: [upscale, video_upscale]
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

Not applicable in the conventional sense — no steps/sampler/schedule. Default parameters: scale 2.0, target short side unset, color correction `wavelet`, tile size 2048, tile overlap 256. Both tile values are ceilings: the VAE encode and decode size their own tile from live free VRAM under them. `latent_noise_scale` and `input_noise_scale` default to 0 (the augmentation is off, matching ByteDance's one-step APT inference script). Video-only knobs (temporal overlap, prepend frames, uniform batch size) are ignored on the image path.

The video path's **batch size** (frames per temporal batch, on the VAE's 4n+1 lattice) defaults to `0` = **auto**: the largest batch, from 5 to 49 frames, whose modelled DiT peak (its weights plus an activation term per token at the output resolution) fits ~92% of live free VRAM (the upstream ComfyUI node's fixed default of 5 leaves a large card mostly idle). On a GPU of 12.5 GB or less (`POTIONUI_VRAM_CAP_GB` counts), auto is 5 frames: on a 12 GB RTX 4070 Ti under WSL2 the sizer picked 37 frames for the 3B fp16 checkpoint quantised to fp8 at load, and the DiT forward of the second batch ran the card out of memory. Any explicit value is honoured. One batch is on the GPU at a time, so peak VRAM tracks the batch size, not the clip length.

Each batch's VAE encode and decode are spatially tiled, the tile sized so its modelled peak fits 75% of live free VRAM with the overlap margin on top. The model's costs come from the VAE alone on a 12 GB RTX 4070 Ti (bf16 compute, the 501 MB VAE) at 1, 5 and 13 frames: the measured peak grows linearly with the tile's area, and per pixel it is affine in the frame count, about 0.0017 MB per frame plus 0.0010 MB for the encoder and 0.18 MB per frame plus 0.12 MB per latent pixel for the decoder. The generator counts 0.0018 and 0.19 MB per pixel over the frames plus one, which covers every measured peak; a one-frame image costs per frame several times what a long batch does.

A batch or a tile that runs out of memory is halved and re-run, except where the first driver-error OOM is fatal to the process, the default under WSL2 (`docs/native-engine.md`): there the encode, the DiT forward and the decode of a batch have to fit the first time.

## Limitations

Video-only knobs exist on the same config surface as the image path but have no effect when upscaling a still image.

## Hardware

Four DiT sizes ship as picker recommendations (`content/presets/marketplace/SeedVR2/modes/upscale/tabs/generation.yml`): 3B fp8 **3.39 GB**, 3B fp16 6.78 GB, 7B fp8 **8.24 GB**, 7B fp16 16.48 GB (the "sharp" 7B variants match the plain 7B sizes), plus a ~501 MB VAE. This is the one native family that **scales down cleanly to small cards** rather than hitting a hard floor: the 3B fp8 checkpoint fits comfortably on an 8 GB card, and the video path sizes its batch and its VAE tiles from live free VRAM. 7B fp8 is the realistic choice at 12 GB; the fp16 variants want 16 GB+.
