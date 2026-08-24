---
type: model
title: Anima
family_key: anima
modes: [txt2img, img2img]
spec:
  arch: Cosmos-Predict2 MiniTrainDIT backbone with an in-model LLMAdapter
  latent: 16-channel, Wan21-format causal-3D VAE latent (image-only, single frame)
  vae: qwen_image (Wan 2.1 causal-3D VAE, shared with Qwen-Image and Krea-2)
  te: Qwen3-0.6B (plus in-model T5 token fusion)
  guidance: cfg
  shift: 3.0
  engine: native
files:
  - role: dit
    dir: models/checkpoints
  - role: text_encoder
    dir: models/clip
    note: Qwen3-0.6B
  - role: vae
    dir: models/vae
---

# Anima

Anima (`src/platform/runtime/native/arch/anima/model.py`) is built on Cosmos-Predict2's MiniTrainDIT backbone with an in-model LLMAdapter: the text encoder (Qwen3-0.6B) produces a hidden state that the adapter fuses **inside the DiT** with T5 token ids/weights passed through the conditioning dict, rather than the more usual "encode once outside, feed a fixed embedding in" split every other family uses. It runs flow-matching with true classifier-free guidance.

## Files & detection

The text encoder is Qwen3-0.6B — small, which is why Anima's memory footprint is lower than every other family's. The VAE is the same Wan 2.1 causal-3D VAE that Qwen-Image and Krea-2 use, despite Anima being a text-to-image-only model, not video.

## Presets & modes

The shipped Anima preset ships `txt2img`.

## Sampling

Default generation parameters: 24 steps, guidance 6.0 (true CFG scale), sampler `euler`, img2img strength 0.55.

## Limitations

The T5 token ids/weights the LLMAdapter needs are supplied by the generator pipe alongside the Qwen3-0.6B hidden state — a custom prompt-encoding path that swaps in a different flow without also producing those T5 ids will silently lose part of Anima's conditioning rather than raising an error, so verify both halves of the conditioning dict are populated when customizing prompt encoding for this family.

## Hardware

**Measured peak (32 GB-card ceiling):** 20.7 GB at 1024², 24 steps, cfg 6.0 (`anima_aestheticV10b` bf16 DiT + the tiny Qwen3-0.6B text encoder + the shared Wan 2.1 causal-3D VAE — the peak lands during the VAE's fp32 causal-3D decode, `docs/native-engine.md`). Anima's own text encoder is the smallest of any native family, but the DiT and VAE decode spike still push the total close to a 24 GB card's ceiling, making 12 GB not viable and **16 GB marginal at best**. 24 GB is the realistic recommendation.
