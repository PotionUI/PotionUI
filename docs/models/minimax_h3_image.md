---
type: model
title: MiniMax-H3-Image
family_key: minimax_h3_image
modes: [txt2img, edit]
spec:
  arch: MiniMax-H3 packed-sequence DiT without the audio stream (50 blocks, hidden size 5376, pruned rank-8 AdaLN, six per-step output heads)
  latent: 24-channel MiniMax-H3 image VAE latent (16x downscale, one frame)
  vae: minimax_h3_image (causal-conv encoder, 24-layer 2D ViT decoder)
  te: Qwen3-VL-32B truncated to 50 layers (the MiniMax-H3 text encoder file)
  guidance: "none (distilled); the step's target sigma picks the output head"
  shift: "fixed six-step grid from the checkpoint metadata"
  engine: native
files:
  - role: dit
    dir: models/diffusion_models
    note: int8 ConvRot or bf16 (the w4a8 file is not supported)
  - role: text_encoder
    dir: models/text_encoders
    note: Qwen3-VL-32B, nvfp4 AWQ or int8 ConvRot, shared with MiniMax-H3
  - role: vae
    dir: models/vae
    note: minimax_h3_image_vae_fp16, not the MiniMax-H3 video VAE
---

# MiniMax-H3-Image

MiniMax-H3-Image (`src/platform/runtime/native/arch/minimax_h3/image.py`) is MiniMax's image model: the MiniMax-H3 transformer with its audio stream removed, denoising one latent frame. It does text to image and editing with up to 16 reference pictures. It is distilled to six steps, so there is no guidance scale and no negative prompt, and it is tuned for about 2048×2048 pixels of area.

## Files & detection

Comfy-Org/MiniMax-H3-Image ships three DiT files with one architecture:

- `minimax_h3_image_pruned_bf16.safetensors`: 37.5 GB.
- `minimax_h3_image_pruned_int8_convrot.safetensors`: 19.5 GB. Only the four block projections are int8 ConvRot; the token refiner, AdaLN, patch projection and output heads keep their source precision.
- `minimax_h3_image_pruned_w4a8.safetensors`: 11.7 GB, a 4-bit codebook format (`asym_w4a8_int8`). Not supported: loading it stops with a message naming the files that work.

A DiT is recognised as MiniMax-H3-Image when it has `video_patch_proj` and `condition_proj` but no `audio_patch_proj`. Its `final_layer.video_out` holds six output heads (576 rows instead of 96), and the checkpoint metadata (`config.transformer`) carries the step grid they were distilled on: `pdd_steps` 6 and `pdd_sigmas` `[1.0, 0.96752, 0.88755, 0.75, 0.53277, 0.23201, 0.0]`. A file whose head count and grid disagree is refused. The per-block `comfy_attention.config` hints are dropped at load.

The text encoder is the Qwen3-VL-32B file MiniMax-H3 uses (`qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` or the int8 ConvRot one), loaded with its vision tower so edit pictures can be shown to it. Both presets cache it under the same key.

