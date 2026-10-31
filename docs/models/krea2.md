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

The shipped Krea-2 preset ships `txt2img`, `enhance`, `upscale` and `edit`.

### Upscale

`upscale` enlarges an existing picture 2x, 3x or 4x and rebuilds its detail with Krea-2. It runs the family-agnostic `tiled_refiner` pipe: the picture is enlarged (an optional ESRGAN upscaler model, otherwise Lanczos), then each overlapping tile is refined by a low-denoise img2img pass and the seams are feathered. Peak memory is set by one tile (1024 px by default), not by the output size, so a 4x result refines in the same memory as a 1x one.

`enhance` is the other way to refine an existing picture and is not replaced. It re-noises the whole enlarged frame and walks only the last 1 to 3 steps of Krea-2's fixed-mu 8-step schedule, which keeps the picture globally coherent but needs the whole frame resident and is marked experimental. Use `upscale` for large outputs, for low memory or when an ESRGAN model should do the enlargement; use `enhance` for a light single-pass refine at up to 2x.

- **Added detail** is the per-tile denoise: Subtle 0.2, Balanced 0.3, Strong 0.45. **Calm flat areas** (content-aware) scales each tile's denoise by its texture so sky and skin cannot grow details the prompt does not describe.
- The prompt is optional. Describing the picture keeps the added detail on target.
- Turbo defaults: 8 steps, CFG 1, Euler. Tile size and overlap are in the Advanced tab.

Not validated on GPU: the tail of Krea-2's distilled schedule under `tiled_refiner`'s denoise-truncated img2img is untested against real weights.

### Edit

`edit` changes a picture from a text instruction. The source picture is VAE-encoded and prepended to the target sequence as clean in-context tokens (RoPE frame 1, a second picture at frame 2), so the model reads it through a path distinct from img2img. It needs an edit LoRA trained for that sequence; a style or character LoRA does not activate editing. Generator: `generator/krea2_edit` (`src/pipelines/pipes/generator/krea2_edit/`), derived from comfyui-krea2edit by lbouaraba (Apache-2.0).

| Task | Edit LoRA | Steps | CFG | Reference fidelity | Reference placement |
| --- | --- | --- | --- | --- | --- |
| Edit the picture (remove, recolor, relight, restyle) | General Edit v2 | 8 (turbo) | 1 | 4 (auto) | pinned |
| Remove or delete large areas | General Edit v2 on the raw checkpoint | about 20 | 3 | 4 | pinned |
| Place a person | Identity Edit v2 | 8 (turbo) | 1 | 2 (auto) | beside |

- **Reference fidelity** (`ref_boost`) multiplies target-to-reference attention for the last picture; the first picture takes **Scene fidelity** (`ref_boost_a`). 0 means automatic from the Task. A painted Inpaint mask on the picture the dial targets restricts the boost to that region (a face, say). The mask is resampled to its picture's size, then fitted exactly as the picture is, so it stays aligned through a Fit or Crop. `fit_ref_mask` and `fit_source_image` in `generator/krea2_edit/geometry.py` are the reusable pieces.
- **Reference placement** (`ref_position`, `ref_position_a`) sets where each picture sits in RoPE space. Pinned (the trained layout) keeps the picture's layout; Beside shifts it right of the canvas so identity flows but the layout pin is gone, which restages a person more freely. Scene pinned with the person beside gives the most natural composites. The v2 LoRAs were trained pinned only; Beside is new. The implementation is `arch/krea2/placement.py`.
- **Read the picture** loads the text encoder's Qwen3-VL vision tower so the instruction is encoded together with the picture ("the man on the left" resolves). It is on by default because the v2 LoRAs were trained with this dual conditioning. The recommended `qwen3vl_4b_fp8_scaled` encoder ships the tower. **Reading detail** (`grounding_px`, default 768) caps the longest side the encoder sees.
- **Source fit** `fit` resamples the whole picture into the frame; `crop` centre-crops to the output aspect ratio and is for v1 weights.
- Generate at 2 megapixels or less.
- Use one edit LoRA per task. Both v2 LoRAs at 1.0 together break the image; about 0.6 each is the author's combination.

Licences, checked on the model card: the v2 LoRAs ship under the Krea 2 Community License plus the author's Identity Edit V2 licence, free for non-commercial use, with commercial use requiring a licence from the author (and an enterprise licence from Krea above $1M revenue). The v1.2 LoRA is under the Krea 2 Community License only.

Not validated on GPU: Beside and the fidelity defaults are taken from the author's recommended-settings tables, and the CFG above 1 removal path grounds the negative prompt through the shared prompt encoder.

## Sampling

`txt2img`'s speed profile drives steps, CFG, and the sigma-schedule mu source together (`content/presets/marketplace/Krea2/preset.yml`):

| Profile | Steps | Sampler | CFG | mu schedule | Checkpoint |
| --- | --- | --- | --- | --- | --- |
| Turbo | 8 | Euler | 1.0 (off) | fixed mu=1.15 | distilled turbo |
| Balanced | 14 | Euler | 1.0 (off) | fixed mu=1.15 | distilled turbo |
| Quality | 25 | DPM++ 2M | 4.0 | fixed mu=1.15 | distilled turbo |
| Base | 52 | Euler | 4.0 | resolution-anchored dynamic mu | raw / non-distilled |

