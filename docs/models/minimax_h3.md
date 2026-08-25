---
type: model
title: MiniMax-H3
family_key: minimax_h3
modes: [video, refs, upscale]
spec:
  arch: MiniMaxH3Model — one packed sequence carrying video, text and audio rows through 50 shared blocks, no cross-attention
  params: 20B (pruned) / 33B (full)
  latent: 24-channel video latents (16x spatial, 17-frames-to-5-latents temporal) plus 32-channel audio latents at 40 per second
  vae: minimax_h3_video + minimax_h3_audio (two separate files)
  te: Qwen3-VL-32B, trimmed to 50 decoder layers
  guidance: "none"
  shift: "12.0 video / 3.0 audio"
  engine: native
files:
  - role: dit
    dir: models/diffusion_models
    note: fl2va or ref2va, pruned (rank-8 AdaLN) or full; fp8-scaled or bf16
  - role: text_encoder
    dir: models/text_encoders
    note: the trimmed Qwen3-VL-32B repack (layers 0–49, no final norm, no lm_head)
  - role: vae
    dir: models/vae
    note: the video VAE
  - role: audio_vae
    dir: models/vae
    note: the audio VAE — always loaded, audio is not optional for this family
---

# MiniMax-H3

MiniMax-H3 (`src/platform/runtime/native/arch/minimax_h3/model.py`) generates a video track and its soundtrack jointly: video rows, text rows and audio rows are concatenated into **one** sequence that every block attends over, so there is no cross-attention and no separate audio pass. It is guidance-distilled — no CFG, no negative prompt, one forward per step — and runs flow matching with a reversed velocity sign and the timestep fed as `1 - sigma`.

The two streams do not share a noise schedule. Video and audio step the same number of times, but on independently shifted sigma grids (12.0 and 3.0), and the per-row timestep vector built for each forward is what lets one transformer call serve both. Both shifts are applied to **one** underlying `t` grid, so at every knot the two sigmas are two images of the same `t` — which is what keeps the pair packable into a single forward under any scheduler.

A step is one model evaluation (an NFE). `steps = N` puts `N` knots on the unshifted grid at `q_i = (N - i)/N` plus the terminal `0`, matching ModelTC's published turbo schedules: `steps = 4` gives video `[1, 0.973, 0.923, 0.8] → 0` and audio `[1, 0.9, 0.75, 0.5] → 0`. The Advanced tab's manual sigma boxes are the one place the other convention applies — those spell out the grid itself, so an `N`-value list runs `N - 1` steps.

## Files & detection

Every component is its own file — the DiT, the text encoder, the video VAE and the audio VAE are four separate downloads and four separate pickers in a preset. Detection keys on the DiT's structural signature (`video_patch_proj` + `audio_patch_proj` + `condition_proj` + fused `blocks.0.attn.qkv_proj`), and the same `ModelSpec` covers both checkpoint shapes: the **full** 33B checkpoint with a timestep-embedder MLP and full per-block AdaLN projections, and the **pruned** 20B one where that whole branch collapses to a 1025-entry lookup table and a rank-8 factorization.

What detection cannot see is the difference between the `fl2va` and `ref2va` checkpoints: they are architecturally byte-identical, same keys, same config. Only the file you pick decides which one runs, and picking the wrong one produces a silently wrong generation rather than an error.

The text encoder is the trimmed Qwen3-VL-32B repack — 50 decoder layers, no final norm, no LM head. H3's encode contract is the raw prompt with no chat template, no system prompt and no special tokens, tapped after layer 49; keyframe images enter the same sequence as vision blocks labelled `<Picture i>: `.

## Presets & modes

`content/presets/marketplace/MiniMax-H3` ships two modes. `video` covers both `t2va` (prompt only) and `fl2va` (optional first-frame and last-frame anchors); `refs` covers `ref2va`, conditioning on a set of references instead of keyframes. The same checkpoint architecture and the same generator pipe handle all three — but `ref2va` and `fl2va` are different *partitions* of the weights, so the `refs` mode needs the `ref2va` DiT file and the `video` mode the `fl2va` one, and nothing can detect a wrong pick.