The VAE is `minimax_h3_image_vae_fp16.safetensors` (1.1 GB): the MiniMax-H3 causal-conv encoder with a 24-layer, 16-head 2D ViT decoder (`src/platform/runtime/native/vae/minimax_h3_image.py`). It is told apart from the H3 video VAE by its 768-row `decoder.proj_out` and 2D `post_quant_conv`. The file carries no latent statistics; the 24 means and standard deviations come from the upstream `vae/config.json` (they equal the MiniMax-H3 video VAE's). Pixels go in and out ImageNet-normalised, and encode and decode run in 256-pixel tiles with a 64-pixel overlap. When decoded tiles are joined, each one is blended against neighbours that are already blended, as the reference's tiled decode does, so the corners where overlaps cross match it. The VAE runs in float16, its stored precision, even on cards where the engine would otherwise pick bfloat16: the class declares float16 and float32 as its working precisions (the reference allows only those two), and the loader honours that for encode and decode alike.

## Presets & modes

The shipped **MiniMax-H3-Image** preset has `txt2img` and `edit`. The pipes are `model_loader/minimax_h3_image` and `generator/minimax_h3_image`.

**Edit** takes up to 16 pictures in order. Picture 1 is the image being edited. The text encoder sees each picture after a `Picture n: ` label at a 1024-pixel short edge; the generator VAE-encodes each one at a 1024 to 2048-pixel short edge and puts it in the sequence ahead of the image being generated. *Same size as Picture 1* (on by default) gives the result Picture 1's shape at that VAE size.

**Prompt enhancement.** The preset's `llm.guide` tells the assistant how this model wants its prompts written: one long, concrete paragraph for text to image, and for an edit a paragraph in three parts that names what it takes from each picture as `<Subject N>` from `<Picture N>`, says what stays (`<Subject N> is fully preserved.`) and then describes only what changes. The guides follow the format of MiniMax's published prompt-expansion instructions (`MiniMaxAI/MiniMax-H3-Image-PE`) and are written in PotionUI's own words; MiniMax's prompt files and its prompt-expansion model are not bundled.

## LoRA

Both modes have a LoRA tab (up to six LoRAs, strength -2 to 2). The loader (`model_loader/minimax_h3_image`) applies them to the DiT when it loads, through the same key mapping MiniMax-H3 uses (`build_minimax_h3_lora_key_map` in `src/platform/runtime/native/lora/key_mapping.py`), because the image DiT is the MiniMax-H3 block stack:

- **Formats.** ComfyUI and kohya keys (`diffusion_model.blocks.N.attn.qkv_proj`, `lora_unet_blocks_N_attn_qkv_proj`), the `base_model.model.` PEFT wrapper, and diffusers/PEFT keys (`transformer_blocks.N.attn.to_q/to_k/to_v/to_out.0`, `ff.net.0.proj`, `ff.net.2`, `token_refiner.refiner_blocks.N.*`, with or without PEFT's adapter name in the key). Split `to_q`, `to_k` and `to_v` land on the matching row slices of the fused `qkv_proj`, and the SwiGLU halves of `ff.net.0.proj` are swapped into the model's `[gate | value]` layout.
- **Quantised DiT.** On the int8 ConvRot DiT the LoRA is not merged into the weights; its low-rank factors are kept next to each layer and added in every forward. On the bf16 DiT it is merged in place.
- **Changing LoRAs** reloads the DiT file (the text encoder and the VAE stay cached), because the LoRA is part of the DiT's cache key.
- **No step window.** The six steps each use their own output head, so a LoRA cannot be switched off for part of the run.
- **History.** Active LoRAs are listed under Models and come back with Reuse settings.

**MiniMax-H3 video LoRAs** load, but only partly. They are not listed apart from image LoRAs: PotionUI does not read a family from a LoRA file, so the picker lists every LoRA that passes the preset's *LoRA tags* setting, and a tag is the way to keep the two kinds apart. Keys for layers the image model does not have (the audio patch projection, the audio output head, the video model's time embedder) and the AdaLN projections of a video LoRA (their input width belongs to the video checkpoint) are skipped and counted in the run log as unmatched keys; the attention and feed-forward layers still apply. A video distillation LoRA that depends on its AdaLN rows is therefore not equivalent to what it was trained to be.

## Sampling

The generator runs six Euler steps on the checkpoint's grid (`h3_image_pdd` schedule). Each step uses its own output head: head 0 is a full output layer and heads 1 to 5 are offsets added to it, picked from the step's start and target sigma. The model receives each step's sigma and target sigma in float32, so the time-embedding lookup and the head choice use the exact grid values, and the final latent stays float32 until it is denormalised and cast once to the VAE's precision. The model is called with `t = 1 - sigma`; reference rows run at `max(t, 0.999)` and are mixed with 0.1% noise drawn from the generation seed. The packed sequence is `[text | pictures | image]`: text rows sit on the time axis at their index, each picture one step later than the last, and every picture's spatial grid is normalised to its own area.

Resolutions are multiples of 32. The preset lists 4, 8 and 16 megapixel sizes; 2048×2048 (4,096 image tokens) is the default.

## Memory

On a 32 GB card the int8 DiT (21 GB) stays resident and the nvfp4 text encoder (15.7 GB) is swapped in only when the prompt changes. Expected activations are about 1 GB at 4 MP and 2 GB at 16 MP (estimates, not measured). The bf16 DiT streams part of its blocks from system RAM. Caching all three files takes about 38 GB of system RAM with the int8 DiT. On the int8 DiT a LoRA adds its rank-sized factors on top of that, roughly 24 MB per block at rank 128 (about 1.2 GB for the 50 blocks), placed on the card the way the other native families place theirs; the placement plan counts the DiT file size only, so on a 12 GB card with streaming the factors come out of the headroom.

## Limitations

- **No GPU run has been performed.** Everything above is built and CPU-tested with tiny dimensions and the real key lists; nothing has run with real weights.
- The w4a8 checkpoint does not load.
- **LoRA support is CPU-tested only.** The key mapping and the in-place and runtime application are proven on a tiny model; a LoRA on the real int8 or bf16 DiT, with and without streaming, has not been run.
- Steps, sampler and guidance are fixed; other step counts work through the grid but are untested.
- **The weights are territorially restricted.** MiniMax-H3-Image is published under the MiniMax-H3-Image Community License Agreement, not an open-source license. Its Applicable Territory excludes the European Union, the United Kingdom, the Republic of Korea and the United States of America, and the license does not allow the weights or their outputs to be used outside that territory. Separate, prior written authorization from MiniMax (https://platform.minimax.io/license) is required above US$1 million annualized revenue or for a hosted service offered to third parties, and a commercial product must display "MiniMax-H3-Image" in its interface. Redistribution outside a hosted service needs the NOTICE line "MiniMax-H3-Image is licensed under the MiniMax-H3-Image Community License Agreement, Copyright © 2026 MiniMax. All Rights Reserved." MiniMax claims no rights over outputs. PotionUI ships no weights. The architecture is ported from ComfyUI (GPL-3.0) and diffusers (Apache-2.0), which carry none of these terms.
