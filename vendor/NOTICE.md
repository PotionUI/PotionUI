# Vendored third-party code

This directory bundles third-party source that PotionUI depends on but does not
author. Each component keeps its upstream license alongside the code; per-file
provenance headers name the upstream project and any local modifications.

| Component | Upstream | Version / commit | License | Local modifications |
|-----------|----------|------------------|---------|---------------------|
| `BiRefNet/` | BiRefNet — https://github.com/ZhengPeng7/BiRefNet | commit `25cb9309bacf3dde954e4584594e16e142c51de5` | MIT (`BiRefNet/LICENSE`) | Inference-only subset (per-file headers detail each removal): `PyTorchModelHubMixin`/`from_pretrained` gone (the download vector — weights load only from the model depot), all `self.training` branches gone, vgg/resnet and vit/dino backbone branches gone (`config.py` pins `swin_v1`). Moved here from `content/plugins/marketplace/trellis2/vendor/BiRefNet/` so the spritesheet plugin's `key_mode: "model"` matting and trellis2's background-removal preprocessing share one copy instead of each plugin vendoring the same ~1000-line architecture. Consumed via `src/platform/runtime/native/matting.py`, exported through `src.plugin_api.media.BackgroundMattingModel`. |
| `k_diffusion/` | k-diffusion — https://github.com/crowsonkb/k-diffusion | unknown; vendored ~2025 | MIT (`k_diffusion/LICENSE`) | Trimmed to the `sampling`, `utils`, and `external` modules used by PotionUI. |
| `chainner_pfn/` | chaiNNer ESRGAN RRDB arch — https://github.com/chaiNNer-org/chaiNNer | commit `e4cee69e1fb8a38c1a5cdb7a4b5089cfa15a3179` | GPL-3.0 (`chainner_pfn/LICENSE`) | None apparent. Previously labelled MIT here in error: that MIT text was written locally and was never an upstream grant. Upstream chaiNNer is GPL-3.0, and these files match upstream almost exactly, so they are GPL-3.0. |
| `gpl/fooocus/` | Fooocus — https://github.com/lllyasviel/Fooocus | unknown; vendored ~2025 | GPL-3.0 (`gpl/LICENSE`) | None apparent (`anisotropic.py`, `patch.py`). |
| `gpl/comfyui/` | ComfyUI — https://github.com/comfyanonymous/ComfyUI | unknown; vendored ~2025 | GPL-3.0 (`gpl/LICENSE`) | `ops.py` is `comfy/ops.py` trimmed to the layer namespaces the native engine uses, plus locally written additions (runtime LoRA deltas, partial-residency streaming flags, the `torch._scaled_mm` fp8 and `torch._scaled_mm_v2` nvfp4 fast paths, `comfy_quant` descriptor gating). The nvfp4 dequantiser IS a port, from ComfyUI's own `comfy/float.py` + `comfy/quant_ops.py` (same GPL-3.0 upstream). The int8_tensorwise ConvRot dequantiser is **not** a port: it is written from the published on-disk format (key names, the `comfy_quant` JSON schema, scale ranks, and the Hadamard construction rule — Kronecker power of a format-fixed regular H4, normalised by `1/sqrt(group_size)`). That format is *documented* by **comfy-kitchen** — https://github.com/Comfy-Org/comfy-kitchen, Apache-2.0, Copyright (c) 2025 Comfy Org — but no code was taken from it, and deliberately nothing from its `TensorWiseINT8Layout` (documented as originating in dxqb/OneTrainer, whose licence this project has **not** verified) or from the parts of its eager backend derived from PyTorch AO (BSD-3-Clause, NOTICE not reviewed). Reading a documented file format and implementing a loader for it is not a derivative work; the H4 matrix itself is retained verbatim because it is a format constant, not expression — any other orthogonal H4 decodes these checkpoints to noise. Per-function provenance comments in the file name which upstream each block came from. |
| `sol_attn/` | ComfyUI_sol-attn_Blackwell — https://github.com/KingGore/ComfyUI_sol-attn_Blackwell | commit `a8a9584e1ed700f2ce3b7569048cab0071bbf58a` (2026-08-05) | Apache-2.0 (`sol_attn/LICENSE`) | Runtime subset only: the two attention backends (`flex.py` = upstream's root `sol_attn_flex.py`; `interface.py` + `preprocess.py` + `triton_ref/` = upstream's `sol_attn/` package) and nothing else. Not vendored: the ComfyUI node/loader wrappers, `minimax_h3_patch.py` (this tree wires the model directly instead of monkeypatching an attention override), and `inductor_fix.py` (it MOVES files out of the installed torch package to work around one stale torch 2.11 layout — mutating a user's torch install is not something this project does). Per-file headers list each change; the substantive ones are that `vendor/sol_attn/__init__.py` imports nothing at import time (upstream re-exported `interface.sol_attn`, which pulls in `triton` — a hard dependency this tree cannot take for an opt-in feature), and that `triton_ref/fwd.py`'s two `from sol_attn.<mod>` absolute imports are relative. The CuTe DSL kernels the SM90/SM100 path needs (`common`, `sm90`, `sm100`) are NOT vendored, so that path raises ImportError; upstream already imported them lazily, which is what lets the rest of the module load without `cutlass`/`cuda.bindings`. Consumed only by `src/platform/runtime/native/sol_attn.py`, behind an opt-in preset toggle that is off by default and falls back to the engine's ordinary attention on any failure. Sol-Attn is APPROXIMATE attention (arXiv:2607.24027) — it changes output, which is why nothing reaches it unless a user asks. |
| `sla_attn/` | Two-hop: LightX2V — https://github.com/ModelTC/LightX2V (algorithm origin) via PlagueKind/ComfyUI-PlagueKind-Nodes — https://github.com/PlagueKind/ComfyUI-PlagueKind-Nodes (adaptation layer) | commit `6ca3037bd16dc143b6d461c67c87a28ca8074063` (2026-08-20) | Apache-2.0 (LightX2V) over MIT (PlagueKind); both texts in `sla_attn/LICENSE` | `block_map.py` and `kernel.py` only, taken verbatim from `ComfyUI-H3-SLA-Attention/sla/` in the PlagueKind tree — no local changes beyond the provenance header each file carries. Not vendored: `patch.py`, the ComfyUI model-patch glue that wired these two files into a `MODEL` graph node; `src/platform/runtime/native/sla_attn.py` is its replacement, matching `sol_attn.py`'s opt-in, never-raises seam. SLA is block-sparse APPROXIMATE attention for MiniMax-H3 (mean-pool Q/K into blocks, top-k the key blocks per query block, exact attention only over those) — an alternative sparse-attention backend to Sol-Attn, dispatched alongside it through `src/platform/runtime/native/sparse_attn.py`. Off by default; falls back to the engine's ordinary attention on any failure, same contract as Sol-Attn. |
| `rife/` | Practical-RIFE — https://github.com/hzwer/Practical-RIFE | master (fetched 2026-07-23; single-branch, tag-less) | MIT (`rife/LICENSE`) | `warp.py` is `model/warplayer.py` verbatim except the backward-warp grid is built on the flow tensor's own device (CPU support). `ifnet.py`/`loader.py` carry the RIFE 4.x IFNet for the rife46/47/48/49 checkpoints: hzwer ships that network definition ONLY inside the downloadable `train_log/*.py` bundle (MIT), not in the git tree, so it was taken from mirrors of the author's own bundles — the v4.6 `IFNet_HDv3.py` (bundled beside a `flownet.pkl` matching the shipped rife-4.6 weights) and the v4.9 one (beside the `rife49.pth` of sha256 `e55fd00f3cc184e3c65961f4bb827a9da022e78eed36b055242c0ac30000d533`). Module names and shapes follow those files, so the published checkpoints load under their own key names; per-block channel widths and the optional feature encoder are read from the checkpoint at load time. The two generations' differing mask semantics are taken from the same two files. GPL RIFE forks were not consulted. |