### The `refs` mode (`ref2va`)

Three pickers, one per modality: up to 9 reference images, 3 videos and 3 audio tracks, 12 in total, and at least one reference of some kind. An audio reference cannot be the only kind — a soundtrack conditions a video that some visual reference has to anchor. Each modality contributes differently: an image contributes one visual condition latent encoded at its own 2048px-short-edge resolution; a video contributes one visual condition latent as a whole frame stack (so `5n + 2` latent frames rather than 1), fitted to the canvas its own aspect ratio resolves to and truncated to the generated clip's length; an audio track contributes clean condition rows through the audio VAE and no picture at all.

The three pickers are **one packed sequence**, in a fixed order: every image, then every video, then every audio track. That order is what fixes each reference's label (`<Picture i>: `, `<Video k>: `, `<Audio j>: `, numbered per modality by the text encoder itself) and its position on the packed rotary clock. Three separate traversals have to agree on it — the encoder's presentation, the generator's reference blocks, and the condition-latent iterators the layout consumes alongside them — and a disagreement between any two is silent: the request runs, the shapes line up, and every reference conditions the generation from another reference's position. Both consumers therefore derive their traversal from one function (`pipes/_shared/generation/reference_order.py`), and each modality is loaded by exactly one media-loader node that both read.

`mode: "references"` on the generator is what makes an empty reference set an error rather than a text-only run: on the reference partition that fallback returns plausible video that ignores the request.

A keyframe is used twice. It goes through the text encoder's vision tower, so it appears in the prompt presentation as a `<Picture i>: ` block; and it is VAE-encoded into condition rows prepended to the packed sequence and pinned to a fixed timestep, rather than blended into the target latent behind a mask the way LTX conditions. Both consumers read one media-loader node in the preset, so they cannot disagree about which images they got or in what order. The keyframes are a fixed set shared by every output of a request rather than one source image per output, which the clip adapter signals with `forwards_full_image_batch` so `prompt_encoder` forwards the whole list to each request instead of indexing into it.

The preset exposes resolution, clip length, steps, sampler, scheduler and seed. There is no guidance scale and no negative prompt to expose, and audio has no toggle because it is inherent to the checkpoint — but the solver and the knot placement are independent of guidance, so both are pickers on the Generation tab.

### The `upscale` mode (HD detailer)

