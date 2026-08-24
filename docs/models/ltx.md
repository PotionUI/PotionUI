---
type: model
title: LTX-2 / 2.3 / 2.5
family_key: ltx
modes: [video_director]
spec:
  arch: LTXAVModel — dual parallel token streams (video and audio) with cross-modal attention
  latent: video via a causal-3D VAE; audio via a causal audio VAE and vocoder
  vae: ltx_causal_video (+ ltx_diffusion_video, 2.5) + ltx_audio
  te: Gemma3-12B (ltxav, 2.0/2.3) or Gemma4-12B-with-proj (ltxav, 2.5) or T5-XXL (ltxv, legacy 0.9 shape)
  guidance: cfg
  shift: "exp(2.05)≈7.768 (ltxav) / 2.37 (ltxv legacy)"
  engine: native
files:
  - role: dit
    dir: models/checkpoints
    note: an all-in-one safetensors file bundling the DiT, video VAE, audio VAE, vocoder, and text-embedding projection (2.0/2.3); LTX-2.5 ships the DiT alone, transformer-only
  - role: text_encoder
    dir: models/text_encoders
    note: Gemma3-12B (2.0/2.3) or Gemma4-12B-with-proj (2.5, also carries the text-embedding projection) — or T5-XXL (legacy ltxv)
  - role: vae
    dir: models/vae
    note: LTX-2.5 split layout only — standalone video-VAE (conv- or diffusion-decode, see below) and/or audio-VAE(+vocoder) files; 2.0/2.3 slice both out of the all-in-one DiT checkpoint
  - role: upscaler
    dir: models/upscalers
    note: optional LTX-2.3/2.5 latent upscalers — spatial (x1.5/x2) and/or LTX-2.5's temporal x2
---

# LTX-2 / 2.3 / 2.5

LTX (`src/platform/runtime/native/arch/ltx/model.py`) is an audio-video DiT: two parallel token streams (video and audio) with per-block self-attention on each stream, plus cross-modal attention between them and text cross-attention on both. It runs flow-matching with true classifier-free guidance — there is no embedded-guidance path.

## Files & detection

