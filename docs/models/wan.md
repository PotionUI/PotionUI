---
type: model
title: Wan 2.1 / 2.2
family_key: wan
modes: [video_director]
spec:
  arch: WanModel t2v/i2v backbone (dual-expert high/low-noise pair for 14B checkpoints, single dense model for the 5B ti2v)
  latent: 16-channel (14B variants) or 48-channel (5B ti2v), causal-3D VAE latent, video
  vae: wan21 (16ch, 14B variants) / wan22 (48ch, 5B ti2v)
  te: UMT5 (plus CLIP-vision for the classic image-to-video variant)
  guidance: cfg
  shift: "8.0 (t2v-14B); varies by variant — see Files & detection"
  engine: native
files:
  - role: dit
    dir: models/checkpoints
    note: one file for a single-DiT variant, two files (high/low-noise experts) for a dual-expert 14B pair
  - role: text_encoder
    dir: models/text_encoders
    note: UMT5
  - role: vae
    dir: models/vae
---

# Wan 2.1 / 2.2

The Wan model (`src/platform/runtime/native/arch/wan/model.py`) is the base text-to-video/image-to-video backbone for both Wan 2.1 and 2.2 — the 2.2 14B dual-expert (high/low-noise) split is a weights-and-sampling pairing at the loader/generator level, not a structural difference the model class itself needs to know about. It runs flow-matching with true classifier-free guidance.

## Files & detection

Four checkpoint shapes share the Wan family, disambiguated by input/output channel count and model type: `wan_t2v_14b` (shift 8.0, expert boundary 0.875), `wan_i2v_14b` (CLIP-vision conditioning via a dedicated image-embedding path, expert boundary 0.900), `wan22_i2v_14b` (channel-concatenated reference-frame conditioning instead of CLIP-vision — a distinct local Wan 2.2 i2v checkpoint shape, distinguished purely by input channel count), and `wan_ti2v_5b` (single dense model, no expert boundary, coarser spatial granularity). The text encoder is UMT5. The VAE is a causal-3D autoencoder — the 16-channel shape for 14B checkpoints, the 48-channel shape for the 5B ti2v. A dual-expert 14B pair loads both DiT files plus one shared UMT5/VAE; a single-DiT Wan variant simply leaves the low-noise expert slot empty.

## Presets & modes

The shipped Wan preset's only generation mode is `video`, a Video Director flow covering four sub-modes: plain text-to-video (`t2v`), image-to-video (`i2v`), first-last-frame (`flf`), and `director` — a routed multi-shot chain that composes a long video from a sequence of shots. The standalone `txt2vid` and `img2vid` modes were retired in favor of the Director's `t2v`/`i2v` sub-modes, which route to the same `generator/txt2vid_wan22` / `generator/img2vid_wan22` pipes; the `director` sub-mode runs `generator/chain_video_wan22`. The `director` sub-mode replaced an earlier `chain` sub-mode — the editor and document are unified now, and Wan declares `segment_routing: true` in its `video_director` capabilities, which is what makes the shared Director editor render Wan's multi-shot composer (per-shot duration/LoRAs/sub-type, first-only keyframe, continuity controls) instead of LTX's keyframe/audio timeline. A single-shot `director` document is a degenerate chain of one. See [Video Director](../video-director.md) for the composition contract this preset implements.

### Two checkpoint sets

Wan 2.2 ships architecturally-separate t2v (in_dim 16) and i2v (in_dim 36, 20-channel concat conditioning) checkpoints, so the form carries **two** DiT sets — `t2v_high_noise_model` / `t2v_low_noise_model` and `i2v_high_noise_model` / `i2v_low_noise_model` — plus a shared text encoder and VAE, and per-set base LoRA stacks on the LoRA tab. Each set's loader is enabled only when the submitted document actually contains a segment that needs it (the `needs_t2v_set` / `needs_i2v_set` flags `normalize_video_director` precomputes): a pure-t2v request never loads the i2v pair, a pure-chain-from-an-image request never loads the t2v pair. The 5B TI2V checkpoint (in_dim 48, dense, no CLIP-vision `img_emb`) belongs in the **t2v set only**: its built-in image conditioning is not the 36-channel concat contract `generator/img2vid_wan22` implements, so that generator rejects it before doing any conditioning work — see [Mode support matrix](#mode-support-matrix) below.

### Per-segment sub-type routing