Takes an existing H3 video (a `video`/`refs`-mode output, or anything else on the checkpoint's own canvas grid) and runs it through a latent-space spatial upscale followed by a short partial-denoise refine, rather than a fresh generation. Two pipe stages do the work: `latent_upscaler/minimax_h3` VAE-encodes the source onto the release canvas for its aspect ratio, pads the frame count up to the video VAE's `17n + 5` lattice, and upsamples the resulting latent with a dedicated upscaler checkpoint; `generator/video_minimax_h3` then refines that upsampled latent through its `initial_latent`/`denoise` refine entry path (the same mechanism a from-noise generation's `initial_latent` input uses, described above under "Sampling") instead of starting from pure noise.

The released recipe: target ≈2.1 decimal-megapixel area (1920×1088 for a 16:9 source), 4 refine steps, denoise 0.45, video stream sigma shift 9 (lower than a fresh generation's 12, since the source is already close to the target). The audio stream's own shift stays fixed at 3.0 and is not exposed for a refine. cfg is not a knob here as elsewhere in this family — H3 is guidance-distilled and the generator pipe has none. The source clip's own audio track passes straight through into the refined output unchanged (`audio_source: "passthrough"`); this mode does not re-sample or re-mix the soundtrack.

Three friendly presets are offered for the refine's denoise (Light 0.30 / Balanced 0.45 / Strong 0.60), plus an expert override on the Advanced tab for denoise, video sigma shift and step count directly. A turbo LoRA at ~0.5 strength is an optional accelerant for this pass — it is not required, and this repository's own 4-step turbo recipe is validated LoRA-free. If you do load one, use **Kijai's `_comfy` conversion** (`minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors`): the original ModelTC file is missing its alpha metadata and runs 8× too strong at face value, which produces pure structured noise from the first step — see "The original turbo LoRA is missing its alpha metadata" under Known issues below before picking a file.

**Weights provenance.** The default latent upscaler (`LBH-123-AI/Minimax_h3_latent_Upscaler` on Hugging Face, Apache-2.0) is a community-trained checkpoint, not a MiniMax first-party release — trained on roughly 80k latent pairs, with its architecture reconstructed from the checkpoint's own layout rather than documented upstream. Treat it as a useful, unofficial complement to the base model rather than a first-party recipe.

**Caveats.** GPU validation for this mode is pending, same as the rest of the family. A ~2.1MP target over many frames makes the refine pass VRAM-heavy — for long clips, pair it with `sparse_attn: sla` and the SLA turbo LoRA (see "Sampling" below) rather than running it dense.

## Sampling

Default generation parameters: 24 steps, 1344×768, 124 frames at a fixed 24 fps (≈5.2 s).

The preset offers two speed profiles, and they carry a step count and nothing else — being guidance-distilled, the family has no guidance for a profile to switch, and the sampler and scheduler are left to the user rather than pinned per profile. `quality` is the 24-step default; `turbo` is 4 steps.

Three samplers are available, all stepping in `x0` space off the same data estimate. `euler` is the reference solver and the default, and it is what the turbo LoRA was distilled against. `res_multistep` (second-order exponential Adams–Bashforth, derived from the exponential-integrator form rather than ported) and `dpmpp_2m` (DPM-Solver++(2M), adapted from the MIT-licensed `vendor/k_diffusion`) both reuse the previous step's estimate, so they cost no extra forward and buy accuracy where the grid is coarse — most visibly at turbo step counts. Their history is per stream and per window: video and audio walk different sigma grids inside one forward, and each window is an independent trajectory whose first step legitimately has no past.

Two schedulers place the knots. `simple` is the reference uniform grid; `beta` pushes the same uniform quantiles through a Beta(0.6, 0.6) quantile function, which clusters knots at both ends — more steps on early composition and on final detail, fewer in the middle. A manual sigma grid together with a non-`simple` scheduler is refused rather than resolved by precedence: both knobs answer the same question, and a silent winner would leave the scheduler picker reading as if it did something on a run it has no say in.

`turbo` needs no LoRA. The base model is a capable three-to-four-step generator on its own shift-12 schedule, producing usable output with softer detail than a 24-step run rather than the noise a few-step run on an undistilled model would normally give. The turbo distillation LoRA sharpens it further, but it is an option, not a prerequisite.

Two further speed levers ship off by default. The keyframes tab carries a pixel-budget cap that bounds how large a keyframe the text encoder's vision tower reads (as a multiple of the output canvas area — keyframes otherwise enter at up to their full 16.7-megapixel processor bound, which the 32B encoder pays for directly); and the advanced tab carries the same First Block Cache knobs as the other video families (`step_cache_threshold` and friends), wired into this family's bespoke dual-schedule loop so a skipped step reuses both streams' velocity in one decision. A replayed velocity enters a multistep sampler's history unchanged — it is the model's most recent real output, and the sample it applies to did move — so caching and a second-order sampler compound: cache more conservatively (a lower `step_cache_max_skips`) when running `res_multistep` or `dpmpp_2m`. Neither lever has been speed- or quality-measured on real H3 weights yet.

A third lever, also off by default, is **sparse attention** (advanced tab: `sparse_attn`, one method select — `off` / `sol` / `sla` — plus each method's own knobs). Both methods are approximations of attention, not a cheaper way to compute the same numbers, and both compose with the step cache — a cached skip never reaches attention at all.

**Sol-Attn** (`sparse_attn: sol`, tuned with `sol_attn_tau`) summarises every 128-token KV block by its mean vector, scores those summaries against pooled query blocks, and computes exact attention only over the blocks that clear a threshold plus the always-exact local diagonal window. The implementation is vendored from [ComfyUI_sol-attn_Blackwell](https://github.com/KingGore/ComfyUI_sol-attn_Blackwell) (Apache-2.0) at `vendor/sol_attn/`; upstream measured its speedups on an RTX 5090 (sm120) and this engine allows it on any bfloat16 CUDA device of compute capability 8.0+, unmeasured elsewhere.

**SLA** (`sparse_attn: sla`, tuned with `sla_sparsity` and `sla_block_size`) instead mean-pools every KV block and every query block, scores the pooled pairs with one matmul, and keeps only the top-k fraction of key blocks per query block — a fixed sparsity budget rather than a threshold. It pairs with the LightX2V SLA turbo LoRA, distilled against ~85% sparsity, so `sla_sparsity` 0.85-0.90 is the sensible range once that LoRA is loaded; below roughly 0.60 SLA is slower than dense attention, not faster. `sla_block_size` defaults to 64, the audio-safe choice — 128 forces roughly 1.6s of audio through one attention pattern and made speech robotic in upstream testing. The implementation is vendored at `vendor/sla_attn/` (Apache-2.0 over MIT), a single Triton kernel with no CPU/non-CUDA fallback backend the way Sol-Attn has a second implementation.

Both methods pin the packed sequence's whole prefix (text, condition audio, keyframe conditioning and the target audio rows) so every query still attends to it exactly, and both run the last `sparse_attn_dense_last_steps` steps of every window (2 by default) on the ordinary dense path — the end of a trajectory carries the least noise to hide a sparse approximation behind. Anywhere a method cannot run — no CUDA, wrong dtype, a torch whose `flex_attention` fails (Sol-Attn) or no usable `triton` (SLA) — it logs one warning naming the reason and the generation continues on the normal attention path at normal speed and quality. Neither method has been speed- or quality-measured on real H3 weights yet.

Choosing between them: Sol-Attn is the threshold-routing method to A/B against dense attention at an arbitrary step count. SLA is the one to reach for on a 4-step Turbo run paired with the SLA turbo LoRA, and it needs far less transient VRAM to get there.

Neither method is free in VRAM, but the cost differs sharply between them. Sol-Attn's routing pass materialises the packed q/k/v several times over — padded to a multiple of 128, then permuted contiguous to head-major — so at 768×1344 / 141 frames (43k rows, 56 heads) one such copy is 590 MiB and the peak holds roughly eight of them, about 5.3 GB on top of the ordinary activation reserve. SLA's routing works in pooled block space instead of full-size copies, so the same 43k-row sequence reserves well under 1.5 GB. The generator estimates each with its own `estimate_transient_gb` and passes the result to `place_dit_for_sequence` as `reserve_gb`, so the placement holds the room back before deciding how much of the DiT stays resident; with the feature off the reserve is 0.0 and placement is unchanged. Sol-Attn's first real run without this reserve OOM'd mid-sampling (a 590 MiB allocation with 151 MiB free) and fell back to dense attention as designed. Expect enabling Sol-Attn on a 32 GB card to push the DiT further into partial residency — whether the cheaper attention pays for the extra weight streaming is exactly what an A/B on real weights has to answer; SLA's much smaller reserve makes that tradeoff far less likely to bite.

Geometry is constrained on both axes. The canvas must be a multiple of 32 with an aspect ratio between 1:4 and 4:1, and the generator rejects anything else instead of snapping it — the released canvas is 1344×768 (short edge 768, area capped at 768·1344), and smaller canvases are the family's strongest speed lever (960×544 runs roughly 2.3× faster per step). The frame count *is* snapped, upward to the next `17n + 5` the video VAE can decode, which maps to `5n + 2` latent frames; the audio stream's length follows from it at 40 latents per second per channel, doubled for stereo. Clips run 5 to 15 seconds.

Sampling draws three noise tensors from one generator per seed, in a fixed order — conditioning, then video, then audio. Reproducing a seed elsewhere requires that same order.

## Limitations

- **The weights are territorially restricted.** MiniMax-H3's weights are published under the MiniMax H3 Community License, not an open-source license. Its Applicable Territory excludes the European Union, the United Kingdom, the United States and the Republic of Korea, and the exclusion extends to the model's outputs; use in those territories requires an individual authorization from MiniMax (https://platform.minimax.io/h3-license). The license additionally requires separate written authorization above US$20M annual revenue, and requires a commercial product's interface to display "MiniMax H3" prominently. PotionUI ships no H3 weights. The architecture implementation is ported from the Apache-2.0 diffusers source, which carries none of these terms.
- **The original turbo LoRA is missing its alpha metadata.** The ModelTC release of the 4-step distillation LoRA ships without the PEFT alpha its training used (alpha 16 at rank 128, so the intended scale is 0.125). Nothing in the file says so, which means any consumer applying it at face value — this engine and ComfyUI's generic default alike — runs it eight times too strong and gets noise at any step count. Two workarounds: use Kijai's conversion (`minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors` in [Kijai/MiniMax-H3_comfy](https://huggingface.co/Kijai/MiniMax-H3_comfy)), which bakes the correct scale in and is right at roughly 0.75 strength, or keep the original and set its strength to 0.125 by hand. The preset's LoRA field points at the former in its `ai_hint`; it cannot offer it as a download, because `recommendations:` is a `model`-field feature that `lora_picker` does not serialize. Full derivation, including the bit-exact comparison that proved the factor, is in `ai/minimax_h3/TURBO_LORA_FINDINGS.md`.
- **A reference video's own soundtrack is ignored.** The released pipeline reads sound off a video reference that carries one; here a video reference conditions the picture only, and sound reaches the model exclusively through an explicit audio reference. The reason is that the two consumers decide independently: the text encoder emits `<Audio j>: ` for an audio-bearing reference, the generator packs the rows, and if a probe and a demux ever disagreed about whether a file has usable sound the presentation and the packed sequence would silently describe different references. Picking the audio track explicitly makes that impossible and puts the choice in the user's hands.
- **`int8_convrot` is supported for the video VAE, untested for the DiT.** The quantized video VAE repack (`minimax_h3_video_vae_int8_convrot.safetensors` in [Kijai/MiniMax-H3-experimental](https://huggingface.co/Kijai/MiniMax-H3-experimental)) loads and decodes here: its 144 int8 ViT-decoder linears carry per-output-channel scales plus a ConvRot (Hadamard) rotation the engine un-rotates at dequant, and it is 39% smaller than the fp16 file (3.17 GiB vs 5.21 GiB) with the causal-3D encoder left unquantized. The pruned **DiT** int8_convrot file has not been exercised against the same path; for the DiT, use the fp8-scaled or bf16 repacks.
- **Keyframes do not set the canvas.** The released pipeline derives height and width from the first keyframe's aspect ratio when they are unset; here the configured resolution always wins and the keyframe is fitted onto it.
- **GPU validation is pending.** No end-to-end run on real weights has been performed for this family — everything above describes the implemented contract, not a measured result.

## Hardware

**No local weight files exist and no GPU run has ever been performed for this family** (`docs/native-engine.md`: "GPU validation pending; no local weight files"). The DiT alone is a 20B-parameter pruned checkpoint or a 33B full checkpoint (per this page's front matter); with a separate Qwen3-VL-32B (trimmed) text encoder, a video VAE, and an audio VAE all loaded as four independent files, this is the heaviest family PotionUI documents by a wide margin. There is no supportable tier recommendation — **not recommended on any card size** until real-weight validation exists, and separately, the weights themselves carry a territorial license restriction (excludes the EU, UK, US, and South Korea).
