---
category: Presets / Models
category_order: 70
order: 40
---

# Native Engine v2

`src/platform/runtime/native/` is PotionUI's second-generation native (in-process) inference stack. It loads
modern diffusion models — Flux1, Flux2/Klein, Krea-2, Qwen-Image and Wan 2.2 (video) today; LTX 2.x
in progress — from the same single `.safetensors` files ComfyUI consumes, fully offline, with no
`config.json` and no HuggingFace Hub calls at runtime.

This document is the authoritative reference for the subsystem: what exists, how a checkpoint
becomes a running model, and — the part that matters most for anyone extending it — the contract
a new model family must satisfy. Where a detail depends on a source file, that file is named so you
can double-check.

**Status** (updated 2026-07-09): the full stack is landed and GPU-validated end-to-end. The
loading, detection, ops (incl. fp8-scaled + nvfp4), VAE (2D + causal-3D), sampling, LoRA, attention,
memory-tiering and `engine.py` (`NativeEngineLoader` / `NativeGenerator` / `NativeModel` /
`Conditioning`) layers are implemented and tested, and the pipe/preset integration
(`src/pipelines/pipes/model_loader/*` + `generator/*` + `content/presets/marketplace/*`) has landed for Flux, Krea-2,
Qwen-Image and Wan. **Four image families + one video family are GPU-validated on real weights**
(RTX 5090): Flux2/Klein (fp8 + bf16), Krea-2 turbo, Qwen-Image, and Wan 2.2 t2v (first video); LTX-2
is the one family still in progress (arch/TE/VAE built, AV forward + golden validation open). The
"loading pipeline" and "extension contract" sections below describe the lower-level
`load_torch_file → detect → ModelSpec → ...` path that `engine.py` orchestrates and that every pipe
is built on; `engine.py`'s public API (sample/decode, 5D-latent + causal-3D handling, phase
sequencing) is covered in `src/platform/runtime/native/engine.py`'s own docstrings.

## Design tenets

- **Single-file `.safetensors`, structural detection.** A checkpoint is identified the way ComfyUI
  does it: a signature key that only one family's state dict contains (e.g.
  `double_blocks.0.img_attn.norm.key_norm.scale` for Flux, `txtfusion.projector.weight` for
  Krea-2), then every hyperparameter is read back out of tensor *shapes* — head count, depth,
  hidden size, context width. No `config.json` ships or is fetched; the checkpoint is
  self-describing. See `detect/unet_detect.py`, `detect/te_detect.py`, `detect/vae_detect.py`.
- **Fully offline.** Tokenizer assets are vendored under `text_encoders/` rather than pulled from
  the Hub at load time — the predecessor engine's Qwen-Image implementation depended on a live
  HuggingFace fetch for its tokenizer, which this design explicitly avoids repeating.
- **One flow-matching sampler for every target.** Flux1/Flux2/Klein/Krea-2/Qwen-Image/Wan (and the
  in-progress LTX) are all flow-matching models differing only in their sigma-shift schedule and
  guidance mode. `sampling/denoise_loop.py` is the single loop every family calls; per-model
  behaviour arrives as data (`ModelSpec.sampling_settings`) or a small strategy object, never as a
  branch inside the loop. See "The extension contract" below.
- **Tier-driven memory, not per-model VRAM logic.** `memory/tiering.py` maps a component's actual
  size (or, when unknown, the existing `MemoryPolicy` VRAM tier table) to a placement plan. A new
  model family does not write its own memory-management code.
- **Load integrity is asserted, not logged.** Every load goes through one gate
  (`base.load_into_module`) that hard-fails on any missing/unexpected key outside a per-model
  allowlist, then calls the arch's mandatory `post_load()` and sanity-checks the result (no tensor
  left on the meta device, no NaN/Inf). This is a deliberate reaction to a past failure mode: an
  earlier engine's meta-device assembly silently produced garbage RoPE buffers that *looked* valid.
  See "Load integrity" below.

## Architecture map

```
src/platform/runtime/native/
├── io/                 safetensors loading + state-dict introspection helpers
├── detect/              structural checkpoint detection (DiT / text-encoder / VAE) + ModelSpec registry
├── ops/                 layer namespaces (plain / cast-on-forward / fp8-scaled) + dtype selection
├── base.py               NativeArchModule contract + the load-integrity gate
├── attention.py           attention backend dispatch (sdpa/flash/sage/sage2)
├── arch/                 vendored per-family DiT modules (flux/, krea2/, ...)
├── text_encoders/        vendored text-encoder modules + bundled tokenizers + loader
├── vae/                   2D AE (Flux) + causal-3D VAEs (Wan2.1/Qwen, Wan2.2, LTX video/audio) + tiling
├── sampling/              the one flow-matching denoise loop + guidance strategies + step algorithms
├── memory/                VRAM-tier placement planning + per-component device assignment
├── lora/                  LoRA key-dialect mapping + runtime weight patching
└── errors.py              NativeEngineError family (dependency-free, importable from anywhere)
```

One line per package, in loading order:

