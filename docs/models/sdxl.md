---
type: model
title: SDXL
family_key: sdxl
modes: [txt2img, inpaint, refine_faces, upscale, restyle]
spec:
  arch: SDXL U-Net (diffusers pipeline stack, not the native transformer engine)
  latent: 4-channel SD-VAE latent, 2D
  vae: SDXL VAE
  te: CLIP-L + OpenCLIP-G
  guidance: cfg
  shift: null
  engine: diffusers
files:
  - role: dit
    dir: models/checkpoints
    note: single-file .safetensors, loaded via diffusers' from_single_file
  - role: vae
    dir: models/vae
    note: optional custom VAE override
---

# SDXL

SDXL is the one family that runs on diffusers pipelines rather than the native transformer engine every other family page in this section documents. Checkpoint loading and generation both call into `diffusers` directly, and the SDXL generator does not use the shared attention dispatcher or any other native-engine module. None of the native engine's attention backends, first-block cache, native fp8 matmul, or regional `torch.compile` machinery applies to SDXL — it has its own, separate quality and performance stack.

The generator/loader split is component-based rather than one monolithic wrapper: input validation, model wrapping (which also owns ControlNet configuration and dynamic GPU/CPU placement), conditioning building, a denoising hook, sampler configuration, a sharpness filter, inpainting, image processing, and post-processing each live in their own module. ADM guidance, self-attention guidance, and the sharpness filter are separate pipes, documented individually — see the related techniques below.

## Files & detection

SDXL checkpoints load via `from_single_file()` (a single safetensors file), with support for LoRAs, textual-inversion embeddings, and custom VAEs.

## Presets & modes

The shipped SDXL preset lives under the same directory layout as the native-engine presets — the shared "native" label refers to plugin-free in-process execution, contrasted with a remote ComfyUI backend, not to the transformer engine. A ComfyUI-backed SDXL preset also exists as an alternative.

### Fix faces & hands (`refine_faces`)

Takes one picture and repaints its faces and hands with the checkpoint you pick; everything else is left as it is. It reuses the `detailer/sdxl` pipe that txt2img runs after generation, as two passes: a face pass, then a hand pass on its result. **Fix** chooses faces, hands or both, **Strength** is the denoise of each repaint (0.4 by default), and the prompt box is the optional face prompt. An empty prompt falls back to `detailed face, detailed eyes, best quality`; the hands use their own **Hand description** (Advanced) instead, so a face prompt never leaks into the hands.

The detectors are YOLO files picked in the `detection_bbox` pickers. The mode recommends `face_yolov8s.pt` / `face_yolov8n.pt` and `hand_yolov8s.pt` / `hand_yolov8n.pt` from `Bingsu/adetailer` (the model card declares Apache-2.0; the files are Ultralytics YOLOv8 weights, whose upstream licence is AGPL-3.0). A picker is hidden, and so not required, when its target is not selected. The detailer skips a detection whose model file is missing and logs a warning rather than failing.

### Upscale & refine (`upscale`)

Enlarges one picture 1.5 to 4 times with an ESRGAN upscaler, then re-details it tile by tile with the checkpoint (`upscaler`, then `tiled_detailer/sdxl`, the same pair txt2img runs in its Enhance tab). **Detail strength** is the per-tile denoise (0.25 by default; above about 0.45 the checkpoint starts inventing content). Turning **Re-detail** off runs the upscaler alone: the checkpoint, prompt encoder and tiled detailer are disabled and the Checkpoint picker is hidden, so no checkpoint has to be installed. The prompt is optional; an empty one is replaced by `best quality, highly detailed`. Tile overlap, blending, minimum tile size, detail boost and the two adaptive options are under Advanced.

### Img2img / restyle (`restyle`)

Starts from one picture instead of noise: the picture is encoded, partly noised by **Strength** and denoised again toward the prompt. Around 0.3 changes finish and detail, 0.6 changes style and mood, 0.9 keeps only the rough layout. It uses the `generator/sdxl` img2img path with no mask (the same path inpaint uses with one), plus the LoRA and Embeddings tabs of txt2img. **Working size** caps the source at 1, 2 or 4 megapixels (default 1) through the generator's `max_pixels` option, shrinking a larger picture to a multiple of 8 before it is encoded; Original size keeps it. **Quantity** loads the source once per image, and each image gets its own seed and its own expanded prompt.

## Sampling

Default generation parameters: 25 steps, CFG scale 6.0, sampler `DPMPP_2M`, scheduler `karras`, 1024×1024 resolution, CLIP-skip 2, inpaint strength 0.8. This is SDXL's own sampler/scheduler configuration (U-Net + k-diffusion-style samplers), independent of the flow-matching sampler menu documented for the transformer families — the names overlap in places (`dpmpp_2m`) but the two are not the same code path. A separate `guidance_rescale` knob (default 0.0) exists here; it is not the same mechanism as CFG-Zero* on the transformer families.

Prompt weighting and CLIP-skip both apply here specifically — unlike the transformer families, which have no CLIP-skip concept at all.

## Limitations

SDXL is architecturally and operationally separate from every other family in this section. A knob, env gate, or performance feature documented for the transformer families (attention backends, first-block cache, native fp8 matmul, `torch.compile`) has no effect on an SDXL generation.

## Hardware

SDXL is the one family with its own VRAM-tier policy, separate from the native engine's fit-first placement (`src/platform/runtime/model_lifecycle/memory_policy.py`): **< 8 GB** aggressive (sequential CPU offload, max attention slicing, VAE slicing+tiling), **8–12 GB** balanced (model offload, auto attention slicing), **12–16 GB** light (no offload, VAE slicing only), **≥ 16 GB** minimal (fully GPU-resident). SDXL is PotionUI's supported floor — it is the one family guaranteed to run on an 8 GB card. Checkpoint files are single-file safetensors, typically a few GB each. No host-RAM-specific constraint beyond the general model-cache floor (`max(8 GB, 10% of system RAM)` kept free — see the [Hardware Requirements](../user/hardware-requirements.md) page).