## What the licensing here actually means

This code is vendored with provenance and imported by core. It is not
separable: PotionUI calls into it at runtime, so the combined program is
subject to the terms of the strongest license among the parts it links —
GPL-3.0 today.

Earlier revisions of this file described `gpl/` as isolated and excisable as a
unit. That was inaccurate and has been removed. GPL-3.0 material is not
confined to `vendor/gpl/`: core imports it, and further GPL-derived code lives
under `src/` carrying `# Derived from:` markers. Those markers record
provenance; they do not imply the code can be dropped without replacing what it
does.

PotionUI as a whole is licensed under **GPL-3.0** (see the repository-root
`LICENSE`), which is the strongest license among the linked components, so no
component needs replacing on licensing grounds. Permissively-licensed vendored
components (MIT, Apache-2.0) remain under their own upstream terms, which are
GPL-3.0-compatible.

## Permissively-licensed lineage under `src/` (outside `vendor/`)

`# Derived from:` markers under `src/` are not all GPL. The entries below name
code whose upstream is permissively licensed (**Apache-2.0** or **MIT**), so it
imposes no copyleft of its own on the combined program — only the attribution
the licence requires, which the per-file markers provide.

| Location | Upstream | License | Notes |
|----------|----------|---------|-------|
| `src/platform/runtime/native/sampling/algorithms/unipc.py` | `diffusers` `UniPCMultistepScheduler` (`schedulers/scheduling_unipc_multistep.py`), Copyright TSAIL Team and The HuggingFace Team | Apache-2.0 | Flow-matching UniPC. The scheduler class is reshaped into a functional sampler loop; the predictor/corrector B(h) expressions, the flow noise map, and the warmup/`lower_order_final` bookkeeping are the reference's, configured at its own defaults. Algorithm published as arXiv:2302.04867 with a reference implementation at `wl-zhao/UniPC`. `tests/platform/runtime/native/sampling/test_unipc_reference_equivalence.py` pins the loop to that scheduler numerically. |
| `src/platform/runtime/native/sampling/flow_schedule.py` (`_anchored_mu`) | `diffusers` `calculate_shift` (`pipelines/krea2/pipeline_krea2.py`, itself copied from `pipelines/flux/pipeline_flux.py`) | Apache-2.0 | Two-point sequence-length -> mu line, with the endpoints reparameterised from tokens to pixels. Krea-2's anchors are the `base_image_seq_len`/`max_image_seq_len`/`base_shift`/`max_shift` documented on `Krea2Pipeline`. Covered by the same test file. |
| `src/platform/runtime/native/arch/krea2/{model,layers}.py` | `diffusers` `transformer_krea2.py`, Copyright Krea AI and The HuggingFace Team | Apache-2.0 | Forked and renamed to the native checkpoint's key space; see each file's own header for the per-symbol mapping and the local additions. Rotary embeddings are the exception: they come from `vendor/gpl/comfyui/flux/math_ops.py` (GPL-3.0) by reference. |
| `src/platform/runtime/native/text_encoders/{qwen3,tokenization}.py` (Krea-2 Qwen3-VL conditioning) | `diffusers` `Krea2Pipeline` | Apache-2.0 | Prompt template strings, the stripped system-prefix length, and the fused hidden-state layer set. |
| `src/platform/runtime/native/sparse3d/` | `microsoft/TRELLIS.2` `trellis2/modules/sparse/`, Copyright (c) Microsoft Corporation | MIT | Pure-torch port of the sparse spatial-tensor container, spatial up/down-sampling and channel<->spatial octree ops, sparse RoPE, and varlen attention packing; the compiled conv/attention backends are not ported. Per-file `# Derived from:` markers name the exact source modules. |