| Package | Role |
|---|---|
| `io/` | `load_torch_file` (safetensors-only entry point) + prefix/shape/dtype introspection over a raw state dict. No model knowledge. |
| `detect/` | Structural sniffing: `detect_unet_config` / `detect_te_config` / `detect_vae_config` turn a state dict into a config dict; `detect/registry.py`'s `ModelSpec` list resolves a DiT config to a concrete arch class + sampling settings + TE/VAE pairing + key allowlists. |
| `ops/` | `disable_weight_init` (plain), `manual_cast` (cast-on-forward), `fp8_ops` (scaled-fp8-aware `Linear`) — the layer-class namespace every arch module is built from, and `pick_operations`/`pick_dtypes` to choose one from a checkpoint's storage dtype. |
| `base.py` | `NativeArchModule` (the `from_config`/`post_load` contract every arch subclasses) and `load_into_module`, the single load-integrity gate. |
| `attention.py` | One `attention(q, k, v)` call every arch module shares, dispatching to sdpa/flash/sage/sage2 by availability with a uniform `(B, H, L, D)` contract. |
| `arch/` | Vendored, empty-weight-constructible DiT modules, one subpackage per family (`arch/flux/`, `arch/krea2/`). |
| `text_encoders/` | Vendored TE modules (Qwen3, T5-XXL, CLIP-L, Qwen2.5-VL, Qwen3-VL, UMT5-XXL, Gemma3) + bundled tokenizer assets + `load_text_encoder`, the detect→build→integrity-load→wrap entry point. |
| `vae/` | `AutoEncoder2D` (Flux-family 2D VAE: `flux_ae`/`flux2_ae`) + `tiling.py` (its spatial tiled encode/decode, not yet 3D-aware); `AutoEncoderCausal3D` (Wan 2.1-shaped, 16ch — Qwen-Image's and Krea-2's VAE) and `AutoEncoderCausal3D_2_2` (Wan 2.2-shaped, 48ch, patchified) for the causal-3D family; `load_vae`/`load_causal3d_vae`/`load_causal3d_v2_vae` entry points. |
| `sampling/` | `denoise()` — the one loop — plus `flow_schedule.build_sigmas`, `cfg.py`'s guidance strategies (`EmbeddedGuidance`/`TrueCFG`/`NoCFG`), the step algorithms (`euler`/`dpmpp_2m`/`unipc`), and `hooks.py`'s per-step `StepHook` protocol. |
| `memory/` | `device_plan.make_device_plan` (which CUDA device each component lives on) and `tiering.plan_placement` (residency/ops-mode per component from VRAM + component sizes). |
| `lora/` | `key_mapping.map_lora_keys` (normalizes comfy/kohya/diffusers/PEFT LoRA key dialects onto native param names) and `apply.apply_loras`/`remove_loras` (in-place or cast-mode runtime patching). |

## The loading pipeline

This is the sequence every component (DiT, text encoder, VAE) goes through, and it is the same
sequence regardless of family — a new model adds entries to data structures along this path, it
does not add a new path.

```
load_torch_file(path)                         io/safetensors_loader.py
  -> (state_dict, metadata)

detect_*_config(state_dict)                    detect/unet_detect.py | te_detect.py | vae_detect.py
  -> config dict (structural signature + shape-derived hyperparams), or None

match_model_spec(config)                       detect/registry.py
  -> ModelSpec (arch class, sampling_settings, latent_format, clip_targets, vae_target,
                key allowlists)                 [raises NativeEngineUnsupportedError if nothing matches]

pick_dtypes(...) / pick_operations(...)        ops/dtype.py | vendor/gpl/comfyui/ops.py
  -> (storage_dtype, compute_dtype, ops_namespace)   [ops namespace picked from quant format]

arch_class.from_config(config, operations)     e.g. arch/flux/model.py:Flux.from_config
  -> empty-weight module, built under `with torch.device("meta"):`, every parameterised layer
     constructed from `operations.*` (never bare `torch.nn.*`)

load_into_module(module, state_dict, spec)     base.py
  -> module.load_state_dict(strict=False, assign=True)
  -> HARD ASSERT: missing keys ⊆ spec.expected_missing_keys (fnmatch globs)
                  unexpected keys ⊆ spec.expected_unexpected_keys
     else raise NativeEngineLoadIntegrityError naming the offending keys
  -> module.post_load()                         MANDATORY: recompute any derived buffer
                                                  (RoPE inv_freq, causal masks, ...) that
                                                  meta-device + assign-load leaves as garbage
  -> assert no parameter/buffer left on the meta device
  -> assert no NaN/Inf in a cheap sample of tensors

memory/device_plan.make_device_plan(...)       -> which device this component (and its siblings)
memory/tiering.plan_placement(...)                lives on and whether it stays resident
```

The DiT, each text encoder, and the VAE all go through this same `load_torch_file → detect →
ModelSpec/TESpec → from_config → load_into_module` path independently — see
`text_encoders/loader.py:_load_one` and `vae/loader.py:load_vae` for the TE/VAE instances of the
same shape, and `detect/registry.py` for the DiT's.

### Load integrity

`load_into_module`'s hard-assert step exists because of a specific, previously-shipped failure
mode: constructing a module with `torch.device("meta")` and then assign-loading real weights leaves
every *parameter* correct, but any *derived, non-persistent buffer* (RoPE frequency tables, causal
masks — anything computed in `__init__` rather than loaded from the checkpoint) is left as
uninitialized meta-device memory that silently becomes garbage once materialized. It doesn't crash;
it produces numerically-plausible-looking output that's subtly wrong. `post_load()` is therefore not
optional — `NativeArchModule` is an `ABC` that fails construction (not just at load time) if a
subclass omits it. Both shipped families (Flux, Krea-2) currently document an *intentional* no-op
here — see "The extension contract" below for why, and for what a real recompute implementation
would look like once one lands.

## Memory tiers & low-VRAM

