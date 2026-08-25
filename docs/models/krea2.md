---
type: model
title: Krea-2
family_key: krea2
modes: [txt2img, enhance]
spec:
  arch: Krea2 distilled turbo MMDiT (forked from diffusers' transformer_krea2, Apache-2.0)
  latent: 16-channel, Wan21-format causal-3D VAE latent (image-only, single frame)
  vae: qwen_image (Wan 2.1 causal-3D VAE, shared with Qwen-Image and Anima)
  te: Qwen3-VL-4B
  guidance: "true CFG; turbo defaults to cfg=1 (single forward, byte-identical to the old NoCFG path), base/raw and Quality run cfg>1"
  shift: "fixed mu=1.15 (turbo/distilled default); resolution-dynamic mu interpolation opt-in via the Base speed profile"
  engine: native
files:
  - role: dit
    dir: models/checkpoints
  - role: text_encoder
    dir: models/text_encoders
    note: Qwen3-VL-4B
  - role: vae
    dir: models/vae
    note: shared Wan 2.1 causal-3D VAE
---

# Krea-2

Krea-2 (`src/platform/runtime/native/arch/krea2/model.py`) is a distilled turbo image DiT, detected as its own family. It runs flow-matching with true CFG (`sampling_settings.guidance = "cfg"`): the turbo checkpoint drives `cfg_scale=1.0`, which makes `TrueCFG` collapse to a single conditional-only forward (see `src/platform/runtime/native/sampling/cfg.py`'s `abs(scale - 1.0) < 1e-6` short-circuit) — byte-identical in output to the old NoCFG strategy. A raw/base (non-distilled) checkpoint, or an experiment on the distilled checkpoint, sets `cfg_scale > 1.0` to get a real negative-conditioned pass.

One `ModelSpec` covers both regimes: turbo and a raw/base checkpoint share the same architecture signature, so there's nothing in the state dict to detect the difference. The **speed profile** picks the regime instead — see Sampling below.

## Files & detection

The text encoder is Qwen3-VL-4B. The VAE is the Wan 2.1 causal-3D VAE verbatim — Krea-2 is an image model but reuses the video VAE's latent format (per-channel mean/std, not a scalar scale factor). Checkpoint layout is mixed-dtype: bf16 block/txtfusion linears with fp32 norms and peripheral weights. Krea-2 shares its model-loader pipe with Flux.

## Presets & modes

The shipped Krea-2 preset ships `txt2img` and `enhance`.

## Sampling

`txt2img`'s speed profile drives steps, CFG, and the sigma-schedule mu source together (`content/presets/marketplace/Krea2/preset.yml`):

| Profile | Steps | Sampler | CFG | mu schedule | Checkpoint |
| --- | --- | --- | --- | --- | --- |
| Turbo | 8 | Euler | 1.0 (off) | fixed mu=1.15 | distilled turbo |
| Balanced | 14 | Euler | 1.0 (off) | fixed mu=1.15 | distilled turbo |
| Quality | 25 | DPM++ 2M | 4.0 | fixed mu=1.15 | distilled turbo |
| Base | 52 | Euler | 4.0 | resolution-anchored dynamic mu | raw / non-distilled |

Turbo/Balanced stay CFG-off (`cfg=1.0`, single forward) — byte-identical to the preset's pre-CFG behavior. Quality turns on real CFG as an experiment on the still-distilled checkpoint, keeping the official fixed-mu=1.15 schedule. Base is for a raw (non-distilled) Krea-2 checkpoint: real CFG plus the resolution-anchored mu interpolation upstream (`krea-ai/krea-2` `sampling.py`) documents for the un-distilled/midtrain schedule (`x1_px=256/x2_px=1280/y1=0.5/y2=1.15/align=16`, wired through `generator/krea2`'s `mu_schedule` config knob and `engine._sampling_settings_for`'s `fixed_mu`/`dynamic_shift` whitelist). A "CFG Scale" advanced field lets any profile's value be overridden manually. The `enhance` mode has no speed profile; its own standalone "CFG Scale" field defaults to 1.0 (off) and should track whatever regime the base generation used.


**NAG vs CFG**: `nag_scale`/`nag_tau`/`nag_alpha` (Normalized Attention Guidance) exist to give the negative prompt influence at `cfg=1.0`, when no negative-conditioned forward runs. Real CFG (`cfg>1.0`) already runs that forward pass; stacking NAG on top of it is unvalidated and likely redundant. The Turbo/Balanced-only NAG toggle resets to off when the form's speed profile switches to Quality or Base.

**LoRA dialect**: Krea-2 recognizes both the kohya-underscore spelling and a bare-dotted spelling of LoRA keys, so LoRAs published in either convention load without a manual rename.

## Limitations

Base-profile (raw/non-distilled checkpoint) output is unvalidated — the 52-step / cfg=4.0 / dynamic-mu combination has not been run against real base weights on GPU. Quality profile's cfg>1.0-on-the-distilled-checkpoint combination is likewise an untested experiment, not a documented upstream recipe.

## Hardware

The Krea-2 DiT (~12B) is **26 GB on disk in bf16**; on-the-fly fp8 quantize-at-load brings that down to **~12.5 GB** (`docs/native-engine.md`, `memory/tiering.py`'s `fp8_quantize` knob, `auto` by default). **Measured peak (32 GB-card ceiling):** 26 GB in the streaming manual_cast tier at 1024², 8-step turbo. That fp8 path is what makes Krea-2 **viable on a 12 GB card** — the best fp8 story of the native families, since the design doc calls out this exact model as the reference example for the quantize-at-load feature. Below 12 GB, expect partial residency (a handful of resident blocks, the rest streamed) rather than a hard failure. Bf16 needs 16 GB+.
