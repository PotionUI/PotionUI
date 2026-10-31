---
type: technique
title: Latent-Mask Inpainting
category_group: Sampling
status: needs-gpu-validation
families: [moss_image, qwen_image21, z_image]
authors: []
paper: null
reference_impl: null
knobs:
  - key: mode
    surface: preset
    default: txt2img
    effect: "`inpaint` repaints the masked area of a picture and leaves the rest of the latent untouched"
  - key: denoise
    surface: preset
    default: 1.0
    effect: "How much of the schedule the masked area walks; 1.0 repaints it from noise, lower keeps more of its colours and shapes"
---

# Latent-Mask Inpainting

Inpaint mode repaints a painted area of a picture with the model's ordinary weights. No inpaint
checkpoint, LoRA or ControlNet is needed, so every family whose generator inherits the shared
img2img path gets it from one mechanism.

## How it works

The source picture is VAE-encoded to a latent. The sampler starts from the usual noise blend of that
latent, then wraps the guidance strategy in `LatentMaskGuidance`
(`src/platform/runtime/native/sampling/latent_mask.py`). The mask is the painted area plus a soft edge
outside it, downsampled to the latent grid by area averaging, so edge cells hold fractional values.

Sampling is differential, in the style of differential diffusion. At each step a cell is free (denoised
by the model) only when its mask value is at least `1 - progress`. Cells at 1 are free from the first
step; soft edge cells are freed later the lower their value, so they receive only small changes and
the model can continue the background across the edge. Cells at 0 are never freed. Pinned cells have
their clean estimate set to the source latent on every step, so they follow the source's own noised
path; at the end, every cell that was never freed is replaced by the source latent, so kept latents
come back bit-identical to the input.

Inside the painted area the output is always fully generated. The decoded picture is composited over
the source pixels by the soft pixel mask, and the final restore takes the painted area whole, fading
out only beyond its edge, so nothing of the old content shows through.

## Presets

- **Inpaint** (Z-Image, MOSS-Image, Qwen-Image-2.1): paint the area, optionally describe what
  should be there. The painted area is dilated by *Grow*, cropped with surrounding context
  (`inpaint_region_crop`, scaled strategy), repainted, and composited back by `inpaint_region_restore` (`inpaint` strategy)
  with a *Feather* band outside the painted edge.

## Tradeoffs and limitations

- A cast shadow can only be repainted inside the painted area, so paint where the shadow should fall.

- Lowering *Strength* below 1.0 starts the masked area from a partly noised copy of itself, so it
  keeps colour and layout but cannot add much that is new.
- The prompt describes what belongs in the painted area. With no prompt, the preset asks for the
  surroundings continued, which suits removing an object.
- The mask is applied in latent space, so its edge is decided by the latent grid (8 to 16 pixels per
  cell depending on the family) and the pixel feather.