Two checkpoint shapes share the LTX family: `ltxav` (2.0, 2.3, and 2.5 all detect into this shape — Gemma3-12B or Gemma4-12B text encoder plus a connector chain) and `ltxv` (the older LTXV-0.9 shape, T5-XXL text encoder, a different sigma shift). 2.0 and 2.3 ship every component — DiT, video VAE, audio VAE, vocoder, text-embedding projection — in one all-in-one safetensors file; the model loader still splits acquisition by component so a shared text encoder or VAE can be reused across LTX presets. LTX-2.5 switches to a split layout instead (see below): the `model` file carries the DiT only, and the video VAE, audio VAE(+vocoder), and text-embedding projection each move to their own standalone files. The 2.3 checkpoint shape adds gated attention and a sigma-driven prompt-conditioning modulation that the earlier 19B shape doesn't have; 2.5 keeps both and adds a few new, shape-sniffed structural flags (`use_prompt_adaln_single`, `ff_bias`/`audio_ff_bias`, `use_keyframes_abs_pos_embedding`) that default to matching every earlier checkpoint's shape, so 2.0/2.3 detection is unaffected. Every 2.5 checkpoint also embeds its own `config["transformer"]` metadata JSON (earlier checkpoints don't); when present it takes priority over shape-sniffing for the fields it declares. The checkpoint's own `model_version` string (e.g. `2.5`) is parsed to an int tuple and surfaced as `bundle.model_version` for the sampling layer to branch on — `None` for checkpoints that predate the field. All three generations load through the same `LTXAVModel` class, which branches internally on which shape it's looking at.

### Split-file layout (LTX-2.5)

| Component | 2.0 / 2.3 | 2.5 | Pipe config | model_type → dir |
| --- | --- | --- | --- | --- |
| DiT | sliced from the all-in-one `model` file | `ltx-2.5-22b-{dev,distilled}-transformer-bf16.safetensors` (transformer-only) | `model` | `checkpoint` → `models/checkpoints` |
| Text encoder + projection | Gemma3-12B; `text_embedding_projection` embedded in the DiT file | `gemma4-12b-with-proj-ltx-2.5-bf16.safetensors` (Gemma4-Unified-12B; carries `text_embedding_projection`, relocated off the DiT) | `text_encoder` | `text_encoder` → `models/text_encoders` |
| Video VAE | sliced `vae.*` from the all-in-one file | `ltx-2.5-video-vae-conv-bf16.safetensors` (conv decode) or `ltx-2.5-video-vae-bf16.safetensors` (diffusion decode — see below) | `vae` | `vae` → `models/vae` |
| Audio VAE + vocoder | sliced `audio_vae.*`/`vocoder.*` from the all-in-one file | `ltx-2.5-audio-vae-bf16.safetensors` | `audio_model` | `vae` → `models/vae` |
| Spatial upscaler | — (2.3+ optional) | `ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors` | `upscale_model` | `upscaler` → `models/upscalers` |
| Temporal upscaler | — (2.5 optional) | `ltx-2.5-latent-temporal-upscaler-x2-bf16-1.0.safetensors` | `temporal_upscale_model` | `upscaler` → `models/upscalers`; picked by the LTX-2.5 preset's Temporal Upscaler field (Enhance tab), required for Motion smoothing |
| Duration head | — (2.5 optional) | `ltx-2.5-duration-head-bf16.safetensors` (published under `model_patches/`) | `duration_head` | no preset picker yet |
| LoRA | — | `loras/ltx-2.5-22b-distilled-lora-450-bf16.safetensors` | `loras` | `lora` → `models/loras` |

`model_loader/ltx`'s `vae`/`audio_model` config slots cover both shapes: left unset, they default to slicing the component out of the all-in-one `model` file (2.0/2.3); set, they point at the standalone 2.5 file. A pre-flight check (`_require_embedded_component`, `src/pipelines/pipes/model_loader/ltx/main.py`) raises a crisp error up front — instead of failing deep inside VAE construction — when a transformer-only 2.5 `model` file is used with `vae`/`audio_model` left unset. `load_projection` (`projection.py`) tries the DiT file first, then falls back to the text-encoder file, so a 2.5 Gemma4 TE is probed automatically once the DiT alone doesn't carry the projection.

The split layout is why 2.5 gets its **own preset** rather than another branch inside `LTX-2 / 2.3` — see [Presets & modes](#presets--modes) below. `content/presets/marketplace/LTX-2.5` puts all four file pickers on a dedicated Models tab and wires them to `model_loader/ltx`'s `model`/`text_encoder`/`vae`/`audio_model` config slots. `video_vae` is a **required** field in both of that preset's modes (a transformer-only DiT has no embedded `vae.*` to slice, so there is nothing to fall back to); `audio_vae` is optional and lives on the `video` mode only, since the `upscale` mode never generates audio and the pre-flight `_require_embedded_component` check only fires when audio is actually requested.

### Text encoders

2.0/2.3 checkpoints pair with Gemma3-12B. 2.5 checkpoints pair with Gemma4-12B (Gemma4-Unified-12B, ported from transformers' `Gemma4Unified*` modeling code — `src/platform/runtime/native/text_encoders/gemma4.py`); the loader accepts it as a drop-in TE alongside Gemma3-12B (`detect/te_detect.py`), detected ahead of the Gemma3 branch since a flat Gemma4 file reuses Gemma3's `model.*` key names — the discriminator is Gemma4's extra `layer_scalar` key. The legacy `ltxv` (LTXV-0.9) shape pairs with T5-XXL.

### Video VAE: conv vs. diffusion decode

2.5's video VAE ships in two decode variants, distinguished by checkpoint
metadata and mutually exclusive detectors in `detect/vae_detect.py`:
`ltx-2.5-video-vae-conv-bf16.safetensors` matches `detect_ltx_video_vae_config`
(`CausalVideoAutoencoder` — the same conv decoder as 2.0/2.3), and
`ltx-2.5-video-vae-bf16.safetensors` matches `detect_ltx_diffusion_vae_config`
(`config["vae"]["_class_name"] == "CausalDiffusionVAE"`, `model_version:
"2.5.0"`). Both route through `_load_vae_module` (`engine.py`) into the same
`vae` config slot above, so the LTX pipes don't branch on which one loaded.

The diffusion-decoder VAE (`LTXDiffusionVideoVAE`,
`vae/ltx_diffusion_video.py`, loaded by `load_ltx_diffusion_video_vae` in
`vae/loader.py`) reuses the 2.3 causal-conv encoder unchanged — it composes
`ltx_causal_video.Encoder` directly — and only replaces the decoder. That
decoder (`NADiffusionDecoder`) is itself a small diffusion model: four
deterministic stages (`det_stages` + `upsamples`) upsample the latent into a
context volume via 3D neighborhood attention, then a fifth stage
(`diff_blocks`) denoises patchified pixels from noise, conditioned on that
context via AdaLN-Zero scale/shift. Decode runs `N = default_num_inference_steps`
steps (**1** in this checkpoint) at timesteps `linspace(1.0, 1/N, N)`, scaled
by `timestep_scale_multiplier` (1000.0) before the embedder, with
`model_output_type = "x0"`: at N=1 the stage-5 prediction from pure noise IS
the decoded output, no Euler update; at N>1 each x0 prediction converts to a
velocity `(x_t - x0)/sigma` and integrates `x_t <- x_t - dt*v`. Because the
decoder samples noise, decode is stochastic — `decode(latent, generator=...)`
accepts a generator for reproducibility. `enable_tiling()` (off by default,
matching diffusers) runs the last deterministic stage and the stage-5 blocks
on overlapping tiles with linearly blended seams; earlier stages always see
the full latent. Latent space (128 channels, 32x spatial / 8x temporal
compression, `per_channel_statistics` normalization) and the public interface
(`encode`/`decode`/`tiled_encode`/`reset_cache`) are unchanged from the conv
VAE, so latents are interchangeable between the two decoders.

Because stage 5 runs over the full pixel-token grid with no internal chunking
(unlike the conv decoder, which self-chunks by a fixed memory budget), the
generator pipe routes its decode through `_shared/vae/ltx_tiled_decode.py`'s
`decode_with_oom_retry`: it projects the decode's activation cost from the
latent shape and the module's own widths, decodes whole-clip when that fits,
and on a CUDA OOM evicts foreign GPU-resident components, retries once, then
falls back to a tiled decode whose tile size is sized to the same VRAM budget
(overridable with `NATIVE_LTX_DIFFUSION_TILE_PX` / `NATIVE_LTX_DIFFUSION_TILE_FRAMES`).

## Presets & modes

The family ships **two** presets, split by checkpoint layout — pick the one that matches the files you have:

| Preset | Checkpoints | File pickers |
| --- | --- | --- |
| `LTX-2 / 2.3` (`content/presets/marketplace/LTX-2`) | 2.0 / 2.3 all-in-one (`ltx-2-19b-dev`, `ltx-2.3-22b-dev`) | `model`, `text_encoder`, optional `upscale_model` — the VAEs are sliced out of `model` |
| `LTX-2.5` (`content/presets/marketplace/LTX-2.5`) | 2.5 split set | `model` (transformer-only), `text_encoder` (Gemma4 + projection), `video_vae` (required), `audio_vae`, `upscale_model` |

Pointing the 2.0/2.3 preset at a transformer-only 2.5 DiT is rejected up front by `_require_embedded_component` (that preset leaves `vae`/`audio_model` unset, so there is nothing to slice) rather than failing deep inside VAE construction. The reverse — an all-in-one file in the 2.5 preset — is not guarded, because its required `video_vae` picker satisfies the pre-flight check; it just loads a mismatched component set, so pick the preset that matches your files.

Both presets carry the same two modes — a `video`/Director mode (first-frame/last-frame/arbitrary-keyframe conditioning, IC-LoRA reference videos, an optional jointly-generated or muxed-in audio track, and an in-flow two-stage latent upscale) and a standalone `upscale` mode that refines an existing clip. See [Video Director](../video-director.md) for the keyframe/IC-LoRA/audio composition contract. What genuinely differs between them, beyond the pickers, is the stage-1 sampler and schedule — see [Sampling](#sampling).

### Latent upscalers: spatial and temporal

`latent_upscaler/ltx` runs one upsample in latent space, between two generation stages. Its `mode` config picks which checkpoint it runs, and each has its own model-loader slot, because a pipeline can need both files resident at once:

| `mode` | Loader slot | Checkpoint | Effect |
| --- | --- | --- | --- |
| `spatial` (default) | `upscale_model` | `ltx-2.3-spatial-upscaler-x{1.5,2}-*` / `ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors` | H/W × 1.5 or × 2; frame count unchanged |
| `temporal` | `temporal_upscale_model` | `ltx-2.5-latent-temporal-upscaler-x2-bf16-1.0.safetensors` | Frames `T → 2T − 1`; H/W unchanged |

The temporal mapping is not `2T`: the arch pixel-shuffles the frame axis by 2 and then **drops the first frame**, which is what keeps the causal VAE's `1 + 8k` lattice intact across a round. The same expression holds for latent and pixel frame counts (`geometry.temporal_upsample_out_frames`). Nothing downstream needs to be told the count changed — `generator/video_ltx` derives a stage-2 refine's frame count from the latent it is handed. Note that at an unchanged playback fps a temporal round **doubles the clip's duration**; a preset that wants the same duration at double the frame rate has to double its own fps.

`mode` is cross-checked against the checkpoint actually loaded into the slot (both checkpoints share a shape and a `_class_name`, and only the embedded config's `temporal_upsample`/`spatial_upsample` flags tell them apart). A crossed slot is a hard error before any GPU work, not a silently halved clip.

The temporal path has no local checkpoint yet, so it is verified against the diffusers and Lightricks references and synthetic configs rather than a real file; the spatial path is what runs in production today.

### DFR (Diffusion Fidelity Rendering) — temporal rounds

DFR is LTX-2.5's multi-phase rendering scheme; **increment 1 of it is implemented**, and it is a
separate feature from Multishot (the two are often listed together and should not be). What ships is
its *phase C*: temporal densification rounds, exposed as the **Motion smoothing** select on the
LTX-2.5 preset's Enhance tab (`off` / 1 round / 2 rounds) and implemented by
`generator/dfr_video_ltx`.

Each round doubles the frame count and the playback frame rate, so a clip's duration is unchanged and
its motion gets smoother — 24 fps becomes 48 at one round, 96 at two. One round is:

1. temporal-upsample the video latent (`T → 2T − 1`, the `temporal` upscaler above);
2. cut the new timeline into tiles that meet at shared keyframe **seams**, one tile per
   `2**round` by default;
3. re-denoise each tile independently from a partially-noised start (the distilled schedule's
   `0.975, 0.909375, 0.725, 0.421875, 0.0` tail, ancestral Euler at eta 0.5), with the carried
   keyframes pinned as conditioning anchors at strength 0.95;
4. concatenate the tiles back along the temporal axis — a hard latent concatenation of disjoint
   ranges, with **no** overlap blending.

Every tile but the first reaches one segment backwards past its shared seam, and that lead-in is
discarded **plus one more latent frame** (the seam latent, which the previous tile owns). Getting
that `+1` wrong in either direction still decodes: too few dropped latents duplicates an 8-frame span
at every seam and reads as a stutter, too many reads as a skip. The whole canvas/tile/stitch
arithmetic therefore lives in one pure-integer module, `_shared/generation/dfr_layout.py`, pinned by
unit tests against the specification's worked tables, and the pipe asserts the stitched extent
against the layout's prediction after every round.

Three frame rates coexist and are not interchangeable: the **base** fps (what the form asked for),
the **conditioning** fps `min(60, base × 2**round)` handed to the DiT as its RoPE time base, and the
**playback** fps `base × 2**rounds`, uncapped, which governs only the decode and the muxed
container. The 60 fps cap is load-bearing — RoPE time is `pixel_frame / fps`, so an uncapped time
base shrinks every token's temporal span relative to the trained distribution, whose failure
signature is a motion spike at each latent boundary followed by a stall rather than anything that
looks like noise. All three are logged per round.

Each tile draws its ancestral noise from its own generator at
`seed + 10000 + 1000 × round + tile`, extending the engine's existing stream-offset convention
(initial noise at `seed`, ancestral at `+10000`, decode at `+20000`). Tiles are positionally
identical, so a shared ancestral stream would inject byte-identical noise into every one of them and
correlate them visibly.

**What increment 1 does not do yet.** DFR's full scheme also has the base and detailing phases
*generate* keyframe content into extra appended token groups ("slots") at the segment boundaries, and
carries those generated keyframes between rounds. None of that is implemented: increment 1 carries
**anchors only**, synthesized from the incoming latent by decoding it once and VAE-encoding the pixel
frame at each seam as a standalone one-frame clip. (Slicing a mid-stream latent frame is not a
substitute — under causal encoding such a frame encodes eight pixel frames relative to its
predecessors.) A consequence worth knowing: with nothing added to the keyframe bag, the seams only
double each round, so round 2 runs on half the seam density the full scheme would use. The pipe's
`reanchor_each_round` config re-derives the bag at full canvas density from each round's own output,
at the cost of one extra decode per round. The detailing IC-LoRA pass is likewise not implemented,
which is exactly what DFR degrades to without that LoRA — so phase B remains today's plain refine.

Because anchors are ordinary given-content conditioning, none of this depends on the
`use_keyframes_abs_pos_embedding` weight below; the rounds run on any LTX-2.5 checkpoint the engine
can load. The temporal upscaler, however, is strictly required: `rounds > 0` without it is a hard
error before any GPU work.

## Sampling

Default generation parameters: 24 steps, CFG scale 4.0, 768×512 resolution, 49 frames at 25fps. The default sampler is `euler` on the `LTX-2 / 2.3` preset and `euler_ancestral` on `LTX-2.5` — see [LTX-2.5: ancestral stage-1 sampling](#ltx-25-ancestral-stage-1-sampling) below.

Three more samplers exist specifically for the distilled-refine recipe below:
`euler_sde` (ancestral Euler), `euler_ancestral_cfg_pp`, and `euler_cfg_pp` —
the latter two decouple the CFG-guided step *target* from an unguided step
*direction* (CFG++, arXiv:2406.08070); `euler_cfg_pp` is the deterministic
(non-ancestral) form, `euler_ancestral_cfg_pp` additionally injects fresh
ancestral noise each step. See
`src/platform/runtime/native/sampling/algorithms/euler_cfg_pp.py` and its
sibling `euler_ancestral_cfg_pp.py`. Both are bit-identical to plain `euler` at CFG 1.0 (no uncond branch => the
CFG++ split is a no-op) *for the guided-step target*; `euler_cfg_pp` additionally
never draws noise, so at CFG 1.0 it is bit-identical to `euler` in full, while
`euler_ancestral_cfg_pp` still diverges there via its ancestral noise draw
(default `eta=1.0`). A `manual_sigmas` field (comma-separated, descending,
`1.0` → `0.0`) overrides the shift-based schedule outright, ComfyUI
`ManualSigmas`-style; its length becomes the step count directly
(`schedule="manual"` in `build_sigmas`, `sampling/flow_schedule.py`). A
`linear_quadratic` schedule (LTX-lineage: linear ramp then quadratic tail,
see `_linear_quadratic_sigmas` in `sampling/flow_schedule.py`) is also
selectable via the shared `schedule`/`schedule_options` config knobs for a
generated (non-manual) schedule shaped like the distilled recipe's own hand
tuned curve.

### LTX-2.5: ancestral stage-1 sampling

LTX-2.5 samples stage 1 with an ancestral sampler (`euler_ancestral`,
stochastic, `eta=1.0`/`s_noise=1.0`, drawing from its own dedicated RNG offset
— `ANCESTRAL_NOISE_SEED_OFFSET` in
`src/platform/runtime/native/sampling/algorithms/euler_ancestral.py` — so the
extra draws never shift what a deterministic sampler would have drawn for the
same seed). 2.3-and-earlier stage 1 runs deterministic `euler` (balanced/
quality) or `euler_cfg_pp` (distilled) instead. Stage 2 stays deterministic
`euler` in every generation, 2.5 included.

**Provenance.** The rule is first-party and keyed on the checkpoint
*generation*, not on distilled-vs-dev. Lightricks' own distilled pipeline gates
stage 1 on `detect_model_version(transformer) >= ANCESTRAL_SAMPLER_SINCE_VERSION`,
and that constant is exactly `(2, 5)`; the ancestral step runs at `eta=1.0`,
`s_noise=1.0`, with the loop's noise generator offset from the pipeline seed by
`10000`. All three already match this engine's `euler_ancestral` defaults and
its `ANCESTRAL_NOISE_SEED_OFFSET`. (Constants read from
`packages/ltx-pipelines/src/ltx_pipelines/distilled.py` in
`github.com/Lightricks/LTX-2` — facts only; that repo is community-licensed and
no code was consulted or ported from it.) Because the gate lives in the
*distilled* pipeline, "distilled still samples ancestrally at 2.5" is settled
rather than inferred. Stage 2 is always deterministic there too, for the reason
that pipeline's own docstring gives: its 3-step refinement schedule is too short
to remove freshly injected noise.

Cross-checked against diffusers main (Apache-2.0), which ships the same step
math as `LTXEulerAncestralRFScheduler`
(`schedulers/scheduling_ltx_euler_ancestral_rf.py`, © Lightricks + HuggingFace)
at the same `eta`/`s_noise` defaults.

This is a plain preset choice, not a runtime version gate: `content/presets/marketplace/
LTX-2.5` sets `sampler: "euler_ancestral"` on all four speed profiles
(`balanced`/`distilled`/`quality`/`custom`), and `generator_stage1` in
`modes/video/pipeline.yml` reads it through the usual
`form.sampler | default(get_speed_profile(...)['sampler'])` idiom. The visible
Sampler field stays selectable (its reactions bake the profile's value, so it
can never disagree with what runs) for anyone who wants a deterministic
sampler back. `generator_stage2` and the standalone `upscale` mode's refine
pass are hardcoded `euler`, matching "stage 2 stays deterministic" above.

### Dynamic-shift schedule (`ltx_dynamic`)

`schedule: "ltx_dynamic"` (`_ltx_dynamic_shift_sigmas`,
`sampling/flow_schedule.py`) interpolates the flow shift between
`schedule_options.base_shift` (0.95) and `max_shift` (2.05) from the packed
video token count (endpoints 1024/4096 — LTX-2.5's own anchors; Flux1's
otherwise identically-shaped schedule uses 256/4096), then optionally
`stretch`es the tail so the last nonzero sigma lands on `terminal` (0.1). All
four values are confirmed against diffusers main, which calls
`calculate_shift(latents.shape[1], base_image_seq_len=1024,
max_image_seq_len=4096, base_shift=0.95, max_shift=2.05)`
(`pipelines/ltx2/pipeline_ltx2.py:1335`); the modular path agrees, passing a
`video_seq_len` derived from latent frames × height × width
(`modular_pipelines/ltx2/before_denoise.py:394-406`). Both are the packed video
token count *before* any keyframe/reference conditioning tokens are appended —
which is why the generators pass their base token count, not base + extras.

Check the version you are reading: diffusers **0.39.0** (what the repo venv
pins) predates the LTX-2.5 PR and passes the `max_image_seq_len` anchor as that
first argument instead, making `mu` a constant `max_shift`. Only diffusers main
is a valid 2.5 reference here.

`content/presets/marketplace/LTX-2.5` selects it: `schedule: "ltx_dynamic"` on
`generator_stage1` in `modes/video/pipeline.yml`, with `schedule_options` left
unset so the anchors above apply. It is deliberately *not* set anywhere else —
`generator_stage2` and the standalone `upscale` mode's refine pass both run an
explicit `refine_sigmas` list, leaving no generated curve for a dynamic shift
to shape. A non-empty `manual_sigmas` also takes priority over `schedule`
(`schedule_settings_overrides`, `guidance_options.py`), so the Distilled
profile's hand-tuned list still wins by design.

`content/presets/marketplace/LTX-2` sets no `schedule:` key at all: 2.0/2.3 have no such
curve, and the LTX `ModelSpec` pins a static shift (`exp(2.05)≈7.768`, the
max-shift anchor — see the frontmatter above) for every checkpoint. That
`ModelSpec` shift is unchanged by any of this; `ltx_dynamic` overrides the
schedule at the sampling layer rather than repointing the registry.

Both the distilled-refine sigma schedule and the Quality recipe are unchanged
from 2.3 to 2.5, so the two presets carry byte-identical
`vars.distilled_sigma_recipe` and `vars.quality_*` values: Quality runs 30
steps, CFG 3.0 (plus STG 1.0, rescale 0.7, modality 3.0, blocks `28`);
Distilled runs the same 9-value `DISTILLED_SIGMA_VALUES` schedule documented
below. Only the sampler differs (see above).

## Limitations

The 19B, 2.3, and 2.5 checkpoint shapes differ structurally (gated attention,
prompt-conditioning modulation, and 2.5's own new flags) even though all three
detect into the same `ltxav` variant — this is handled inside the transformer
block implementation, not exposed as a separate model spec. 2.5 checkpoints
resolve the ambiguity for the fields their own embedded metadata JSON
declares; earlier checkpoints rely on shape-sniffing alone, so the checkpoint
metadata is the only reliable way to tell which shape a given pre-2.5 file is.

### Not yet supported (LTX-2.5, phase 2)

- **Duration head** — loads, but nothing consults it. `model_loader/ltx`'s
  `duration_head` config acquires the head
  (`arch/ltx/duration_head.py`, `LTXDurationHead`) into `bundle.duration_head`,
  and `bundle.predict_num_frames(...)` returns a frame count on the VAE's
  temporal grid from the prompt connector outputs. No generator pipe calls it:
  there is no auto-duration form field, and every mode still takes an explicit
  frame count. The port is the model plus the prediction API, not the feature.
- **Multishot** — not wired. The `LTX-2.5` preset's Director mode joins segment
  prompts into one prompt for a single generation
  (`video_director.modes.director` in `preset.yml`), exactly as the 2.0/2.3
  preset does, rather than LTX-2.5-native multishot conditioning. (Multishot
  and DFR are separate features; DFR is partly implemented — see below.)
- **int8-convrot DiT** — untested. Quantized 2.5 split files load through the
  same generic quant path as every other family (`detect_quant_format` + the
  vendored int8_tensorwise/ConvRot and nvfp4 ops): the **nvfp4 DiT** and the
  **int8-convrot Gemma4 TE** are GPU-confirmed in real generations
  (2026-08-12). Only the int8-convrot *DiT* has never been loaded — relevant
  because it is the one 2.5 repack that carries the trained
  `keyframes_abs_pos_embedding` (the nvfp4 repack dropped that tensor).
- **`use_keyframes_abs_pos_embedding`** — detected
  (`detect/unet_detect.py`'s `_detect_ltx`, from the
  `keyframes_abs_pos_embedding` state-dict key or the checkpoint's embedded
  metadata) and constructed on the model (`arch/ltx/model.py`,
  `LTXAVConfig.use_keyframes_abs_pos_embedding` — construction/load-parity
  only, per that module's own comment), but no generator pipe feeds it
  generated-keyframe positional data yet — detected but not yet consumed,
  pending a 2.5.1+ generated-keyframe checkpoint's forward-path wiring.

Audio generation is off by default at zero extra cost when unset.

**Distilled-refine recipe.** This section describes the `LTX-2 / 2.3` preset
(`content/presets/marketplace/LTX-2`); the `LTX-2.5` preset inherits the same sigma recipe
and profile structure, differing only in its stage-1 sampler (see above). That
preset defaults to
single-pass, full-CFG generation (CFG 4.0, 24 steps, `euler`) — do not add a
distilled/turbo LoRA at those settings; it is trained for a short, low-CFG
(~1.0) schedule and produces noisy output otherwise. The reference ComfyUI
LTX-2.3 workflow (`content/plugins/marketplace/comfyui-backend/presets/LTX-2-3/official/modes/t2v/files/workflows/t2v.json`)
actually runs TWO independent full-resolution generations from the same seed —
a "full" pass (own custom multi-term guidance: skip-layer + a
cross-modal-attention "modality" guidance term + a std-preserving rescale,
none of which this native engine implements yet) and a "distilled" pass
(distilled LoRA at 0.5 strength, CFG 1.0, sampler Euler Ancestral CFG++, and
the explicit 9-value sigma schedule `1.0, 0.99375, 0.9875, 0.98125, 0.975,
0.909375, 0.725, 0.421875, 0.0`) — plus a separate, optional 2x-latent-upscale
third pass gated by `upscale_enable` that refines the distilled pass's output
(not the other way around).

The native engine now supports the *distilled* half of that recipe end to
end, as the **Distilled** Speed profile (Generation tab) on the
`video`/Director mode (that preset's only text-to-video mode since
the standalone `txt2vid` mode was retired — a Director document
with no keyframes covers plain t2v): picking it overrides Sampler, CFG, and
Manual Sigmas with **Euler CFG++**, 1.0, and the schedule above, whatever
those fields were individually set to (they revert to being authoritative
again on the Custom profile). Add the distilled LoRA (~0.5 strength) on the
LoRA tab and pick the profile — no need to configure Sampler/CFG/Manual
Sigmas by hand. The sampler default was corrected from the ComfyUI
workflow's `euler_ancestral_cfg_pp` to `euler_cfg_pp` (see the first-party
validation below: Lightricks' own distilled pass is deterministic, no
ancestral noise); `euler_ancestral_cfg_pp` stays selectable on the Custom
profile for anyone who wants the community (ComfyUI) ancestral variant
instead. The "full" pass's skip-layer/modality/rescale guidance stack (Lightricks'
`MultiModalGuider`) IS ported, as the **Quality** speed profile: it replaces the sampler's
usual guidance strategy outright (`quality_mode` config knob →
`src/platform/runtime/native/sampling/multimodal_guider.py`'s `MultiModalGuidance`), running up
to four forwards per step (cond, uncond, STG-perturbed, modality-off) and combining them per
modality slice (video/audio). `quality_mode` and `distilled_mode` are mutually exclusive full-recipe
overrides — setting both raises (`check_guider_mode_conflict`). `MultiModalGuidance` fully owns the
guidance strategy for the step, so it cannot be combined with FBCache (`step_cache`) or NAG
(`nag_scale`) in the same run the way the normal guidance path can — the source documents this pairing
as unsupported ("INCOMPATIBLE with this multi-pass strategy ... no deep integration in this port"),
though neither of those two knobs is disabled or blocked from being set at the same time as Quality
today; enabling all three together is untested and not recommended.

**First-party validation (Lightricks `LTX-2` repo, `packages/ltx-pipelines`,
Apache-2.0).** Checked the 9-value sigma recipe above and the ComfyUI
workflow's parameters against Lightricks' own inference package
(`ltx_pipelines.utils.constants`, `github.com/Lightricks/LTX-2`):

- `DISTILLED_SIGMA_VALUES = [1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375,
  0.725, 0.421875, 0.0]` — an EXACT, digit-for-digit match with the recipe
  above; this isn't a community approximation, it's Lightricks' own shipped
  constant. `STAGE_2_DISTILLED_SIGMA_VALUES = [0.909375, 0.725, 0.421875,
  0.0]` is a suffix of it, used for their own upscale/refine stage (confirms
  the Pass1/Pass2/upscale read above: upscale reuses the tail of the
  distilled schedule, not the full-pass one).
- Lightricks' own distilled pipeline (`DistilledA2VPipeline` /
  `SimpleDenoiser` / `euler_denoising_loop`) runs NO classifier-free guidance
  at all for the distilled pass (a single transformer call per step, matching
  CFG=1.0) and — per the function's own name and the absence of any
  noise-sampler/eta code in `blocks.py` — is a **plain deterministic Euler**
  loop, not ancestral. The ComfyUI workflow's choice of `euler_ancestral_cfg_pp`
  was a community layering on top of Lightricks' schedule that this preset's
  Distilled profile used to inherit by default: at CFG=1.0 that sampler's
  CFG++ split already degenerated to a no-op (see the sampler's own module
  docstring), but its ancestral noise injection itself (default `eta=1.0`,
  never exposed as a preset field) still ran every step, unlike Lightricks'
  own path. **Fixed**: the Distilled profile's default sampler is now
  `euler_cfg_pp` (`sampling/algorithms/euler_cfg_pp.py`) — the same CFG++
  target/direction split with no noise draw ever, so at this profile's own
  CFG=1.0 it is bit-identical to plain deterministic `euler`, matching
  Lightricks' own distilled pass exactly. `euler_ancestral_cfg_pp` is still
  offered (Custom profile / manual sampler choice) for anyone who prefers the
  community ancestral variant.
- Useful for the F-pass work (not implemented here): Lightricks'
  constants define per-checkpoint STG block indices and step counts —
  `LTX_2_3_PARAMS`: `num_inference_steps=30`, `stg_blocks=[28]` (confirms the
  ComfyUI workflow's `skip_blocks='28'` is the first-party value, not a
  community guess); `LTX_2_3_HQ_PARAMS`: `num_inference_steps=15`,
  `stg_blocks=[]` (STG OFF) — note the ComfyUI Pass 1 default of 15 steps
  actually matches the "HQ" (no-STG) variant's step count while ALSO
  enabling STG (`skip_blocks='28'`, the plain-variant's setting) — the
  community recipe blends properties of two distinct first-party presets,
  not a straight port of either. Per-modality CFG defaults in the same
  constants file: video `cfg_scale=3.0`, audio `cfg_scale=7.0` (also an exact
  match to the ComfyUI workflow's `video_cfg`/`audio_cfg` defaults); an "HQ"
  guidance variant sets `stg_scale=0.0` and adjusts `rescale_scale` (0.45
  video / 1.0 audio).

## Hardware

The 2.0/2.3 all-in-one checkpoint (DiT + both VAEs + vocoder + text-embedding projection) is **~27 GB on disk** (19–22B-parameter DiT; `memory_policy.py`'s `ltx2 = 27.0` cost estimate); LTX-2.5's split layout adds up to roughly the same total across its separate DiT/TE/VAE files. No measured VRAM peak exists for any LTX generation yet — **this family is not GPU-validated end-to-end** (AV forward + golden validation is still an open task per `docs/native-engine.md`), so there is no honest tier recommendation to give beyond "not yet" at any card size. Treat every number above as an implemented-contract description, not a measured result.
