---
type: model
title: Qwen-Image
family_key: qwen_image
modes: [txt2img, img2img, edit, camera, real]
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

The shipped Qwen-Image preset ships `txt2img`, `img2img`, `edit`, `camera` and `real`.

### Picture layout in edit modes

`edit`, `real` and `camera` read the source picture through the Qwen2.5-VL vision tower, and the layout follows the loaded checkpoint. The Edit-Plus releases (2509 and 2511) get the layout they were trained with (diffusers `QwenImageEditPlusPipeline`, ComfyUI `TextEncodeQwenImageEditPlus`): the instruction reads `Picture 1: <image>` and the picture the vision tower sees is box-filtered to at most 384 x 384 pixels in area. The original Qwen-Image-Edit release keeps its single-image layout, with no label and the picture at full size. The reference latent the DiT edits stays at about 1 MP either way. For a 1200 x 1500 photo the vision tower sees about 2,300 tokens in the single-image layout and 180 in the Plus layout.

2511 is recognised by its timestep-zero marker. 2508 and 2509 share every key and shape, so the original release is recognised by the values of its `proj_out` bias, cast to fp8 e4m3 and hashed so that bf16, fp16, fp32 and fp8 copies all match. Every other checkpoint gets the Plus layout. A mode can override either half with `picture_labels` and `grounding_area` on `prompt_encoder`.

### Camera angle

`camera` shows the same picture from another viewpoint. It is an `edit` run (the 2511 edit checkpoint with the vision tower loaded) with the fal Multiple-Angles LoRA applied, trained on 3,000+ Gaussian Splat renders over 96 camera positions. The mode is promptless: its three dials compose the LoRA trigger `<sks> [azimuth] [elevation] [distance]` and nothing the user types reaches the model.

- **Turn** -135 to 180 in steps of 45 degrees. 0 is the original front view, positive goes to the subject's right, negative to its left, 180 is behind (front, front-right quarter, right side, back-right quarter, back, back-left quarter, left side, front-left quarter).
- **Tilt** -30, 0, 30 or 60 degrees (low-angle, eye-level, elevated, high-angle).
- **Zoom** close-up, medium shot or wide shot.

The LoRA is a required `camera_lora` model field with a pinned recommendation (`qwen-image-edit-2511-multiple-angles-lora.safetensors`, 281.5 MB, Apache-2.0) and loads first, at the **Angle strength** (Advanced, default 0.9, range 0 to 2; the card recommends 0.8 to 1.0, and 0 runs the mode with the LoRA off). Any LoRAs picked on the LoRA tab load after it. Only the 2511 checkpoints are recommended: the LoRA was trained on 2511, and dx8152's 2509 Multiple-angles LoRA uses free-text camera commands instead of this trigger, so it is not wired here.

**Why not Qwen-Image 2.1.** Both multi-angle LoRAs target the dual-stream layout (60 blocks, width 3072, `add_*_proj` and `txt_mlp` keys). The 2.1 DiT is single-stream with 32 blocks of width 4096. Mapping the real safetensors headers onto a meta-device 2.1 DiT matched 0 of 840 (fal) and 0 of 720 (dx8152) LoRA modules, and the same headers matched all of them on the Qwen-Image DiT.

### Make it real

`real` turns an illustration, anime, painting, render or 3D shot into a photograph and keeps the composition. It is an `edit` run on the 2511 checkpoint with the lrzjason Anything2Real 2601 LoRA (required `real_lora` field, pinned recommendation `anything2real_2601.safetensors`, 585.1 MB, Apache-2.0). The instruction follows the LoRA card: `change the picture 1 to realistic photograph`, with the optional prompt appended as a description (`..., a woman in a red coat on a rainy street`). The LoRA loads first at **Realism strength** (Advanced, default 1.0; the card suggests 0.75 to 1), then any LoRAs from the LoRA tab. If the result is not photographic enough the card advises changing the strength or the checkpoint before raising it further.

Qwen-Image 2.1 gets the same task as the **Photo** task of its Restyle mode, prompt-only (no LoRA), see [Qwen-Image 2.1](qwen_image_21.md).

### Picture layout in edit mode

`edit` reads the source picture through the Qwen2.5-VL vision tower, and the layout follows the loaded checkpoint. The Edit-Plus releases (2509 and 2511) get the layout they were trained with (diffusers `QwenImageEditPlusPipeline`, ComfyUI `TextEncodeQwenImageEditPlus`): the instruction reads `Picture 1: <image>` and the picture the vision tower sees is box-filtered to at most 384 x 384 pixels in area. The original Qwen-Image-Edit release keeps its single-image layout, with no label and the picture at full size. The reference latent the DiT edits stays at about 1 MP either way. For a 1200 x 1500 photo the vision tower sees about 2,300 tokens in the single-image layout and 180 in the Plus layout.

2511 is recognised by its timestep-zero marker. 2508 and 2509 share every key and shape, so the original release is recognised by the values of its `proj_out` bias, cast to fp8 e4m3 and hashed so that bf16, fp16, fp32 and fp8 copies all match. Every other checkpoint gets the Plus layout.

## Sampling

Default generation parameters: 20 steps, guidance 4.0 (true CFG scale), sampler `euler`, img2img strength 0.55.

The preset's **Sampler** and **Schedule** pickers (`type: "sampler"` / `type: "schedule"`, family `qwen_image`) are registry-driven: they list whatever this instance has registered, core entries plus any a plugin contributes, rather than a list written into the preset. See [Samplers and sigma schedules](../techniques/samplers-and-schedules.md).

## Limitations

Qwen2.5-VL has a different text-encoder architecture than CLIP-family encoders — there is no CLIP-skip concept for this family. A `clip_skip` setting carried over from an SDXL-style preset has no effect here.

## Hardware

The validated local checkpoint (`qwen_image_2512_fp8_e4m3fn`, bare fp8) is **~20 GB on disk**, plus the `qwen_2.5_vl_7b_fp8_scaled` text encoder. **Measured peak (32 GB-card ceiling):** 19.37 GB at 1024², 20 steps, true-CFG (`docs/native-engine.md`). That peak already sits close to a 20 GB card's ceiling, so a **12 GB card is marginal at best** — the fp8 DiT file alone nearly exceeds that budget before the text encoder and VAE are counted. 16 GB+ is the realistic floor for this family.