The engine targets small GPUs (8–16GB) as first-class, not just the 24–32GB dev card. Placement is
**estimate-driven and fit-first**: `memory/tiering.plan_placement` decides per component (DiT / TE /
VAE) whether it stays GPU-resident from its *actual* size. Sampling reserves a latent-size-aware
activation allowance (`sampling_headroom_gb`); decode is a separate phase with its own larger,
resolution-aware spike estimate (`activation_headroom_gb`) and tiling policy. The VRAM tier table
below is only the *fallback* when component sizes are unknown; when they're known, two same-VRAM
cards get different plans for a 9B Klein vs a 14B Wan. Residency priority is DiT > TE > VAE (the
VAE is dropped to tiled/offloaded first, the TE next).

A component that does **not** fit resident is no longer streamed all-or-nothing. Two mechanisms
close that gap:

- **Partial layer residency** (`memory/partial.py`). As many leaf modules (Linear/Conv/norm) as fit
  a weights budget stay resident on the GPU; the remainder stream from **pinned** CPU RAM with
  `non_blocking=True` H2D copies, one layer at a time per forward, through the existing ops seam
  (`comfy_cast_weights` + `stream_non_blocking` on the streamed leaves — no arch or `denoise_loop`
  change). Embeddings and nvfp4 layers are always kept resident. This is ComfyUI's `model_patcher`
  lowvram approach, scoped to the native engine. `NativeGenerator._stream_dit_to_gpu` computes the
  budget (live free VRAM − sampling headroom, after evicting foreign residents) and streams only
  the overflow; an OOM backstop re-streams at a zero budget (only the small fixed tensors resident).
- **On-the-fly fp8 quantise-at-load** (`ops/fp8_quant.py`). When a bf16/fp16 DiT would not fit
  resident but *would* as fp8 (e.g. Krea-2 24.5GB → ~12.5GB), the loader quantises its big 2D Linear
  weights to per-tensor scaled e4m3 (`weight` fp8 + a `weight_scale` sidecar — the exact format
  `Fp8ScaledLinear` already consumes, so no new runtime path). Norms, embeddings, modulation
  projections and biases stay at original precision (the quality guard, matching real fp8
  checkpoints). Controlled by the loader's `fp8_quantize` knob (`auto` | `off` | `force`, default
  `auto`); `auto` only triggers in the "doesn't fit bf16, fits fp8" window. Prefer resident-fp8 over
  streamed-bf16: fp8 is both faster and higher PCIe throughput than streaming, at a small grid-level
  precision cost.

Tier fallback (sizes unknown) and the low-VRAM regime each tier lands in:

| DiT-device VRAM | Regime |
|---|---|
| **< 8 GB** | Partial residency (a handful of blocks resident, the rest streamed) + fp8-auto quantise-at-load when it lets more fit resident. VAE tiled, TE offloaded during sampling. |
| **8–12 GB** | Partial / mostly-resident DiT; fp8-auto for the bigger families. TE & VAE offloaded off the DiT phase. |
| **12–16 GB** | fp8-resident (auto) or bf16 partial residency for the 9–14B families; VAE offloaded, TE co-resident only when it fits. |
| **≥ 16 GB** | As before — fully resident where the fit test allows, phase-offloading TE/VAE only under pressure. The 32GB dev card is the luxury case. |

All of the above is unit-testable without a GPU: the fit math, the deterministic layer split, the
fp8 quant equivalence, and the eviction accounting are pure functions (`tests/core/native/memory/`,
`tests/core/native/test_fp8_quant.py`). The wall-clock A/B (streamed-bf16 vs partial-residency vs
fp8-auto, in s/step) is measured on GPU via `tests/manual/native_smoke.py --tier-vram <GB>`.

## The extension contract — adding model N+1

This is the section that matters most. Everything above is infrastructure; this is how you use it.
The invariant the whole design protects: **adding a new model family must never require editing
`sampling/denoise_loop.py`, `vendor/gpl/comfyui/ops.py`, `io/safetensors_loader.py`, or `memory/tiering.py`.**
Those four files are the shared core; a new model expresses itself entirely through data (a
`ModelSpec`) and a small number of new, additive files. Flux2/Klein and Krea-2 both landed without
touching any of the four.

Concretely, adding model family N+1 means:

1. **Detection signature** (`detect/unet_detect.py`). Pick a signature key that exists in the new
   family's state dict and in no other supported family's — Krea-2 used
   `txtfusion.projector.weight`, checked *before* the Flux signature so a Krea-2 checkpoint can
   never be misdetected as Flux (`_detect_krea2` short-circuits at the top of
   `detect_unet_config`). Every other hyperparameter comes from tensor shapes read off the same
   state dict (see `_detect_krea2` for the pattern: `sd["blocks.0.attn.wq.weight"].shape[1]` for
   `features`, `count_blocks(sd, "blocks.{}.")` for `layers`, etc.) — never hardcode a value that a
   checkpoint could tell you.