Every segment resolves to a sub-type via `derive_segment_sub_type` (`src/features/video_director/normalize.py`, the single contract the frontend mirrors for its per-segment display): an explicit per-segment `sub_type` override wins; otherwise a segment carrying a start (and optionally end) image is `i2v`/`flf`, the first prompt-only segment is a fresh `t2v` shot, and a later prompt-only segment defaults to `chain` — a continuation of the previous segment's tail frames. This makes a chain heterogeneous: it can open on a fresh `t2v` shot (t2v set, no conditioning) and continue as `chain` (i2v set, conditioned on the previous tail), or take a hard `t2v` cut mid-sequence via the override. `generator/chain_video_wan22` runs the whole sequence, switching between the two sets per segment, and optionally stitches the segments into one continuous video with a **per-join** overlap: each join's dropped-frame count comes only from that segment's own executed sub-type and trim outcome, never from another segment's. A fresh `t2v`/`i2v`/`flf` cut joins at 0 (it shares no conditioning with what came before it), as does a `chain` continuation whose leading context the generator already trimmed (dropping the replayed tail from the decoded clip before it ever reaches disk — see `context_trimmed` below). A `chain` continuation whose window was too short to trim without emptying it still has that replayed tail on disk, so its join crossfades it out once. Every one of those counts is capped not by the configured tail length alone but by **how much tail the previous segment actually emitted** (`context_frames = min(tail_count, previous segment's own frame count)`) — a short opener (e.g. a 1-frame `t2v` shot) leaves less real context than `tail_count` assumes, and the pre-decode trim, the stitch-time join fallback, and the latent seam splice all read this same clamped value so they can't disagree about it. `stitch_segments` itself also refuses (`ValueError`) a join whose overlap would exceed the frames accumulated so far, rather than silently truncating. The generator records `segment_emitted_frames` / `segment_context_trimmed` / `segment_join_overlap` (the per-join overlap it planned) / `segment_overlap_dropped_at_stitch` (what stitching actually applied — equal to the planned values only when stitching ran; all zeros when it was disabled or the run was cancelled first) alongside its other per-segment metadata so a caller can reconcile the executed geometry against what it planned.

### Per-segment LoRAs

Each chain segment carries its own `loras` list (same `{high, low}` shape as the LoRA-tab pickers). The generator patches that stack onto the live experts of whichever set the segment runs on and un-patches back to the set's base experts for a later plain segment. Re-acquisition goes through the shared model-lifecycle cache keyed by **base** checkpoint path + LoRA fingerprint, so the cache never holds a patched-per-combination copy (a re-used stack is a cache hit, not a fresh load).

## Sampling

