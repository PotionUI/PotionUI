---
type: model
title: Z-Image
family_key: z_image
modes: [txt2img, img2img, enhance, inpaint, outpaint, control]
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
    dir: models/diffusion_models
    note: covers turbo, base, and finetune checkpoints — one model spec, different preset defaults
  - role: text_encoder
    dir: models/text_encoders
    note: Qwen3-4B
  - role: vae
    dir: models/vae
  - role: control_model
    dir: models/model_patches
    note: optional, Structure mode only — the Fun ControlNet Union 2.1 patch
---

# Z-Image

Z-Image (`src/platform/runtime/native/arch/z_image/model.py`) is a NextDiT backbone at hidden dimension 3840. One model spec covers turbo, base, and finetuned checkpoints — they're structurally identical; only a preset's steps and CFG scale differ between them. It runs flow-matching with true classifier-free guidance, but at `cfg_scale=1.0` the guidance strategy collapses to a single forward pass (the unconditional pass is skipped) — the operating point the distilled turbo checkpoint targets. Base and finetuned checkpoints run a real conditional/unconditional pair.

## Files & detection

A `z_image_modulation` flag in the checkpoint distinguishes a Z-Image checkpoint from a generic Lumina2 one at the same hidden size. The text encoder is Qwen3-4B, read at the **penultimate layer** — not the final hidden state, a specific contract of this family's text-encoder integration. The VAE is the Flux-style 2D autoencoder (16-channel) — despite the NextDiT lineage, Z-Image's latent space is Flux's, not a video/causal-3D one.

## Presets & modes

The shipped Z-Image preset ships `txt2img`, `img2img` (shown as Restyle), `enhance`, `inpaint`, `outpaint` and `control` (shown as Structure).

- **Restyle** re-renders a picture with a prompt and a Strength slider. The generator runs `mode: "img2img"`, so the output takes the picture's shape (up to about one megapixel) and Strength is the fraction of the schedule walked.
- **Enhance** upscales a picture by 1.5x, 2x or 3x and redraws it at a low strength set by *Added detail* (Subtle 0.2, Balanced 0.35, Strong 0.5), then optionally runs the face detailer on the result. It is the same `img2img` generator with `img2img_scale`, followed by `detailer/native`.
- **Inpaint** repaints a painted area. The generator runs `mode: "inpaint"`: the unpainted latents stay pinned to the source on every step, so only the painted area changes (see [Latent-Mask Inpainting](../techniques/latent-mask-inpainting.md)). The area is grown by *Grow*, cropped with its surroundings, repainted and composited back with a *Feather* seam that fades out beyond the painted edge; the repainted area itself is never blended with the old content. The prompt is optional; blank asks for the surroundings continued. Strength 1.0 repaints from noise.
- **Outpaint** extends the canvas by pixels per side or to a target shape (16:9, 21:9, 4:3, 9:16 and so on), with the picture centred or kept at an edge. `canvas_expand` fills the new area with a blurred copy of the edge and masks it plus *Blend* pixels of the original edge; the same inpaint generator fills it. The canvas is painted at *Output size* (1, 2 or 4 MP, default 1, the model's native size), scaled back up to the full canvas, and your original pixels are pasted over it. *Blend* is how far into your picture the model looks and fades its repaint, so the new area continues it.
- **Structure** generates a new image that keeps the pose, depth, outlines or a loose sketch of a guide picture. See below.

## Structure (Fun ControlNet Union 2.1)

Structure loads Alibaba PAI's `Z-Image-Turbo-Fun-Controlnet-Union-2.1` (Apache-2.0) as a *model patch* beside the diffusion model (`model_loader/z_image` `control_model`, cached under its own DiT key). The patch is attached at load time by `src/platform/runtime/native/arch/z_image/control.py`, derived from VideoX-Fun's `z_image_transformer2d_control.py`.

- **Layout.** The patch adds an input embedder for the control context, two control blocks that run beside the noise refiner, and a stack of control blocks (15 in the full file, 3 in the lite file) that run beside every Nth joint block, where N is the base block count divided by the control block count. Each control block ends in a zero-initialised `after_proj` whose output is added to the base stream, scaled by *Strength*.
- **Control context.** The guide is VAE-encoded and concatenated with a mask channel and an inpaint-latent block, 33 latent channels in all. Structure fills only the guide channels; the other 17 stay zero.
- **Hints.** The control stream runs over `[caption ; control rows]` so it can read the prompt, but hints are added to the whole sequence, caption rows included, as upstream does.
- **Variants are checked strictly.** The loader refuses the Union 1.0 file (16 control channels, no refiner control) and the Union 2.0 file, whose noise-refiner control blocks were never trained because of an upstream typo fixed in 2.1. It also refuses a patch of the wrong width or a block count that does not divide the diffusion model's.
- **Guides.** *Follow* picks the preprocessor: Pose (DWPose, drawn as an OpenPose skeleton), Depth, Edges (Canny) or Sketch (scribble). Turn off *Find the guide in the picture* to use a ready-made map unchanged.
- **Strength.** The released model recommends 0.65 to 1.0; the preset defaults to 0.8. *Guide starts* and *Guide ends* limit the guide to a window of the sampling.

Recommended files (`alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union-2.1`, Apache-2.0): the 2602 8-step full file (6.25 GB) and the 2602 8-step lite file (1.88 GB). The 8-step files are distilled for the turbo checkpoint's 8 steps at guidance 1.0.

## Sampling

Default generation parameters: 8 steps, guidance 1.0 (turbo default — the single-forward collapse point), sampler `euler`.

The preset's **Sampler** and **Schedule** pickers (`type: "sampler"` / `type: "schedule"`, family `z_image`) are registry-driven: they list whatever this instance has registered, core entries plus any a plugin contributes, rather than a list written into the preset. See [Samplers and sigma schedules](../techniques/samplers-and-schedules.md).

## Limitations

Raising guidance above 1.0 on the turbo checkpoint doubles compute (a real unconditional forward pass is now required) without necessarily improving quality — turbo checkpoints are typically distilled specifically for the `cfg_scale=1.0` operating point. Check the specific checkpoint's own documentation before assuming a higher CFG scale helps.

## Hardware

The validated local checkpoints (`zImage_turbo`, the `cyberrealisticZImage_v30` finetune) are **~11.5 GB in bf16** (`docs/native-engine.md`) — the smallest native-engine DiT documented. No GPU end-to-end peak has been measured yet (CPU-validated only), so treat this as **unvalidated but the best native bet on a small card**: its file size alone comfortably fits a 12–16 GB budget once the small Qwen3-4B text encoder and the Flux-style 2D VAE (both far smaller than the causal-3D VAEs the video families use) are added.