### Wan2GP

Wan2GP (GPL-3.0) is **not** an upstream of this tree. Earlier revisions of the
files above credited it — for the Krea-2 architecture, the Qwen3-VL conditioning
constants, the UniPC solver, and the anchored-mu schedule — and those credits
were wrong in the same way each time: the expression in question is present in
`diffusers` under Apache-2.0, which is where it belongs. Those markers have been
corrected to name the primary source.

Comments that still name Wan2GP do so deliberately, and none of them is a lineage
claim. `arch/krea2/{model,layers}.py` and
`tests/platform/runtime/native/arch/test_krea2_model.py` record which expressions
were established *not* to be Wan2GP-original and what replaced the ones that
were. `vae/ltx_audio.py` cites it alongside diffusers as the *second* independent
confirmation of two facts documented in neither checkpoint's config (a
non-persistent resampler filter; log-space Snake alpha/beta) — observing what
another project does is not deriving from it.

`src/platform/runtime/native/attention.py` was also credited to it ("Modelled on
Wan2GP's `shared/attention.py`"). That was never a lineage claim either — the
module is locally written, and its per-backend functions are short adapters onto
the attention kernel packages' own public entry points — so the note has been
replaced with an accurate one.

## Vendored model configuration assets (outside `vendor/`)

Non-code, third-party *config/tokenizer* assets follow the same disclosure
requirement but live next to the subsystem that consumes them (the precedent
set by `src/platform/runtime/native/text_encoders/assets/`), not under
`vendor/`, since `vendor/` is reserved for source code:

| Location | Upstream | Version / commit | License | Contents |
|----------|----------|-------------------|---------|----------|
| `src/pipelines/pipes/checkpoint_loader/sdxl/assets/sdxl_base_pipeline_config/` | `stabilityai/stable-diffusion-xl-base-1.0` — https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0 | commit `462165984030d82259a11f4367a4eed129e94a7b` | CreativeML Open RAIL++-M (`license:openrail++`) | `model_index.json` + per-component `config.json` + CLIP tokenizer vocab (`tokenizer/`, `tokenizer_2/`). No weights. See the directory's own `README.md` for the full provenance note and why it exists (keeps `from_single_file()` off the Hugging Face Hub on a cold cache — `HF_HUB_OFFLINE=1` is the native engine default). |
| `src/platform/runtime/native/text_encoders/assets/` | ComfyUI-bundled tokenizer assets (Qwen2/Qwen3, T5-XXL, CLIP-L, UMT5, Gemma3 spiece) | see `tokenization.py` module docstring | per-upstream (CLIP/Qwen/T5/Gemma vocabularies are model-open) | Tokenizer-only files consumed by the native text-encoder stack; no weights. |
