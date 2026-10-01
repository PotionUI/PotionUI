---
type: model
title: Qwen-Image-2.1
family_key: qwen_image21
modes: [txt2img, edit, control]
spec:
  arch: single-stream block-causal MMDiT
  latent: 64-channel, RGBA causal-3D VAE latent (16x downscale, image-only, single frame)
  vae: qwen_image21 (dedicated causal-3D VAE, RGBA in/out)
  te: Qwen3-VL-8B
  guidance: cfg
  shift: dynamic mu 0.5..0.9 (≈2.0 multiplicative at 1024²)
  engine: native
files:
  - role: dit
    dir: models/checkpoints
  - role: text_encoder
    dir: models/text_encoders
    note: Qwen3-VL-8B
  - role: vae
    dir: models/vae
  - role: model_patch
    dir: models/model_patches
    note: Fun ControlNet Union, control mode only
---

# Qwen-Image-2.1

Qwen-Image-2.1 (`src/platform/runtime/native/arch/qwen_image21/model.py`) is a single-stream 32-layer MMDiT: text and image tokens share one sequence and one set of attention projections per block, with a single modulation MLP shared across every block (unlike Qwen-Image 1.0's dual-stream design). Attention is **block-causal**: the joint sequence attends causally (`q_idx >= kv_idx`), except every image block (each reference image, and the target image) is internally bidirectional. Text and reference-image tokens are modulated from `t = 0` rather than the sampled timestep — a prefix K/V cache is a documented extension point on the checkpoint but is not implemented yet. It runs flow-matching with true classifier-free guidance — a real conditional/unconditional pair, unlike Flux's embedded guidance. Unlike 1.0, the DiT consumes VAE latents **unpatched** (no internal 2x2 packing): `in_channels` is the raw 64 latent channels.

## Files & detection

The text encoder is Qwen3-VL-8B (not Qwen2.5-VL — a different encoder than 1.0's, no CLIP-L). The VAE is a dedicated causal-3D VAE (NOT the Wan 2.1 VAE 1.0 reuses): 64 latent channels, 16x spatial downscale, per-channel latent mean/std (like Krea-2's), and **RGBA** pixel space (4 channels) rather than RGB. A plain RGB input is auto-padded with a full-opacity alpha channel before encoding.

## Presets & modes

The shipped Qwen-Image-2.1 preset ships `txt2img`, `edit` and `control` (see [Fun ControlNet Union](#fun-controlnet-union) below). `edit` reuses the SAME checkpoint set as `txt2img` — `model_loader/qwen_image21` loads the Qwen3-VL-8B text encoder's vision tower (`vision: true`) so the text conditioning is grounded on up to 10 reference images, and `generator/qwen_image21`'s `GeneratorQwenImage21Pipe.maybe_edit` VAE-encodes each reference (its own area-target aspect, snapped to the 16px granularity) into `ref_latents`. Each reference is spliced INTO the text run at the `image_slots` position the text encoder recorded (`QwenImage21TextEncoder._encode_with_images`), not simply appended after all the text — `QwenImage21DiT.build_sequence` honours those slots (falling back to "after the text" for any reference without one, which keeps a caller that never passes `image_slots` — e.g. a plain txt2img forward — byte-identical to the pre-edit layout).

**Output is RGBA when the checkpoint actually produced transparency, RGB otherwise.** The VAE always decodes 4 channels; `generator/qwen_image21` checks the alpha channel after decode (`drop_opaque_alpha`, `src/pipelines/pipes/_shared/imaging/alpha.py`) and drops it to a plain RGB image only when every pixel is fully opaque — byte-identical to the previous unconditional `Image.convert("RGB")` for every generation that doesn't produce transparency. A genuinely transparent decode (any pixel below full opacity) is kept as RGBA end-to-end: saved as PNG (main output) and WebP-with-alpha (thumbnails), and shown with a checkerboard backdrop in the workbench/gallery/details views. There is no separate "transparent background" toggle — like ComfyUI, whatever the VAE decodes is what gets saved; the checkpoint only produces meaningful alpha when the prompt actually asks for a subject on a transparent background.

## Fun ControlNet Union

`control` mode runs Alibaba PAI's [Qwen-Image-2.1-Fun-Controlnet-Union](https://huggingface.co/alibaba-pai/Qwen-Image-2.1-Fun-Controlnet-Union) on top of the base DiT. One 7.55 GB file covers eight control types (Canny, Depth, Grayscale, HED, Lineart, MLSD, Pose, Scribble) and inpainting, alone or together.

**Files and detection.** The file is a control branch only: `control_img_in` (129 -> 4096) and 16 `control_blocks`, each a full copy of a base block plus a zero-initialised `after_proj` (block 0 also has a `before_proj`). It lives under the `model_patch` model type (`models/model_patches/`, ComfyUI's folder). `detect/patch_detect.py` recognises it by `control_img_in.weight` + `control_blocks.0.img_mlp.out.weight` without the `img_mod` a Qwen-Image 2.0 Fun ControlNet carries. Three published layouts load: the official file (diffusers-split `img_mlp.gate_layer`/`proj`, fused at load), Kijai's bf16 repack (fused `img_mlp.gate_up`) and Kijai's int8 ConvRot repack (`comfy_quant` int8_tensorwise, served by the existing quantised `Linear`).

**Loading.** The patch is merged into the DiT's own state dict under `fun_control.` (`ModelSpec.model_patch_map` -> `arch/qwen_image21/fun_control.py:attach_fun_control`), so the DiT and its control branch are one module: placement, partial streaming, fp8 quantise-at-load and the OOM retries treat them as one ~21.7 GB component, and no family-specific memory code exists. `model_loader/qwen_image21`'s `control_model` gives the patched DiT its own cache key (`native/dit/<dit>+control=<patch>`) and fingerprint, so a cached plain DiT never serves a control run or the reverse; switching between control mode and the other modes reloads the DiT, the same as a LoRA change does for this family. A control run whose DiT has no `fun_control` module stops with "The Fun ControlNet model isn't loaded" instead of running unguided.

**Math** (`vendor/gpl/comfyui/qwen_image21/fun_control.py`, from ComfyUI PR #16519, checked against VideoX-Fun). The control stream starts as `before_proj(scatter(control_img_in(ctx)))` + the joint sequence entering base block 0, with the control tokens on the target rows and zeros on text rows. After base block `2j`, control block `j` runs on the stream with the same modulation, RoPE and block-causal mask, and `after_proj` of its output, times the strength (`control_context_scale`), is added to the base hidden states. Strength 0, or no control, runs the unchanged forward.

**The form.** The Control tab takes one **Image** (required) and an optional painted mask on it. **Guide** picks what the output follows: None, Canny, Depth, Pose, Lineart, HED, MLSD, Scribble or Grayscale. **Extract the guide from a photo** (on by default, hidden for None and Grayscale) runs the matching preprocessor; turn it off when the image already is a pose, edge or depth map and it is used unchanged. An old saved form with the retired "Use as is" value loads as Canny with the switch off, which feeds the model the same image (the Fun ControlNet takes no guide type, only the map). The guide is computed from the Image unless **Take the guide from a different image** is ticked, which reveals a **Guide image** upload under it that is then required (for example, a photo of the pose you want). **Strength** (`control_context_scale`) shows while a Guide is chosen, and in the Advanced view **Control start** and **Control end** limit the guide to a fraction of the sampling (default 0 to 1, the whole run). The four combinations are a guide alone (structure from the image, everything generated new), inpaint alone (Guide None: only the masked area is repainted, no control latents), inpaint with a guide, and a guide taken from a different image. Guide None without a painted mask is refused at submit with a plain message on the Image field. The canvas always takes the Image's aspect ratio, at the area of the chosen resolution.

**Control context.** 129 channels per latent token, built by `generator/qwen_image21/control.py`: the guide's VAE latent (64), a keep mask (1 = keep, nearest-downsampled), and the VAE latent of the Image with the repainted area set to mid-grey (64). Missing parts are zeros, as upstream does, so without a mask the inpaint channels and the keep mask are zero and the run is pure structure generation. Mask convention: white = repaint, the same as the mask editor writes.

**Start and end.** As in ComfyUI PR #16519, the window maps through the model's own schedule: `sampling/flow_schedule.py:percent_to_sigma` (0 -> sigma 1, 1 -> sigma 0, otherwise the same shift curve the sampler uses, `exp(mu) / (exp(mu) + 1/t - 1)` with the resolution's mu at `t = 1 - percent`) gives `(sigma_start, sigma_end)`, and the DiT applies the control only on steps whose sigma lies in that range. Other steps run the plain forward and may use the prefix K/V cache. An empty window (start after end) is the same as no control.

**Guidance and caching.** The control branch is CFG-distilled: the `Control` speed profile runs 40 steps at CFG 1.0, a single forward per step. With CFG above 1 both passes get the same control, as upstream. A controlled forward bypasses the prefix K/V cache, exactly like VideoX-Fun, so the cache never holds control-shaped prefix keys. An uncontrolled forward is unchanged.

**Preprocessing.** `controlnet_preprocessor` turns a photo into the chosen map (Pose is OpenPose). It runs with `strict: true` in this mode, so a missing `controlnet-aux` package, a failed detector, or a detector that found nothing (a blank map, e.g. Pose run on an image that already is a pose skeleton) stops the generation with a plain message telling the user to turn off Extract the guide from a photo. Grayscale, and any guide with the switch off, need no extra package.

**Licence.** The weights are under the Qwen Research License, like the base model.

## Sampling

Default generation parameters: 40 steps, guidance 4.0 (true CFG scale), sampler `euler`, resolution-dynamic sigma shift: mu interpolated from 0.5 at 256 image tokens to 0.9 at 8192 (diffusers `base_shift`/`max_shift`, the same curve ComfyUI's FLUX-type `ModelSamplingFlux` applies to its `shift: 0.69` = mu at 1024²), mapped through `exp(mu)`; a manual `shift` override in the Advanced tab is a plain multiplicative shift (2.0 ≈ the 1024² default).

The preset's **Sampler** and **Schedule** pickers (`type: "sampler"` / `type: "schedule"`, family `qwen_image21`) are registry-driven: they list whatever this instance has registered, core entries plus any a plugin contributes, rather than a list written into the preset. See [Samplers and sigma schedules](../techniques/samplers-and-schedules.md).

## Limitations

- No img2img mode; only `txt2img`, `edit` and `control`.
- `control` does not combine with edit references.
- `edit` accepts up to 10 reference images; the first sets the output's size and aspect and is the image being edited, the rest condition the generation without affecting output sizing.
- Qwen3-VL has a different text-encoder architecture than CLIP-family encoders — there is no CLIP-skip concept for this family. A `clip_skip` setting carried over from an SDXL-style preset has no effect here.
- No distilled/Lightning LoRA exists yet for Qwen-Image-2.1; the preset's "Turbo" speed profile (20 steps) is a proposed fast profile that expects one to be paired in on the LoRA tab for acceptable quality, same as Qwen-Image 1.0's turbo profile.

## Licence

The Comfy-Org repack ships under the **Qwen Research License** (not Apache-2.0) — see `https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE`. Commercial use requires accepting Alibaba's separate terms; this differs from Qwen-Image 1.0's Apache-2.0 files.

## Hardware

The bf16 checkpoint set is **~33 GB on disk** together (14.23 GB DiT + 17.53 GB text encoder + 0.68 GB VAE) — no fp8/quantized repack is published yet. `recommended_vram_gb: 24` on the preset; a 24 GB card needs the model-lifecycle cache's text-encoder offload between the encode and denoise phases to fit, which is automatic. No `min_vram_gb` until a lower tier is GPU-validated.