Default generation parameters (text-to-video): 30 steps, CFG scale 5.0, sampler `unipc`, 832×480 resolution, 81 frames at 16fps. Frame count snaps to `1 + 4k` (the causal VAE's temporal chunking); resolution snaps to 16px granularity for the 14B variants, 32px for the 5B ti2v. Image-to-video shares the same defaults plus an expert-boundary override.

## Techniques

Every knob below is exposed on the preset's **Advanced** tab and threaded into all three Wan generators (`txt2vid`/`img2vid`/`chain`) unless noted; each defaults to its own no-op value, so an untouched form generates exactly as it did before the knob existed. Speed profile, steps, CFG and sampler live on the Generation and Advanced → Sampling groups.

| Technique | What it does | Where | Default | Scope |
| --- | --- | --- | --- | --- |
| Expert switching | Overrides the high→low-noise expert boundary (sigma or step) for 14B dual-expert pairs | Advanced → Expert switching | Model default (t2v σ 0.875 · i2v σ 0.900) | all three |
| [NAG](../techniques/nag.md) | Applies the negative prompt through attention instead of a second CFG pass — keeps a working negative prompt on distilled/turbo runs at cfg 1.0 | Advanced → Negative prompt guidance | scale 1.0 = off | all three |
| CFG-Zero* | Rescales the uncond branch onto cond before extrapolation; optional zero-velocity first N steps (true-CFG path only) | Advanced → CFG-Zero* | rescale on, zero-init 0 | all three |
| [APG](../techniques/apg.md) | Reshapes the CFG delta (eta / norm cap / momentum) to cut oversaturation at high CFG | Advanced → Adaptive Projected Guidance | eta 1.0, norm 0, momentum 0 = off | all three |
| [SLG](../techniques/slg.md) | Pushes the prediction away from a degraded blocks-skipped pass; sigma-windowed | Advanced → Skip-Layer Guidance | scale 0 = off | all three |
| Sigma schedule | Switches the schedule curve family (beta/exponential/linear-quadratic) or supplies a manual descending sigma list (length = step count) | Advanced → Sigma schedule | blank = model shift-based default | all three |
| Detail warp (sigma daemon) | Bumps mid-trajectory sigmas up/down over a start/end trajectory window — a structure/fine-detail lever independent of, and layered on top of, whichever schedule (including manual) built the base sigmas | Advanced → Detail warp | strength 0 = off | all three |
| [FreeInit](../techniques/freeinit.md) | Extra full denoise passes re-noised in 3D frequency space to cut temporal flicker | Advanced → FreeInit | 0 iterations = off | **text-to-video only** (no spec on i2v/chain) |
| [RIFLEx](../techniques/riflex.md) | Clamps an intrinsic RoPE frequency to extrapolate past the trained frame count — for long single shots and director chains | Advanced → Long-video RoPE | off = byte-identical schedule | all three |

`manual_sigmas` (non-empty) overrides the *scheduler pick* only — `linear_quadratic`'s own `threshold_noise`/`linear_steps` fields are silently ignored in favor of the manual list, same as `beta`/`exponential` would be. Detail warp is not part of that precedence: it still applies on top of a manual sigma list.

Not surfaced on the preset:

- **FBCache / step-cache** (TeaCache-style step skipping) is implemented and honored by the Wan forward, but its config is a single opaque dict (`rel_threshold`/`warmup_steps`/`max_consecutive_skips`); the flat preset form has no clean path to it without a `tojson` round-trip the codebase is deliberately retiring. Left internal pending a dedicated structured field.
- **`sampler_options`** (e.g. stochastic-sampler `eta`) is a no-op for the three exposed samplers (`unipc`/`euler`/`dpmpp_2m`), so it stays internal.
- **`beta`/`exponential` schedule options** (`alpha`/`beta`, `sigma_min`) have no preset-level fields of their own — those two families always run at `build_sigmas`'s own defaults (0.6/0.6, 1e-3); only `linear_quadratic` got typed fields, since it needed one to be usable at all beyond its own defaults.
- **Engine-level levers** — sage attention, `NATIVE_TORCH_COMPILE`, `NATIVE_STREAM_PREFETCH`, fp8 quantize-at-load, partial layer residency, NVFP4 — are global backend/env settings (the admin Optimizations panel), not preset-scoped, and apply to Wan runs regardless of this preset.

## Limitations

`wan22_i2v_14b` and `wan_i2v_14b` are structurally different (channel-concatenation vs. CLIP-vision cross-attention) despite both being casually called "Wan i2v" — pointing a preset built for one checkpoint shape at the other's file fails detection outright rather than silently misbehaving, but the two aren't interchangeable.

### Mode support matrix

Which checkpoint works in which Director mode, and what evidence backs each cell. A checkpoint's file size or VRAM footprint says nothing about which modes it supports — the 5B TI2V's small 9.31 GB file does **not** imply it works in i2v/flf/Director; its `in_dim` (48) is simply not the 36-channel concat-i2v contract `generator/img2vid_wan22` implements. The single measured GPU peak on record (see Hardware, 15.2 GB) is for one specific configuration — the A14B **t2v** expert pair (expert boundary 0.875) — and is cited only on that cell; every other cell's "Supported"/"Rejected" call rests on model detection and/or model-free pipeline tests alone, with no GPU run behind it.

Note on the `director` column: `generator/chain_video_wan22` does **not** route through `GeneratorWanImg2VidPipe.build_context` — it holds its own per-set arch checks (t2v set: in_dim 16; i2v set: in_dim 36 + the shared CLIP-vision guard below) and builds its own start/end-frame concat directly, so its Director-column cells reflect only *that* generator's own guards, not the img2vid generator's.

Classic Wan i2v checkpoints (with or without the FLF `emb_pos` table) share `in_dim=36` with the modern concat-i2v contract but condition through a CLIP-vision `img_emb` cross-attention path (`arch/wan/model.py`'s `MLPProj`) that neither Wan generator ever feeds `clip_fea` into. A single shared helper, `src/pipelines/pipes/_shared/generation/wan_i2v_contract.py`'s `require_concat_i2v_contract` (applied to every executing expert via `require_concat_i2v_contract_for_experts`), checks the resolved module's own `img_emb` attribute (populated only for `model_type == "i2v"` checkpoints — channel count alone can't distinguish the two) and is called from both entry points, on **every expert that can execute** — high AND low, so an incompatible low expert beside a compatible high still rejects, since which expert actually runs at a given sampling step is a runtime decision the validation must not have to predict:

- `generator/img2vid_wan22.build_context` — both `bundle.high_dit` and `bundle.low_dit` (skipped when `None`, a single-DiT bundle), before any VAE move/encode/offload or concat build.
- `generator/chain_video_wan22` — a **preflight**, run before ANY segment's work starts, resolves every segment's sub-type up front (`segment_sub_type`, a pure function of the document) to determine exactly which checkpoint set(s) — t2v, i2v, or both — the resolved plan will actually use, and calls `prepare_set` (which validates both `base_high`/`base_low` of that set) for each one immediately. A set the document never needs is never touched, so a loaded-but-unused incompatible checkpoint is harmless; a t2v-opener-then-chain film needing an incompatible i2v set later rejects before the t2v opener itself ever denoises. `prepare_set`'s own per-expert check (idempotent, a no-op once the preflight already built that set's router) remains as the last line of defense for any future call path that skips the preflight.