2. **`ModelSpec` registration** (`detect/registry.py`). One entry in `_VENDORED_SPECS`, which
   self-registers onto the `arch_registry` singleton (a provider shipping its own implementation
   of a family calls `arch_registry.register(spec, provider=..., priority=...)` at a higher
   priority to take the `(family, variant)` key over). A spec carries: `family`,
   `variant`, the `signature` dict that `matches()` checks against the detected config,
   `model_class` (a lazy `"module.path:ClassName"` string — registering a spec never imports the
   arch module), `sampling_settings` (drives the sigma schedule + guidance strategy —
   see `sampling/denoise_loop.py:_make_guidance`), `latent_format` (scale/shift factors — get the
   real numbers from ComfyUI's `latent_formats.py`, never guess them), `clip_targets` / `vae_target`
   (which TE(s)/VAE this family pairs with), and `expected_missing_keys` /
   `expected_unexpected_keys` (fnmatch globs for the load-integrity allowlist — quant sidecars like
   `*.weight_scale` go here so a non-fp8 load never trips on them).

3. **Arch module + `post_load`** (`arch/<family>/`). Vendor (or write) the module as a
   `NativeArchModule` subclass: `from_config(config, operations)` builds empty weights under
   `torch.device("meta")`, wiring every parameterised layer through `operations.*` (never bare
   `torch.nn.Linear`/`Conv2d`/etc. — that's the seam the ops layer's fp8/cast dispatch depends on).
   `post_load()` recomputes whatever the meta-device trick leaves stale — or, if the arch truly has
   none (both Flux and Krea-2 compute RoPE/timestep embeddings inline from `ids`/`t` every forward
   rather than caching them as buffers, so both currently have a documented no-op `post_load`),
   state that explicitly, the way both do; don't leave it a bare `pass` with no explanation, because
   the next reader can't tell "verified none" from "forgot to check". **Anima is the first family
   whose `post_load` does real recompute work**: its 3D-RoPE range tables
   (`VideoRopePosition3DEmb.dim_spatial_range` / `dim_temporal_range`) and its LLMAdapter
   `RotaryEmbedding.inv_freq` are `__init__`-computed non-persistent buffers, so meta construction
   leaves them on the meta device and `post_load` rebuilds all three from config
   (`arch/anima/model.py:Anima.post_load`). Flux/Krea-2/Z-Image compute RoPE inline per forward and
   stay documented no-ops.

4. **TE mapping.** If the family needs a text encoder not yet supported, add it under
   `text_encoders/` the same way (`detect/te_detect.py` signature + a `TESpec` in
   `text_encoders/loader.py:_SPECS` + the arch module + a `NativeTextEncoder` subclass documenting
   its output role keys — see `text_encoders/base.py`'s docstring for the role-key contract
   `context`/`pooled`/`attention_mask`). If it reuses an existing TE (Klein and Flux2 both use
   Qwen3), no TE work is needed — just point `clip_targets` at the existing name.

5. **VAE variant.** Same pattern for `vae/`: if the family's VAE has a different key layout or
   channel count than what `AutoEncoder2D` already handles, extend `detect/vae_detect.py`'s
   detection and, if the architecture genuinely differs (not just key naming), add a new module
   under `vae/`. A same-architecture-different-keys VAE (like `flux2_ae`'s diffusers-layout keys)
   gets a key-rename map instead of a new module — see `vae/key_convert.py`.

6. **Conditioning assembly.** However the family's TE output (`context`/`pooled`/`attention_mask`)
   maps onto the arch's `forward(x, timestep, context, y, guidance)` signature is the generator-side
   adapter's job, not the sampling core's — `sampling/denoise_loop.py`'s `model_forward` callable is
   exactly this adapter, and it's the one new piece of *code* (not data) each family typically needs.
   It stays outside `denoise_loop.py` itself; see the Wan dual-expert note in that file's docstring
   for the general pattern (wrap `model_forward`, don't branch inside the loop).

If a new family needs a genuinely new *guidance* shape (not covered by `EmbeddedGuidance`/
`TrueCFG`/`NoCFG`), add a class satisfying `cfg.GuidanceStrategy`'s `Protocol` and pick it in
`denoise_loop._make_guidance` by a new `sampling_settings["guidance"]` value — still data-driven,
still no branch on family name.

## Quantization support matrix

| Format | Storage | Status | Notes |
|---|---|---|---|
| bf16 / fp16 | native dtype | **Supported** | `disable_weight_init` (storage == compute) or `manual_cast` (storage != compute) namespace, picked by `ops/dtype.py:pick_dtypes`. |
| fp8-scaled (legacy `scale_weight`) | `float8_e4m3fn`/`e5m2` + one f32 scale per Linear | **Supported** | `fp8_ops.Linear` (`Fp8ScaledLinear`) dequantises on forward: `weight.to(compute) * weight_scale`. Detected via a `*.scale_weight` sidecar key or a top-level `scaled_fp8` marker tensor. |
| fp8-scaled (modern `weight_scale`/`input_scale`) | as above, per-tensor `weight_scale` + `input_scale` | **Supported** | Same `Fp8ScaledLinear`; `input_scale` is captured for a future native-fp8 matmul path but unused by the current dequant-on-forward path. Detected via a `_quantization_metadata` header entry or `*.weight_scale`/`*.input_scale` sidecar keys — `vendor/gpl/comfyui/ops.py:detect_quant_format`. Verified against the real `flux-2-klein-9b-fp8.safetensors` checkpoint. |
| nvfp4 | 4-bit packed U8 weight + fp8 block scale + f32 tensor scale (`*.weight_scale_2` sidecar) | **Supported** | `vendor/gpl/comfyui/ops.py:dequantize_nvfp4` + `Nvfp4Linear` (extends `Fp8ScaledLinear`): un-swizzles NVIDIA's `to_blocked` scale layout, dequantises `e2m1_LUT * unblock(scale) * weight_scale_2` per 16-element block. A checkpoint may be *mixed* fp8/nvfp4 across layers (the local `qwen_3_8b_fp8mixed` is one) — `pick_operations` routes the whole module to `fp8_ops` and each Linear independently detects its own format from which sidecar keys it has. Tested against a `Nvfp4Linear` load+forward-parity case and a mixed fp8/nvfp4/plain module (`tests/core/native/test_nvfp4.py`), and **verified end-to-end** via the real `qwen_3_8b_fp8mixed` TE in the GPU-validated Klein run (the 8B TE is exactly this mixed fp8/nvfp4 checkpoint). |
| GGUF | k-quant blocks | **Unsupported, explicit error** | `io/safetensors_loader.py:load_torch_file` rejects any non-`.safetensors`/`.sft` suffix by name, `GGUF / pickle checkpoints are not supported.` Deferred by design, not a bug. |
| Pickle (`.pt`/`.ckpt`) | — | **Unsupported, explicit error** | Same rejection path — the native engine never runs untrusted pickle. |

## Supported families

| Family | Variant | Signature key | Sampling | TE(s) | VAE | Exercised by (local files) |
|---|---|---|---|---|---|---|
| `flux` | `flux1` | `double_blocks.0.img_attn.norm.key_norm.scale` (no Flux2 marker) | Flux dynamic-mu shift (`base_shift=0.5`, `max_shift=1.15`), embedded guidance | T5-XXL + CLIP-L | `flux_ae` (16ch, ldm-layout) | No local Flux1 DiT checkpoint — components (T5-XXL/CLIP-L/`ae.sft`) present but untested end-to-end. |
| `flux` | `flux2` | `double_stream_modulation_img.lin.weight` | constant shift `2.02`, embedded guidance | Qwen3 (4B or 8B; Klein's TE) | `flux2_ae` (32ch, diffusers-layout, batchnorm-packed) | `flux2Klein_9b.safetensors` (+ fp8 variant `flux-2-klein-9b-fp8.safetensors`), `flux2-vae.safetensors`. **GPU-validated both precisions** (fp8: 1024²/20 steps/22s/28.7GB; bf16: passes after runtime phase-offloading, peak 28.55GB) — semantically-correct images. The local 8B Qwen3 TE (`qwen_3_8b_fp8mixed`, fp8 early layers + nvfp4 rest) is the one used and is verified end-to-end via this run. |
| `krea2` | `krea2_turbo` | `txtfusion.projector.weight` | resolution-dynamic shift (diffusers `calculate_shift`: `mu = slope*seq_len + intercept`), no CFG (turbo runs `guidance_scale=0`) | Qwen3-VL 4B (`text_encoders/loader.py`'s `qwen3vl` spec) | `qwen_image` (Wan-2.1-shaped causal 3D, `vae/causal_3d.py`) | `krea2TurboOfficialComfy_krea2TurboBf16.safetensors`, `models/vae/qwen_image_vae.safetensors`. Confirmed a **novel DiT architecture**, not a Flux variant — do not conflate with `flux2` despite both being MMDiT-shaped; forked from diffusers 0.39.0's `transformer_krea2.py` (Apache-2.0), not ComfyUI — see `arch/krea2/{model,layers}.py`'s provenance headers. **GPU-validated** (8-step turbo, 1024², semantically-perfect cabin image, peak 26GB, streaming manual_cast tier, T=1 causal decode); `model_loader/krea2` + `generator/krea2` + `content/presets/marketplace/Krea2/standard` landed. |
| `qwen_image` | `qwen_image` | (own DiT signature; `image_model: "qwen_image"`) | constant shift `1.15`, `cfg` guidance (true CFG -- sampler runs cond/uncond with the negative prompt) | Qwen2.5-VL-7B (`clip_targets=["qwen25_vl_7b"]`, `text_encoders/qwen25_vl.py`) | `qwen_image` (same Wan-2.1-shaped causal 3D VAE Krea-2 uses) | **GPU-validated end-to-end** (`qwen_image_2512_fp8_e4m3fn` bare-fp8 20GB DiT + `qwen_2.5_vl_7b_fp8_scaled` TE + `qwen_image_vae`, 1024²/20 steps true-CFG, photorealistic image, peak 19.37GB). 5D `(B,16,1,H//8,W//8)` latents through the causal-3D VAE (`decode_image`); pipes (`model_loader/qwen` + `generator/qwen`) + `content/presets/marketplace/QwenImage/standard` landed, old diffusers implementation deleted. |
| `wan` (2.2, t2v) | dual-expert | `head.modulation` | dual high/low-noise expert router (outside `denoise_loop.py`, per its docstring), `cfg` guidance, unipc | UMT5-XXL (`text_encoders/`, built) | Wan 2.1 causal 3D VAE (16ch, `vae/causal_3d.py`) for 14B; Wan 2.2 (48ch, `vae/causal_3d_v2.py`) for 5B ti2v | **GPU-validated — first native video** (dual-expert Remix fp8 pair + UMT5 + wan2.1 VAE: 33 frames 832×480, 15 unipc steps, peak 15.2GB, expert switch at sigma 0.875, temporally coherent). DiT + TE built; `model_loader/wan22` + `generator/txt2vid_wan22` + `content/presets/marketplace/Wan/standard` landed. i2v slice in progress. VAE pairing (16ch 14B / 48ch 5B) is by model variant, encoded in each `ModelSpec.vae_target`. |
| `ltxav` (LTX-2/2.3/2.5, audio-video) | — | `audio_adaln_single.linear.weight` present (video-only `ltxv` variant not locally testable -- every local checkpoint is AV) | RectifiedFlow, shift `2.37`, true-CFG | Gemma3-12B (2.0/2.3) or Gemma4-12B-with-proj (2.5, ported `Gemma4Unified*`, `text_encoders/gemma4.py`, detected before the Gemma3 branch since a flat file reuses Gemma3's `model.*` key names) + `embeddings_connector` | video: `ltx_causal_video` (`vae/ltx_causal_video.py`, config-JSON-driven `CausalVideoAutoencoder`, spatial+temporal tiling via thread-keyed streaming cache) -- **built and real-file verified** against both `LTX2_video_vae_bf16.safetensors` and `LTX23_video_vae_bf16.safetensors` (image + multi-frame roundtrips, `vae.`-prefix extraction from the all-in-one 19B checkpoint). audio: `ltx_audio` (`vae/ltx_audio.py`, decode-only `LTXAudioAutoencoder` + `LTXVocoder`/`LTXVocoderAMP`) -- **built and real-file verified for both LTX2 and LTX23**. LTX2 uses `LTXVocoder` (flat HiFi-GAN-v1 shape, plain LeakyReLU resblocks). LTX23 uses `LTXVocoderAMP` (nested `{"vocoder": {"resblock": "AMP1", "activation": "snakebeta"}, "bwe": {...}}` config -- an anti-aliased BigVGAN-style main stage, ported from the unrelated mmaudio family's `comfy/ldm/mmaudio/vae/bigvgan.py`+`activations.py`+`alias_free_torch.py`, real-key-verified with exact parity against `LTX23_audio_vae_bf16.safetensors`) -- **the main (16kHz-native) stage decodes end-to-end with a finite waveform**; the `bwe_generator` (48kHz bandwidth-extension) and `mel_stft` submodules are constructed for checkpoint key-parity only, `forward` isn't wired for them -- no local reference implementation exists for how that second stage actually consumes the first stage's output (task #36 follow-up). `LTXLatentUpsampler` (`vae/ltx_latent_upsampler.py`, multi-scale spatial/temporal latent upsampling) is **vendored, construction + load-integrity + tiny forward smoke tested only** -- no local checkpoint ships it (verified: the all-in-one 19B checkpoint's top-level prefixes are exactly `{model, vae, audio_vae, vocoder, text_embedding_projection}`, no `latent_upsampler.*`), so there's nothing to real-file-verify against yet; not pipeline-wired. | DiT (`arch/ltx/`) and TE (Gemma3-12B) modules exist (see the DiT/TE tasks); AV forward wiring + golden validation is a separate, still-open task -- this row covers VAE/vocoder readiness only. LTX-2.5 split-file support landed at the loader level (`model_loader/ltx/main.py`): transformer-only DiT, standalone video-VAE and audio-VAE(+vocoder) files, and a Gemma4 TE file that also carries `text_embedding_projection` (relocated off the DiT) -- see `docs/models/ltx.md` for the full file-layout table. 2.5 ships as its OWN preset, `content/presets/marketplace/LTX-2.5` (the 2.0/2.3 all-in-one preset `content/presets/marketplace/LTX-2` is unchanged and has no VAE pickers): its Models tab wires `video_vae` -- required, in both the `video` and `upscale` modes -- and `audio_vae` (optional, `video` only) to the loader's `vae`/`audio_model` config slots. |
| `z_image` | `z_image` | `cap_embedder.1.weight` present with `dim == 3840` (Lumina-Image-2.0 NextDiT; the `dim == 2304` original Lumina2 deliberately falls through to unsupported) | constant shift `3.0`, `cfg` guidance — ONE spec for turbo + base + finetunes; the preset drives `cfg_scale` (turbo `1.0` → `TrueCFG` skips the uncond pass = single forward; base/finetune ~`4.0`) and steps (turbo ~8, base ~30) | Qwen3-4B (`clip_targets=["z_image_qwen3"]`) — structurally identical to Klein's Qwen3-4B, so the loader passes `te_variant="zimage"` to select the Z-Image encode contract: the OUTPUT of the penultimate layer (`num_layers-2`), NO final norm, Z-Image template (no `<think>`), min_length 1 (`ZImageTextEncoder` in `text_encoders/qwen3.py`) | `flux_ae` (Flux-style 2D AE, 16ch ldm layout, `vae/ae_2d.py`) with ComfyUI's **Flux** latent format (`scale_factor 0.3611`, `shift_factor 0.1159`), 2D `(B,16,H//8,W//8)` | **CPU-validated; GPU e2e pending.** Detection + real-checkpoint DiT load-integrity (`zImage_turbo` + `cyberrealisticZImage_v30` finetune, ~11.5GB bf16), tiny-config arch forward, real TE encode (context `[B,S,2560]`) and real VAE decode all pass. NextDiT (`arch/z_image/`) reuses flux `EmbedND`/`apply_rope`; returns the ComfyUI `-img` velocity sign; caption padded with the learned `cap_pad_token` and the DiT ignores the attention mask (`cap_mask=None`). Pipes (`model_loader/z_image` + `generator/z_image`) + `content/presets/marketplace/ZImage/standard` landed. GPU run: `tests/manual/native_smoke.py --te-variant zimage`. |
| `anima` | `anima` | `llm_adapter.blocks.0.cross_attn.q_proj.weight` present (NVIDIA Cosmos-Predict2 `MiniTrainDIT` + an in-model `LLMAdapter`; the plain Cosmos-Predict2 backbone with no adapter is not supported) | `ModelType.FLOW` → CONST velocity + `ModelSamplingDiscreteFlow` (shift `3.0`, `timestep(sigma)=sigma*1000`, so the DiT scales sigma×1000 itself — unlike Flux), true CFG | Qwen3-**0.6B** (`clip_targets=["qwen3_06b"]`, detected on hidden **== 1024**). Its conditioning is two-part: the Qwen3 last-hidden `context` PLUS `t5xxl_ids`/`t5xxl_weights` (a T5 tokenization). The DiT's in-model `LLMAdapter` embeds the T5 ids (its OWN `Embedding(32128,1024)` — there is no T5 *model*, only the bundled tokenizer) and cross-attends them to `context` to build the real cross-attention context; the generator threads the two T5 tensors through a custom `model_forward` (`AnimaNativeGenerator`). `AnimaTextEncoder` (`text_encoders/anima.py`) takes the LAST decoder layer's output, no final norm; A1111 emphasis rides on the T5 side. | Wan-2.1 causal-3D VAE (16ch, `vae/causal_3d.py`, `vae_target="qwen_image"`), Wan21 latent format, 5D `(B,16,1,H//8,W//8)` | **GPU-validated end-to-end** (RTX 5090: `anima_aestheticV10b` bf16 DiT + `qwen_3_06b_base` TE + `qwen_image_vae`, 1024²/24 steps/euler/cfg 6.0 → semantically-correct anime kitsune; ~8s sample, peak 20.7GB at the causal-3D fp32 decode). Two ComfyUI-convention traps caught during the GPU run: (1) Anima's `sampling_settings["multiplier"] == 1.0` overrides `ModelSamplingDiscreteFlow`'s default 1000, so `timestep(sigma)==sigma` — the DiT takes RAW sigma and must NOT bake an x1000 into its `Timesteps` embedding (unlike Flux/Qwen); (2) the Qwen3-0.6B context is the last hidden **through the model's final RMS norm** (`SDClipModel` `layer="last"` returns the normed `outputs[0]`; `layer_norm_hidden_state=False` only affects the intermediate path). The TE is the **base** (non-instruct) Qwen3-0.6B (Anima applies no chat template). Detection, tiny-config arch forward + the NOVEL `post_load` recompute (3D-RoPE ranges + LLMAdapter `inv_freq`), and the full generator path (custom `model_forward`, true CFG) all CPU-tested too. Pipes (`model_loader/anima` + `generator/anima`) + `content/presets/marketplace/Anima/standard` landed. |
| `minimax_h3` | `h3` | `video_patch_proj.` + `audio_patch_proj.` + `condition_proj.` + the fused `blocks.0.attn.qkv_proj.` key set. ONE spec covers both checkpoint shapes (pruned 20B: `adaln_t_table` lookup + rank-8 AdaLN factorization, no time embedder; full 33B: time-embedder MLP + full per-block projections). `fl2va` and `ref2va` are byte-identical in keys and config — **detection cannot tell them apart**, only the picked file decides | Rectified-flow Euler with a REVERSED velocity sign and the timestep fed as `1 - sigma`. TWO independent sigma schedules (video shift `12.0`, audio shift `3.0`) advance together at the same step count inside ONE packed-sequence forward per step — the per-row timestep vector is what makes one call serve both, so the shared `denoise()` loop does not fit and the pipe owns a bespoke loop. `guidance: "none"` (distilled): no CFG, no negative prompt anywhere | Qwen3-VL-32B trimmed to 50 decoder layers (`MiniMaxH3TextEncoder` in `text_encoders/qwen3.py`, `variant="qwen3vl_32b"`): raw prompt, no chat template, no system prompt, `add_special_tokens=False`, output tapped after layer 49 (no final norm, no LM head in the repack). Loaded vision-enabled unconditionally — `fl2va` keyframes enter the same sequence as `<Picture i>: ` vision blocks | TWO separate files, both required: `minimax_h3_video` (24ch, 16× spatial and 17-pixel-frames-to-5-latents temporal, causal-conv encoder + ViT decoder, spatial tiling on by default) and `minimax_h3_audio` (DAC encoder + BigVGAN decoder, mono at 32 kHz with stereo carried as batch 2, **fp32 compute always** — bf16 costs ~20 dB) | **GPU validation pending; no local weight files.** Arch, detection, both VAEs, the TE and the pipes (`model_loader/minimax_h3` + `generator/video_minimax_h3`) are built and CPU-tested; `content/presets/marketplace/MiniMax-H3` landed with a `video` mode covering `t2va` + `fl2va` (`ref2va` is not wired). Two family-specific traps: pixel denormalization is **ImageNet mean/std, not `[-1,1]`** — unlike every other family in this table — and `place_dit_for_sequence` must be sized off the attention inner dim (56×128 = 7168), which is WIDER than the 5376 residual stream. Weights are under the MiniMax H3 Community License (EU/UK/US/KR excluded, outputs included) — see `docs/models/minimax_h3.md` |
| `minimax_music3` | `music3` | `cond_layer_logits` + `latent_conditioners.0.weight` + the fused `transformer.layers.0.self_attn.to_qkv.weight` on the DiT; the fused TE is keyed separately on `model.audio_decoder.layers.0.*` + the embedded `tokenizer_json`, with FIVE independent layout booleans recorded (pruned vs. full embeddings/lm_head, LLM `qkv_proj`/`gate_up_proj` fusion, the depth decoder's own merged-qkv/merged-mlp flags) rather than assumed to move together | Two-stage, no shared `denoise()` loop: an autoregressive stage (KV-cached Qwen3-8B global LLM + 4-layer RVQ depth decoder, `arch/minimax_music3/{lm,ar_loop}.py`) samples per-frame semantic + 7 residual codes at 25 fps (`ar_cfg` 1.5, top-k 50, no temperature), producing `frame_hiddens`; a windowed flow-matching DiT (`arch/minimax_music3/{model,flow}.py`, 200-frame windows/100-frame hop, per-step overlap pinning, inverted time convention `t=0`=noise) denoises those into a 128ch latent, `cfg` 1.7 with a ZEROED condition tensor as the unconditional branch (not a second AR pass) | No live text encoder in the flow-stage sense — the fused TE IS the AR core: a KV-cached Qwen3-8B LLM + depth decoder whose own hidden states (post-final-norm) become the DiT's conditioning directly, never an encoded prompt (`MiniMaxMusic3AudioLM`, `text_encoders`-adjacent but NOT `text_encoders/loader.py`'s contract — see `model_loader/minimax_music3/te_loader.py`'s module docstring) | `minimax_music3_dav` (`vae/minimax_music3_dav.py`, decode-only DAC-style vocoder, `weight_g`/`weight_v` folded at load — NOT pre-folded like the H3 audio repack — fp32 compute always, hop 512 → 44.1 kHz stereo, chunked/tiled decode above a latent-length threshold) | **GPU validation pending; no local weight files.** Arch, detection, the vocoder, the fused TE and the pipes (`model_loader/minimax_music3` + `generator/audio_minimax_music3`) are built and CPU-tested; `content/presets/marketplace/MiniMax-Music3` landed with `song` + `instrumental` modes. Text-to-music only — no audio encoder ships, so no style/reference/extend/repaint mode exists for this family. Two open precision questions pending real-weight validation: per-window `latent_length` floors each window's own frame count independently rather than from one global cumulative count, and the AR-stage hidden state is read post-final-RMSNorm for both the LM head and the DiT conditioning, an assumption rather than a directly-confirmed reference match. Weights are under the MiniMax Music3 Community License with **no territorial exclusions** (unlike MiniMax-H3) — see `docs/models/minimax_music3.md` |

> **Krea-2's VAE has a real numerical subtlety, not a missing piece anymore.** The
> `krea2_turbo` spec's `latent_format` carries per-channel `latents_mean`/`latents_std`
> (ComfyUI's `Wan21` latent format) that `vae/causal_3d.py` also exports as
> `LATENTS_MEAN`/`LATENTS_STD` -- keep both in sync if either changes. The **Wan 2.2** VAE has no
> such per-channel constants to port: ComfyUI's `Wan22` latent-format class inherits `Wan21`'s
> `process_in`/`process_out` (which read `self.latents_mean`/`self.latents_std`) but its own
> `__init__` never sets them -- verified by reading the class, not assumed. Wan 2.2 normalization
> is plain `scale_factor=1.0` (a no-op), nothing per-channel.

## Verification approach

- **Key-parity fixtures.** `tests/core/native/text_encoders/test_key_parity.py` and the VAE/arch
  tests build tiny random-weight state dicts using the *real* key names (not full checkpoints) and
  run them through the full detect → build → `load_into_module` path, asserting zero missing/
  unexpected keys outside the declared allowlist. This is the cheap, CI-friendly form of load-
  integrity verification — it catches a key-naming drift between the detector, the `ModelSpec`
  allowlist, and the arch module without needing gigabytes of real weights.
- **Real-file smoke tests**, gated with `@pytest.mark.skipif(not Path(...).exists())` so the suite
  stays green on a machine without the model files: load the real local checkpoint (CPU is fine —
  these are small enough, e.g. the Flux VAE), run a tiny forward pass (e.g. a 64×64 encode/decode
  roundtrip), assert finite output and the expected shape. See
  `tests/core/native/vae/test_ae_2d.py` for the pattern.
- **Load-integrity asserts are themselves the test for meta-device correctness** — `base.py`'s
  no-meta / no-NaN checks run on every load, real or synthetic, so a `post_load()` bug that leaves a
  buffer uninitialized fails loudly in any test that constructs the module, not just a dedicated one.
- **Golden comparison vs ComfyUI** (same checkpoint/seed/sampler/steps, diffing intermediate latents
  at step 0/mid/final to localize a divergence to load vs. sample vs. VAE) is **planned, not yet
  built** — the plan calls for cached golden `.pt` fixtures run manually, not in CI, since it needs
  the real multi-GB checkpoints and a working ComfyUI install to generate the fixtures against.
- **Detection near-miss rejection** — `test_registry.py` / `test_unet_detect.py` /
  `test_te_detect.py` include cases designed to *not* match (e.g. a Flux-shaped signature without
  `img_in.weight`, which is deliberately excluded because Chroma reuses the double-block layout) so
  a loosely-written signature doesn't silently misdetect an unrelated checkpoint.

## See also

- [Backends and Engines](backends.md) — `native` is one of the two registered engines; this doc is
  about what happens *inside* that engine's process, not how it's selected or configured.
- [Providers](providers.md) — unrelated subsystem (marketplace credentials), same "core never
  hardcodes a specific implementation" philosophy this engine's `ModelSpec` registry follows.
- [Native Engine Optimizations](native-optimizations.md) — guidance-stack corrections (CFG-Zero*,
  APG, SLG), NAG, RIFLEx, the sampler/schedule menu, FBCache, temporal-chunked VAE decode, prompt-
  embedding caching, new attention backends, and the fp8 GEMM fast path layered on top of this base.
