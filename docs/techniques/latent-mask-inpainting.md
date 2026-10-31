---
type: technique
title: Latent-Mask Inpainting
category_group: Sampling
status: needs-gpu-validation
families: [krea2, moss_image, qwen_image21, z_image]
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
(`src/platform/runtime/native/sampling/latent_mask.py`). After every model call the clean-latent
estimate is pinned to the source latent wherever the mask is 0 and kept where it is 1, and the
velocity is rebuilt from the pinned estimate. Outside the mask the trajectory therefore follows the
source latent's own noised path, while the masked area is free to change and still sees the kept area
through attention. When sampling ends, every latent whose mask value is 0 is replaced by the source
latent, so kept latents come back bit-identical to the input.

A fractional mask value blends the estimate between source and model. The mask handed to the engine
is the pixel mask max-pooled to the latent grid, so a latent cell that touches any painted pixel is
repainted whole.

The decoded picture is composited over the source pixels by the pixel mask, which keeps untouched
pixels exact despite the VAE round trip.

## Presets

- **Inpaint** (Z-Image, MOSS-Image, Krea-2, Qwen-Image-2.1): paint the area, optionally describe what
  should be there. The painted area is dilated by *Grow*, cropped with surrounding context
  (`inpaint_region_crop`, scaled strategy), repainted, and composited back by `inpaint_region_restore` (`inpaint` strategy)
  with a *Feather* seam that fades inward from the painted edge, and the edge tone is pulled toward the surroundings.
- **Outpaint** (Z-Image): `canvas_expand` grows the canvas by pixels per side or to a target shape,
  fills the new area with a blurred copy of the edge and masks it, plus a few pixels of the original
  edge so the seam blends. The generator repaints the mask and returns the whole canvas.

## Tradeoffs and limitations

- Lowering *Strength* below 1.0 starts the masked area from a partly noised copy of itself, so it
  keeps colour and layout but cannot add much that is new.
- The prompt describes what belongs in the painted area. With no prompt, the preset asks for the
  surroundings continued, which suits removing an object.
- The mask is applied in latent space, so the finest detail at its edge is decided by the latent grid
  (8 to 16 pixels per cell depending on the family) and the pixel feather.