| Checkpoint | t2v | i2v | flf | Director |
| --- | --- | --- | --- | --- |
| Wan 2.1 t2v (in_dim 16) | Supported — model detection + `test_wan_detect.py`; model-free pipeline validation via `generator/txt2vid_wan22` fixtures; no GPU validation on record | N/A (no image conditioning) | N/A | Supported as a fresh t2v shot — `chain_video_wan22`'s own t2v-set guard requires in_dim 16 (model-free pipeline validation only) |
| Wan 2.1 classic i2v / FLF2V (CLIP-vision `img_emb`, in_dim 36, with or without the FLF `emb_pos` table) | N/A | **Rejected** — `generator/img2vid_wan22.build_context` checks every expert (high and low) via `require_concat_i2v_contract_for_experts` and raises before any VAE/concat work, whether the incompatible module is the high-noise or the low-noise expert (model-free pipeline tests: `test_classic_clip_vision_i2v_raises_before_any_vae_or_concat_work`, `test_compatible_high_with_classic_low_expert_still_raises`); no GPU validation — real-weight behavior remains unverified | **Rejected**, same guards and tests (FLF parametrization) | **Rejected** — `generator/chain_video_wan22`'s preflight resolves the segment plan and calls `prepare_set` on every set it will use BEFORE any segment runs, checking every expert of the i2v set; an incompatible set needed only by a later continuation still rejects before an earlier compatible segment denoises, and a loaded-but-unused incompatible set is left untouched (model-free pipeline tests: `test_i2v_set_classic_i2v_without_flf_raises_before_any_work`, `test_i2v_set_classic_flf2v_raises_before_any_work`, `test_i2v_set_incompatible_low_expert_raises_even_with_compatible_high`, `test_chain_continuation_needing_incompatible_i2v_set_raises_before_any_segment_work`, `test_loaded_but_unused_incompatible_set_stays_harmless`); no GPU validation — real-weight behavior remains unverified |
| Wan 2.2 A14B t2v (in_dim 16, dual-expert) | Supported — model detection + `test_wan_detect.py`; model-free pipeline validation; **GPU-validated**, 15.2 GB peak measured for this exact configuration (see Hardware) | N/A | N/A | Supported as a fresh t2v shot — `chain_video_wan22`'s own t2v-set guard requires in_dim 16 (model-free pipeline validation only; the measured t2v peak above was through `txt2vid_wan22`, not this generator) |
| Wan 2.2 A14B i2v (in_dim 36, dual-expert) | Rejected — `generator/txt2vid_wan22` rejects in_dim 36 (model-free pipeline validation) | Supported — model detection + `test_wan_detect.py`; model-free pipeline validation only (`test_wan_i2v_generator.py`'s 36ch concat wiring tests); no separately measured GPU peak for this concat-i2v path | Supported, same generator/tests, model-free validation only | Supported as an i2v/chain shot — `chain_video_wan22`'s own i2v-set guard requires in_dim 36 (model-free pipeline validation only) |
| Wan 2.2 TI2V-5B (in_dim 48/out_dim 48, dense, no img_emb) | Supported — model detection (`test_detect_5b_by_out_dim`) + model-free pipeline validation (`test_ti2v_5b_model_in_txt2vid_mode_proceeds`); no separately measured GPU peak | **Rejected** — `generator/img2vid_wan22` raises before any VAE/concat work (model-free pipeline test); no GPU validation needed to reach this conclusion | **Rejected**, same guard | **Rejected** — an i2v/chain shot needs the i2v set (in_dim 36); `chain_video_wan22`'s own i2v-set guard rejects an in_dim-48 checkpoint there (model-free pipeline validation only) |

## Hardware

The 14B dual-expert pair keeps one **14B expert resident at a time** (`src/platform/runtime/model_lifecycle/memory_policy.py`'s `wan22 = 14.0` cost estimate) — two DiT files on disk, but only one loaded into VRAM per phase. **Measured peak (32 GB-card ceiling):** 15.2 GB for a short, low-resolution fp8 clip (33 frames, 832×480, 15 unipc steps) — a longer or higher-resolution clip will peak higher. 14B is **not realistic below 16 GB**; the **5B ti2v variant** (single dense model, no expert boundary, coarser spatial granularity) is the small-card option but has no separately measured peak yet. Video generation is also RAM-heavier than a still image: a dual-expert pair means two multi-GB checkpoints pass through the host-RAM load path even though only one sits in VRAM at once, so the RAM-cache floor (see [Hardware Requirements](../user/hardware-requirements.md)) matters more here than for an image family.