Turbo/Balanced stay CFG-off (`cfg=1.0`, single forward) — byte-identical to the preset's pre-CFG behavior. Quality turns on real CFG as an experiment on the still-distilled checkpoint, keeping the official fixed-mu=1.15 schedule. Base is for a raw (non-distilled) Krea-2 checkpoint: real CFG plus the resolution-anchored mu interpolation upstream (`krea-ai/krea-2` `sampling.py`) documents for the un-distilled/midtrain schedule (`x1_px=256/x2_px=1280/y1=0.5/y2=1.15/align=16`, wired through `generator/krea2`'s `mu_schedule` config knob and `engine._sampling_settings_for`'s `fixed_mu`/`dynamic_shift` whitelist). A "CFG Scale" advanced field lets any profile's value be overridden manually.

**Enhance (`refine_tail`).** Enhance walks the tail of the official fixed-mu 8-step grid: Subtle is 1 step (sigma 0.311 to 0), Balanced 2 steps (0.513, 0.311, 0) and Strong 3 steps (0.655, 0.513, 0.311, 0). Because those sigmas are the distilled checkpoint's training points, the tail always runs cfg 1.0 and `euler`, whatever the CFG Scale field or the speed profile say (the standalone `enhance` mode has no speed profile and is always treated as distilled). On the Base profile the inline Enhance instead builds the tail from a 24-step resolution-anchored dynamic-mu schedule for the actual upscaled size, starting at the grid sigma closest to the turbo tail's start and never shorter than 2 steps, and keeps the profile's CFG; it raises an error when that schedule would re-noise the image almost completely (about 3072 px and up, so 4x). Subtle is not offered at 4x: the field falls back to Balanced. Running Euler at CFG 1 over the grid's own sigmas is what clears leftover noise: Subtle, Balanced and Strong finish clean in 1 to 3 steps.

The Advanced tab's **Sampler** and **Schedule** pickers (`type: "sampler"` / `type: "schedule"`, family `krea2`) share one row and are registry-driven: they list whatever this instance has registered — the core samplers and schedules plus any a plugin contributes — rather than a list written into the preset. See [Samplers and sigma schedules](../techniques/samplers-and-schedules.md). Schedule's default, `shift`, is the shift-based mu selection the table above describes; every other schedule bypasses the mu/shift math outright — `beta` and friends ignore `mu_schedule`/`fixed_mu`/`dynamic_shift` entirely, so pairing one with the Base profile has no extra effect beyond Base's own CFG change. Civitai users porting a recipe typically pair `er_sde` + Beta for photographic skin and fabric texture at the cost of losing the turbo checkpoint's tuned schedule; `euler` + `shift` stays the fastest, most-tested combination and is the right default outside a specific recipe.


**NAG vs CFG**: `nag_scale`/`nag_tau`/`nag_alpha` (Normalized Attention Guidance) exist to give the negative prompt influence at `cfg=1.0`, when no negative-conditioned forward runs. Real CFG (`cfg>1.0`) already runs that forward pass; stacking NAG on top of it is unvalidated and likely redundant. The Turbo/Balanced-only NAG toggle resets to off when the form's speed profile switches to Quality or Base.

**Text Enhancer and Text Scale (experimental).** The Enhance tab's Text Enhancer toggle makes Krea-2 follow the prompt more closely: it amplifies the deeper text-encoder layers and blends that change in, capped per token so no word's influence jumps too far. Strength 1.0 is the reference setting and 0 is off. Text Scale (Advanced, under Text conditioning) multiplies the whole text signal; 1.0 is neutral and 1.5 to 2 gives the prompt more influence. Both are off by default and leave the output unchanged when off. They apply to `txt2img` and its inline Enhance pass, work with CFG and NAG, and cost one extra text-processing pass per run, not per step.

**LoRA dialect**: Krea-2 recognizes both the kohya-underscore spelling and a bare-dotted spelling of LoRA keys, so LoRAs published in either convention load without a manual rename.

## Limitations

Base-profile (raw/non-distilled checkpoint) output is unvalidated — the 52-step / cfg=4.0 / dynamic-mu combination has not been run against real base weights on GPU. Quality profile's cfg>1.0-on-the-distilled-checkpoint combination is likewise an untested experiment, not a documented upstream recipe. The Text Enhancer and Text Scale are likewise unvalidated against real weights on GPU.

## Hardware

The Krea-2 DiT (~12B) is **26 GB on disk in bf16**; on-the-fly fp8 quantize-at-load brings that down to **~12.5 GB** (`docs/native-engine.md`, `memory/tiering.py`'s `fp8_quantize` knob, `auto` by default). **Measured peak (32 GB-card ceiling):** 26 GB in the streaming manual_cast tier at 1024², 8-step turbo. That fp8 path is what makes Krea-2 **viable on a 12 GB card** — the best fp8 story of the native families, since the design doc calls out this exact model as the reference example for the quantize-at-load feature. Below 12 GB, expect partial residency (a handful of resident blocks, the rest streamed) rather than a hard failure. Bf16 needs 16 GB+.
