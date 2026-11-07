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
loading, detection, kernels (hardware snapshot, attention dispatch, cast/fp8-scaled/nvfp4 layers), VAE (2D + causal-3D), sampling, LoRA,
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
├── io/                 safetensors loading + state-dict introspection + quant-format descriptors and dequantisers
├── detect/              structural checkpoint detection (DiT / text-encoder / VAE) + ModelSpec registry
├── ops/                 dtype selection + quantise-at-load + small tensor helpers + offline model tools
├── base.py               NativeArchModule contract + the load-integrity gate
├── kernels/               the one door to hardware: capability snapshot, GEMM support, fast-path predicate, attention dispatch,
│                          cast/fp8-scaled/int8/nvfp4 layer classes, op selection, runtime LoRA math
├── arch/                 vendored per-family DiT modules (flux/, krea2/, ...)
├── text_encoders/        vendored text-encoder modules + bundled tokenizers + loader
├── vae/                   2D AE (Flux) + causal-3D VAEs (Wan2.1/Qwen, Wan2.2, LTX video/audio) + tiling
├── sampling/              the one flow-matching denoise loop + guidance strategies + step algorithms
├── memory/                VRAM-tier placement planning + per-component device assignment
├── lora/                  LoRA key-dialect mapping + runtime weight patching
├── cancellation.py        the cancel probe of a run, read between DiT blocks and VAE tiles
└── errors.py              NativeEngineError family (dependency-free, importable from anywhere)
```

One line per package, in loading order:

| Package | Role |
|---|---|
| `io/` | `load_torch_file` (safetensors-only entry point) + prefix/shape/dtype introspection over a raw state dict, and `quant_formats.py`: `comfy_quant` descriptor parsing and gating, the ConvRot Hadamard and un-rotation, the nvfp4 dequantiser and activation quantiser, and the single definitions of the format constants (`FP8_DTYPES`, `E4M3_MAX`, `NVFP4_BLOCK`). No model knowledge. |
| `detect/` | Structural sniffing: `detect_unet_config` / `detect_te_config` / `detect_vae_config` turn a state dict into a config dict; `detect/registry.py`'s `ModelSpec` list resolves a DiT config to a concrete arch class + sampling settings + TE/VAE pairing + key allowlists. |
| `ops/` | `pick_dtypes` (storage and compute dtype from a checkpoint's dtype and the device capability), `fp8_quant.py` (quantise-at-load), `embedding.py`/`padding.py` tensor helpers, and `model_tools/` (the offline quantise/bake/merge tools behind the model cards). The layer classes themselves live in `kernels/`. |
| `base.py` | `NativeArchModule` (the `from_config`/`post_load` contract every arch subclasses) and `load_into_module`, the single load-integrity gate. |
| `kernels/` | Everything hardware-dependent, behind one public surface (`kernels/__init__.py`): see "The kernel layer" below. |
| `arch/` | Vendored, empty-weight-constructible DiT modules, one subpackage per family (`arch/flux/`, `arch/krea2/`). |
| `text_encoders/` | Vendored TE modules (Qwen3, T5-XXL, CLIP-L, Qwen2.5-VL, Qwen3-VL, UMT5-XXL, Gemma3) + bundled tokenizer assets + `load_text_encoder`, the detect→build→integrity-load→wrap entry point. |
| `vae/` | `AutoEncoder2D` (Flux-family 2D VAE: `flux_ae`/`flux2_ae`) + `tiling.py` (its spatial tiled encode/decode, not yet 3D-aware); `AutoEncoderCausal3D` (Wan 2.1-shaped, 16ch — Qwen-Image's and Krea-2's VAE) and `AutoEncoderCausal3D_2_2` (Wan 2.2-shaped, 48ch, patchified) for the causal-3D family; `load_vae`/`load_causal3d_vae`/`load_causal3d_v2_vae` entry points. |
| `sampling/` | `denoise()` — the one loop — plus `flow_schedule.build_sigmas`, `cfg.py`'s guidance strategies (`EmbeddedGuidance`/`TrueCFG`/`NoCFG`), the step algorithms (`euler`/`dpmpp_2m`/`unipc`), and `hooks.py`'s per-step `StepHook` protocol. The algorithms and the schedules are both registries (`registry.py`): core registers its own at import time, a plugin adds more through the `samplers:` / `schedules:` manifest roots, and `denoise()`/`build_sigmas()` dispatch through them — there is no other list of sampler or schedule names. |
| `memory/` | `device_plan.make_device_plan` (which CUDA device each component lives on), `tiering.plan_placement` (residency/ops-mode per component from VRAM + component sizes), and `text_encoder.py` with `tiering.plan_text_encoder_placement` (whole move or partial residency for the prompt encode). |
| `cancellation.py` | `cancellation_scope` puts a run's cancel probe in a `ContextVar`: `GenerationEngine` opens one around each pipe, and `denoise()`, `denoise_prenoised()` and the samplers that own their step loop open one that also knows the step. `raise_if_cancelled(i)` raises `SamplingCancelled` when the probe says so and does nothing outside a scope. |
| `lora/` | `key_mapping.map_lora_keys` (normalizes comfy/kohya/diffusers/PEFT LoRA key dialects onto native param names) and `apply.apply_loras`/`remove_loras` (in-place or cast-mode runtime patching). |

### The kernel layer

`kernels/` owns every decision that depends on the GPU in front of the process. Code in `src/` outside it
asks a question and gets an answer; it never probes a device, never picks an attention backend and never
reads a GEMM capability itself.

| Module | Public names | Role |
|---|---|---|
| `hardware.py` | `capabilities()`, `refresh_capabilities()`, `Capabilities`, `DeviceCapabilities`, `GpuGeneration`, the backend names `SDPA`/`FLASH`/`SAGE`/`SAGE2`/`SAGE3`/`SPARGE` | A per-process snapshot of every visible GPU (compute capability, name, generation, total VRAM, vendor `cuda`/`hip`, which attention backends are importable and eligible), built lazily once, allocation-free, cached. `Capabilities.device(index)` returns the entry for one device; an unknown or CUDA-less device answers with a CPU entry of capability `(0, 0)`, and a probe that fails never raises. `refresh_capabilities()` rebuilds it after an in-process install of an attention package. The only place in `src/` that calls `torch.cuda.get_device_capability`; the vendored `sol_attn` keeps its own probe because `vendor/` cannot import `src`. |
| `gemm.py` | `gemm_support(device)`, `GemmSupport` | Whether `torch._scaled_mm` (fp8) and `torch._scaled_mm_v2` (nvfp4) work on a device, with a reason string when not. Probed lazily per device and cached; the nvfp4 trial GEMM runs only on the first ask. The live flags `native_fp8_matmul` / `native_nvfp4_matmul` are not part of it. |
| `linear.py` | `fast_path(layer)`, `quant_format(layer)`, `FastPath`, `FastPathKind`, `QuantFormat` | The static answer to "which path will this quantised linear take": the layer's format, `gemm_support` for its device and the live flags combined into a kind (`fp8_scaled_mm` / `nvfp4_gemm` / `dequant`), whether runtime LoRA output branches are fused, and the reason when the fast kind is not taken. `None` for layers that are not quantised linears. Value-dependent conditions (zero, NaN or negative scales) are outside the static answer. |
| `dispatch.py` | `attention(q, k, v, ...)`, `available_backends`, `get_attention_backend`, `get_backend_override`, `known_backends`, `reset_backend_cache`, `BACKEND_PRIORITY` | The one attention call every arch module shares: a uniform `(B, H, L, D)` contract over sdpa/flash/sage/sage2/sage3 (sparge by pin only), picked by availability from the snapshot, with grouped-KV and mask handling, an optional softmax `scale` forwarded to every backend that takes one, and a per-backend `head_dim` gate (sage/sage2/sage3 up to 128, flash up to 256, sparge 64 or 128). A call a backend cannot serve (mask, dtype, head_dim, or a `scale` on sage3) runs on sdpa inside the dispatcher and is logged once. |
| `casting.py` | `disable_weight_init`, `manual_cast`, `CastWeightBiasOp`, `cast_to` | The plain and cast-on-forward layer namespaces every arch module is built from. |
| `fp8_linear.py`, `nvfp4_linear.py` | `Fp8ScaledLinear` (and `Nvfp4Linear`, reached as `fp8_ops.Linear`) | The scaled-fp8, int8 and ConvRot layer, and the nvfp4 layer, with their dequant-on-forward and GEMM forwards. |
| `operations.py` | `pick_operations`, `detect_quant_format`, `fp8_ops`, `QUANT_FP8_SCALED` | Chooses the layer namespace from a checkpoint's storage dtype and quant format. |
| `lora_math.py` | `partition_output_branch_deltas`, `invalidate_lora_fused_cache`, `log_lora_path_summary` | The runtime LoRA delta math the layers apply on forward. |
| `fused_elementwise.py`, `fused_elementwise_triton.py` | `norm_modulate`, `gated_residual`, `act_gate`, `ActGate`, `norm_args`, `fused_elementwise_disabled_reason` (also on every `operations` namespace) | The adaLN norm + modulate, gated residual and FFN activation every DiT family calls. With `native_fused_elementwise` off, or on a machine, dtype or layout the Triton kernels do not take, they run the per-family eager code bit for bit. With it on, a CUDA bf16/fp16 call runs one Triton kernel (fp32 math, one rounding), registered with `torch.library.triton_op` so `torch.compile` traces through it; a kernel that fails to load turns the path off for the process. |

Live runtime flags (`native_fp8_matmul`, `native_nvfp4_matmul`, `native_fused_elementwise`, `native_lora_fused`,
`native_attention_backend`) are read at call time, never frozen into the snapshot. Tests fake the
hardware with the `install_capabilities` fixture (`tests/fixtures/kernel_capabilities.py`) instead of a GPU.

`tests/architecture/test_kernel_boundary.py` holds the boundary as plain rules:

- Nothing in `src/` or `vendor/` references `vendor.gpl.comfyui.ops`; the file stays deleted. The
  GPL-derived layer code now lives in `kernels/{casting,fp8_linear,nvfp4_linear,operations}.py` and
  `io/quant_formats.py` (see `vendor/NOTICE.md`).
- `get_device_capability` is referenced in `src/` only inside `kernels/`; everything else in `src/` reads the
  snapshot. The vendored `sol_attn` keeps its own probe because `vendor/` cannot import `src`.
- `scaled_dot_product_attention` is called directly outside `kernels/` only from the files in
  `DIRECT_ATTENTION_ALLOWLIST`, each with a written reason (a once-per-prompt, once-per-image or
  once-per-decode call where the dispatcher buys nothing). An entry whose file stops calling it
  fails as stale.

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

pick_dtypes(...) / pick_operations(...)        ops/dtype.py | kernels/operations.py
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

Auxiliary components (VAE, audio VAE, vocoder, latent upscaler, duration head) share one engine
skeleton, `_load_component`: read the file (only the component's prefixed keys when it lives inside an
all-in-one checkpoint, so the DiT is never materialised), slice, size and pick ops from that slice,
then hand the already-read state dict to the family builder. Bare-key VAE files (Flux, Wan, Qwen-Image,
SeedVR2) are read exactly once; `vae.loader.build_native_vae` detects the family from that state dict
and does not touch the file again.

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
  change). Embeddings are always kept resident; nvfp4 layers too, except under the text encoder's
  partial residency below, which opts in to streaming them. This is ComfyUI's `model_patcher`
  lowvram approach, scoped to the native engine. `NativeGenerator._stream_dit_to_gpu` computes the
  budget (live free VRAM − sampling headroom, after evicting foreign residents) and streams only
  the overflow; an OOM backstop re-streams at a zero budget (only the small fixed tensors resident).
  The streamed weights are pinned through one **pinned arena** per placement
  (`memory/pinned_arena.py`): plain host memory registered with `cudaHostRegister` in segments of
  at most 2 GB, each weight a 4 KiB-aligned view into it. Calling `pin_memory()` per weight instead
  goes through PyTorch's caching host allocator, which rounds every block up to the next power of
  two — for a fully streamed MiniMax-H3 fp8 DiT that is 30.6 GB pinned for 19.5 GB of weights
  (computed from the layer shapes; LTX's power-of-two widths lose almost nothing). Teardown copies
  each segment's weights back to plain RAM and unregisters the segment as soon as it is empty, so
  offloading holds at most one segment twice. If registration fails, streaming falls back to
  per-weight `pin_memory()`. Pinning stays best effort past that: when the host refuses to
  page-lock a streamed leaf even per weight (`pin_memory()` raises, as it can on the capped
  page-locked memory of WSL2 / Docker Desktop), `ModuleStreamer.apply` repeats the placement
  unpinned (`pin=False`, synchronous copies, no prefetch), releases the pinned memory it had taken
  and logs one warning per process. The streamed forward is slower but the generation does not
  fail, for the DiT and the text encoder alike. Any other failure mid-placement is rolled back
  completely (hooks, cast flags, pinned tensors). Teardown of a pinned placement empties the CUDA
  cache before it frees its first pinned block: such a block records a CUDA event when it is freed,
  and a failure there cannot be caught from Python.
  A streamer can also **park** its module instead of tearing it down (`ModuleStreamer.park`): the
  streamed weights stay in their arena, the resident ones are copied from the GPU into a new arena
  (per weight when no segment can be registered) and the allocator's cache is handed back to the
  driver. The next placement of a parked module registers and pins nothing, and each resident
  weight keeps its page-locked copy while it runs on the GPU (`release_unused(keep=...)`), so the
  next park copies it back into a segment that is already registered: only the first park of a
  module registers new segments, and a weight whose GPU copy changed shape or dtype is pinned
  again. `NativeModel.park` parks only a pinned placement, and only while free host RAM covers
  the resident weights that have no such copy plus the streaming reserve;
  a parked model streamed again skips the host-RAM guard, since its weights are already
  page-locked. A full move or an offload unparks it (`unpark`: every weight back to pageable
  memory, every segment unregistered), an eviction drops its arenas (`forget_parked`), and a
  placement that fails is rolled back like any other, leaving nothing pinned. When the host
  refuses to page-lock a weight while parking, the module is torn down as before. Wan 2.2's expert
  router parks the idle high-noise expert at the expert switch (native backend setting
  `native_park_experts`, on by default; see [Wan 2.2](models/wan.md)).
- **On-the-fly fp8 quantise-at-load** (`ops/fp8_quant.py`). When a bf16/fp16 DiT would not fit
  resident but *would* as fp8 (e.g. Krea-2 24.5GB → ~12.5GB), the loader quantises its big 2D Linear
  weights to per-tensor scaled e4m3 (`weight` fp8 + a `weight_scale` sidecar — the exact format
  `Fp8ScaledLinear` already consumes, so no new runtime path). Norms, embeddings, modulation
  projections and biases stay at original precision (the quality guard, matching real fp8
  checkpoints). Controlled by the loader's `fp8_quantize` knob (`auto` | `off` | `force`, default
  `auto`); `auto` only triggers in the "doesn't fit bf16, fits fp8" window. Prefer resident-fp8 over
  streamed-bf16: fp8 is both faster and higher PCIe throughput than streaming, at a small grid-level
  precision cost.
- **DiT activation reserve** (`estimate_activation_reserve_gb`, `pipes/_shared/generation/dit_placement.py`).
  The pipes that run their own denoise loop (LTX, MiniMax-H3) budget the DiT's resident weights against
  what one sampling forward allocates on top of them: `budget = free VRAM - reserve`, and a DiT over
  that budget streams the rest. A short clip on a card that holds the DiT and its reserve stays fully
  resident, the DiT moved whole. The weight is `estimated_vram_gb` plus the runtime LoRA deltas still
  off the card plus an nvfp4 DiT's scale tables: `estimated_vram_gb` is the checkpoint's file size and
  cannot see a LoRA (a real H3 turbo-LoRA OOM: ~1.4 GB of bf16 deltas the old budget never saw), and a
  delta already on the card is out of the free reading, so adding it would count it twice. Every fit
  check reads `effective_free_vram_gb`. Eviction reads the raw `free_vram_gb`, so a cold placement,
  or a warm DiT that no longer fits once it has offloaded, hands the idle pool back before it decides
  what to evict. The last budget of the OOM ladder in `_move_resident` reads it too.

  **Formula**, with `S` tokens, `V` of them video: `reserve = max(0.5 GB, 1.15 × [S × attention + S ×
  (lora buffer + quant + gelu) + V × rows × inner × 2 B + lora weight side + transient])`, where
  `transient` is `dequant` on a GEMM fast path (fp8 or nvfp4) and `max(dequant, S × gelu)` everywhere else.
  `attention` is 90,112 B per token at LTX's inner dim 4096; H3 adds its FFN, or takes the larger of
  attention and its chunked-MLP phase. `dequant = 2 × max(out × in) × 2 B` is flat: a quantized Linear
  on the `DEQUANT` path holds two compute-dtype copies of its weight while it scales it, one layer at
  a time, so only the widest counts, ≈ 0.25 GiB for LTX's FFN, ≈ 0.57 GiB for `fc1` on a pruned H3 and
  ≈ 0.97 GiB for `adaln_proj.linear` on the full int8 file. int8
  always takes that path, fp8 whenever the GEMM kernel is off or refuses it. An nvfp4 Linear is left
  out: its scale tables go into the weight, but its forward still materialises the weight, and
  nothing reserves that transient (≈ 0.66 GiB for LTX's FFN).
  `gelu` and `rows` are LTX's (`ltx_reserve_terms`, `pipes/_shared/video/ltx_common.py`, which reads them
  from the loaded DiT's config). `gelu = 4 × inner × 2 B`, 16,384 × 2 B on the 19B and 22B shapes,
  is the GELU output, alive beside `fc2`'s dequantised weight; past about 8,192 tokens `fc1`'s
  output beside that GELU outgrows the two weight copies, hence the `max`. Only this term follows the
  config: `attention` and `rows × inner` keep the 4096 default of `inner_dim`, which the LTX pipes do
  not pass. On the fp8 GEMM path `fc1`'s
  output never coexists with the quantiser's buffers, so the `max` drops out. `rows` is what a block
  keeps as locals until it returns under a per-token timestep (`video_ltx`, `dfr_video_ltx`): 7 adaLN
  values with prompt adaLN, 5 without. A scalar timestep (`txt2vid_ltx`, the LTX detailer) broadcasts
  them, so 0. `video_ltx` adds two cases. Generated audio keeps `vx_norm3`, 1 row, and on a DiT whose
  AV-cross adaLN follows its own timestep (`use_cross_timestep` off, LTX-2 19B) the 5 values of that
  adaLN. A reference at a reduced scale gives a timestep that is no whole number of frames, so
  `CompressedTimestep` keeps its whole table for the forward: the adaLN coefficients plus the embedded
  timestep, 10 rows with prompt adaLN and 7 without, plus the 5 AV-cross rows of that same LTX-2 19B.

  **Against the forward.** `test_the_ltx_reserve_covers_the_forward_peak_with_room_for_the_allocator`
  runs real LTX-2.5-shaped int8 ConvRot blocks on the meta device, attention kernel stubbed, and adds
  up the storages they create. The peak sits at `self.ff` and is linear past 28k tokens, ≈ 201 KB per
  token, 144 KB with a scalar timestep. In GiB:

  | `S` | Peak, scalar | Reserve, scalar | Peak, per-token | Reserve, per-token |
  |---:|---:|---:|---:|---:|
  | 3,080 | 0.59 | 0.69 | 0.75 | 0.88 |
  | 14,080 | 1.90 | 2.35 | 2.66 | 3.21 |
  | 110,880 | 14.86 | 18.48 | 20.79 | 25.29 |
  | 248,880 | 33.32 | 41.49 | 46.62 | 56.77 |

  The ~9.4 GB of the attention calibration in `dit_placement.py`, a measured 40 s 720x1280 LTX-2.3 OOM,
  is what that run read when it died, and its four terms cover the attention phase only. It sits under
  these figures: at S = 110,880 the same blocks reach 9.77 GiB at the first self-attention, 12.32 GiB in later attentions
  and 14.86 GiB at the FFN. That fits a forward that stopped in attention, though nothing recorded
  where it stopped. These are logical bytes, what the forward asks for:
  fragmentation and cuDNN or cuBLAS workspace are what the 1.15 covers, and one 12 GB card has checked
  only the per-token figures of `video_ltx`, from S = 1,560 to 7,800. Generated audio and a reduced
  reference hold the same room on LTX-2.5 and on an LTX-2 19B shape, about 1.2 × the peak, except the
  two together on that 19B shape, about 1.08 ×. A tiny sequence sits just above the floor, about 0.67 GiB
  at 4,000 tokens on a bf16 DiT and 0.81 GiB with the int8 transient, so the LTX detailer's DiT goes
  resident and the tube passes its own pixel-sized VAE decode as `reserve_gb`.

  **The fit hint.** Above its floor the reserve is linear in tokens once a quantized DiT's flat dequant
  transient is set aside, so MiniMax-H3's `_fit_hint` inverts that ratio from the numbers the failed
  placement already computed: `scale = (free - extra - flat) / (reserve - flat)`.

  **The clip planner.** `sequence_fit` charges the same reserve, on every card, so `video_ltx` plans what
  placement will budget for, and counts foreign residents as room, since placement evicts them for the
  weight and the reserve together. Without windows it cuts the clip to what the forward of its refine can
  hold, without charging the VAE decode: at 960×544 in two stages with a prompt-adaLN DiT, a 481-frame
  request plans 81 frames at 10.7 GB free, 89 at 12,
  185 at 23 and 257 at 31. With windows it looks for a window the DiT stays resident in, then streams one
  pass, so the window count can jump where those two tests cross. It judges with the pipe's scoped LoRA
  stack on the DiT, the state placement reads, then drops it: 1.22 GiB of deltas turn those four plans
  into 73, 81, 177 and 241 frames.

  **Scoped LoRA stacks.** `video_ltx` lands `scoped_loras` and `stage2_loras` before its placement and keeps
  them for the whole denoise loop, every per-step forward, but not for the VAE decode. They come off exactly
  on exit, an exception included, and add to whatever the model loader already baked or left resident on the
  shared DiT: refinement shares the generation LoRA chain and only adds to it. An empty stack is a no-op.

  **Eviction.** A cold placement evicts foreign residents for `weight + max(minimum_inference, total_reserve)`,
  the room the budget `free - total_reserve` asks for. This is a choice. It offloads more warm residents than a
  target of `weight + minimum_inference` would (an upscaler, other text encoders), and they reload when next
  used. When the weight and the reserve together exceed the card it offloads them and still goes partial. What
  it frees only counts past the reserve, since the budget is `max(0, free - reserve)`: with a reserve above the
  free VRAM of the whole card, it offloads them and gains nothing.

  **Two FFN settings.** `ffn_dim` is a SwiGLU width (H3's 14,336). `fc1`'s fused value|gate output is
  `2 × ffn_dim` wide and two `ffn_dim` tensors follow it, so it charges `4 × ffn_dim × 2 B` per token on top of
  attention or, when `ffn_chunk_rows` row-chunks the MLP, only that many rows in a phase that competes with
  attention as a `max`. `gelu_ffn_dim` is a GELU output (LTX's 16,384): `width × 2 B` per token, never chunked,
  and it also enters `transient`. One number would need a flag to say which of these rules applies, so the two
  stay apart.

  **Two gaps the formula keeps.** The GELU term charges an audio token 32,768 B in each of the two places it
  appears. The audio FFN, `4 × audio_inner_dim = 8,192` wide, runs after the video one, whose peak holds no audio
  GELU output, so all of it is over the peak. At 720p with the margin that is ≈ 9 MiB at 5 s (126 audio tokens)
  and ≈ 72 MiB at 40 s (1,001), about 0.27 % of the reserve. An `INT8_CONVROT` Linear on the `DEQUANT` path also
  allocates a third array, the unrotate's output: 0.125 GiB for LTX's FFN, 0.287 GiB for `fc1` on a pruned H3,
  0.484 GiB for `adaln_proj.linear` on the full int8 file. It does not raise the live
  figure. The cast copy is released when the scaled product replaces it, so the unrotate's output sits beside
  the scaled weight and two copies stay the peak (`test_a_convrot_unrotate_leaves_two_weight_copies_as_the_live_peak`,
  on the meta device). Whatever workspace that `mm` takes on a real card is outside this count, like the other
  cuBLAS workspace the 1.15 covers.
- **Text encoder: partial residency for the encode** (`memory/text_encoder.py`,
  `tiering.plan_text_encoder_placement`, `engine.TextEncoderStreaming`). The encoder only needs the
  GPU for its encode, so its placement is decided for that phase alone. An encoder that does not fit
  (see **Decision** below) is no longer moved whole with `.to(device)`: its real module(s) are placed
  with `NativeModel.stream_to(device, budget)`, exactly like a DiT that does not fit (leaves resident
  up to the budget, the rest streamed per forward from pinned RAM), then torn down right after the
  encode (unpinned, host RAM reclaimed, wrapper pointed back at the CPU) so the DiT gets the card
  back. nvfp4 leaves are streamed too: their packed buffers stay pinned and a forward pre-hook stages
  them onto the GPU for the call, so the dequant runs on the GPU from the 0.81 B/elem form. Both
  encode sites take this path: the pipes' `run_text_encode` / `run_text_encode_batch` (after the
  existing co-resident and evict steps, path `"streamed"` on the `te.encode` mark; an OOM
  `retry_after_cuda_oom` accepts retries fully streamed, then encodes on the CPU as before) and
  `NativeGenerator.encode_prompt` (torn down by `_maybe_offload_te`). A placement or an encode that
  fails for any other reason is torn down before the error propagates. That frees what the placement
  holds; it cannot repair a CUDA context the driver has already damaged.

  **Footprint formula** (`text_encoder_footprint`): weights are every parameter and buffer the modules
  own, counted once. nvfp4 holds the packed U8 codes (0.5 B/elem) + the fp32 natural scale precomputed
  at load (4/16) + the fp8 swizzled block scale (1/16) ≈ **0.81 B/elem against 0.5625 on disk**, so a
  14.6 GB nvfp4 checkpoint holds ≈ 21 GB on the device; int8 and fp8 hold 1 B/elem plus their scales.
  On top, the largest dequantisation transient: layers dequantise one at a time, so `dequant_peak =
  max over quantised layers of out × in × k`, `k` = 10 B for nvfp4 (int32 code index + two fp32 LUT
  gathers + the fp32 weight) and 8 B for int8/fp8 (fp32 cast + scaled product), ≈ 1.2 GB for a
  Qwen3-32B MLP projection. Plain bf16/fp16 layers add at most one weight-sized copy, which the working
  reserve covers. The peak is kept out of the fit test; it only sizes a streamed encoder's resident
  share.

  **Decision.** The fit test is the one each site already had; streaming only takes over where the
  whole move could not have worked. `available` = live free VRAM + what the encoder already holds on
  the device, and `reserve` = `minimum_inference_memory_gb()` (or the caller's `reserve_gb`).
  - Pipes (`run_text_encode`): step 1 (co-resident, `size + reserve <= free`) is unchanged. Step 2
    evicts as before, re-reads `available`, and the encoder streams only when `size > available`
    (`size` = `module_size_gb`, the figure step 1 compares).
  - `NativeGenerator.encode_prompt`, which always moved the encoder whole and never evicted anything,
    streams only when the weights alone exceed `available`. It still evicts nothing.

  Neither the backend's `gpu_max_vram` nor the dequantisation peak enters these tests, so they never
  turn an encoder that fit into a streamed one. They only size a streamed encoder's resident share:
  `budget = max(0, min(available, cap) − reserve − dequant_peak)`, where `cap` is `gpu_max_vram` for
  the pipes and the placement budget (`vram_gb` or device total) for the generator. The decision lives
  in `plan_placement` (`text_encoder_mode=...` → `PlacementPlan.text_encoder.streamed` /
  `stream_budget_gb`, through `plan_text_encoder_placement`'s `fit_headroom_gb` / `resident_cap_gb`);
  without that argument the plan is the sampling-phase plan it always was.

  **Option** `text_encoder_placement` on the native backend (`auto` | `gpu` | `stream`, default
  `auto`), handed to the engine by `NativeBackend.prepare_pipes` with `gpu_max_vram`
  (`configure_text_encoder_placement`); `NativeGenerator(text_encoder_placement=...)` overrides it
  for standalone use. `gpu` is the whole move, unconditionally; `stream` always uses partial residency
  (and skips the co-resident attempt); `auto` streams only when the encoder does not fit by the test
  above. An encoder that fits follows the same path call for call, for every family and with
  `gpu_max_vram` configured (`tests/platform/runtime/native/test_te_placement_regression.py` and
  `test_review_te_fit_side_effects.py` pin the sequence). The remote driver has no such field: a
  worker runs `auto` with no cap.

  **Limits.** A streamed encode is slower than a resident one: every streamed leaf crosses PCIe on
  each forward (≈ 0.81 B/elem for nvfp4, 1 B for int8/fp8, 2 B for bf16). nvfp4 leaves are staged on
  demand by their own hook, not prefetched. Pinning the streamed share needs that much free host RAM
  (+2 GB), guarded like the DiT's: when the guard refuses, the pipes encode on the CPU. A streamed
  teardown leaves idle blocks in the CUDA cache, which `mem_get_info` counts as used, so a phase that
  decides from free VRAM right after it must empty the cache first when its first reading does not
  fit (LTX's projection ladder does; a cold DiT placement always does, before it decides what to
  evict). An encoder within the fit region is still moved whole, so a
  transient beyond the reserve (very long prompt, many vision tiles) can still overflow there. The
  whole move is also not undone when it fails with anything but an OOM the same predicate accepts. A
  driver-error OOM the policy calls fatal (below) tears the streaming down and propagates.

Tier fallback (sizes unknown) and the low-VRAM regime each tier lands in:

| DiT-device VRAM | Regime |
|---|---|
| **< 8 GB** | Partial residency (a handful of blocks resident, the rest streamed) + fp8-auto quantise-at-load when it lets more fit resident. VAE tiled, TE offloaded during sampling. |
| **8–12 GB** | Partial / mostly-resident DiT; fp8-auto for the bigger families. TE & VAE offloaded off the DiT phase. |
| **12–16 GB** | fp8-resident (auto) or bf16 partial residency for the 9–14B families; VAE offloaded, TE co-resident only when it fits. |
| **≥ 16 GB** | Fully resident where the fit test allows, phase-offloading TE/VAE only under pressure; a long clip still streams part of its DiT or plans a shorter pass, because the activation reserve grows with its tokens. The 32GB dev card is the luxury case. |

### Sequence-length activation reserve (DiT placement)

Video DiTs are placed per sampling call by `dit_placement.place_dit_for_sequence`, which keeps
`free − reserve` GB of weights resident and streams the rest. For `S` tokens (video plus audio) and
attention width `d` (4096 for LTX, 7168 for MiniMax-H3):

```
reserve_gb = max(0.5, 1.15 × (S × B(d) + quant_scratch(S) + LoRA terms) / 2^30)
B(d)       = 6·d·2 (attention transient) + 3·(d/2)·4 (RoPE fp32 build) + 2·(d/2)·2 (bf16 cos/sin) + d·2 (hidden)
           = 90,112 bytes/token for LTX
```

`quant_scratch(S)` is the widest activation quantiser any fast-path linear runs. Both GEMM fast paths
quantise their input in row chunks against one tensor-wide scale (`kernels/row_chunks.py`), so the
scratch is `per_element · k · min(S, rows(k))` bytes, with `rows(k) = ⌊2²⁶ / k⌋` rounded down to the
128-row scale tile: 12 bytes per element for nvfp4, 5 for fp8. For a 16,384-wide input that is at
most 0.75 GiB (nvfp4) whatever the sequence length. If a chunk still runs out of memory, the layer
retries with half the rows, down to 128, and only then dequantizes that one call. int8 and int8
ConvRot layers always dequantize, so they quantise no activations.

| Family | Tokens | Reserve before (per-token quant copy) | Reserve now |
|---|---|---|---|
| LTX | 21,780 | 6.69 GB | 2.97 GB |
| LTX | 87,120 | 26.75 GB | 9.27 GB |
| MiniMax-H3 (nvfp4) | 145,793 + 754 | 51.75 GB | 25.61 GB |

A DiT that takes `seq_chunk_rows` (MiniMax-H3; LTX does not) gets its attention prep chunked automatically when the reserve does not fit: `S·B(d)` becomes `max(6·d·2·S, 4·d·2·S + rows·B(d))`, which is 14.36 GB for the clip above. The MiniMax-H3 page has the details.

A live preview decoder also takes VRAM during sampling, so placement holds it back too. When the
sampling hooks include a preview decoder, `sampling.preview_reserve_gb(hooks, latents_shape)` returns:

```
preview_reserve_gb = resident + transient
resident           = (parameter + buffer elements of the loaded decoder) × 2 bytes (fp16 on CUDA)
transient          = peak live bytes of one preview decode at this latent size
```

The transient comes from running the decoder's own decode functions once on the `meta` device and
recording the largest set of tensors alive at one time. It covers both decodes a step can run: the
middle-frame window at the 512 px preview cap, and, for a hook that sends clips, the whole clip at the
384 px clip cap. The result is cached per decoder architecture and latent size. For `taeh3`, the
resident part is 0.021 GB. A 124-frame 768×1344 MiniMax-H3 clip (37 × 48 × 84 latent) adds a
0.536 GB transient, so placement reserves 0.557 GB. If the estimate fails, it reserves only the
weights and logs a warning.

The BYO-loop families (MiniMax-H3, LTX) build their hooks before placement and pass this value to
`place_dit_for_sequence(preview_reserve_gb=...)`. It is added to `extra_reserve_gb`, and the
over-commit warning, the fit hint and the sampling OOM detail each report it separately. The Wan
expert router takes it from its `preview_reserve_gb` attribute. `NativeGenerator.sample` computes it
from its own `hooks`, so every image family that passes a decoder hook is covered without extra
wiring. Without a decoder hook (the colour projection or previews off) the reserve is 0.0 and
placement does not change.

All of the above is unit-testable without a GPU: the fit math, the deterministic layer split, the
fp8 quant equivalence, and the eviction accounting are pure functions (`tests/core/native/memory/`,
`tests/core/native/test_fp8_quant.py`). The wall-clock A/B (streamed-bf16 vs partial-residency vs
fp8-auto, in s/step) is measured on GPU via `tests/manual/native_smoke.py --tier-vram <GB>`.

**System RAM** (`src/platform/runtime/system_memory.py`) is psutil's reading, capped by cgroup v2 on the process's folder and
its parents from `/proc/self/cgroup`, or a passthrough without a limit: `total` by the smallest `memory.max`,
`available` by the smallest `memory.max - (memory.current - inactive_file)`, since `memory.current` counts page cache.

### Video VAE decode on the Wan pipes

The Wan pipes (`txt2vid_wan22`, `img2vid_wan22`, `chain_video_wan22`) decode through
`wan_experts.decode_wan_video`, not through the engine.

**Room before the decode.** `_make_room_for_decode` evicts foreign residents, never the VAE, until
one latent frame fits: `causal3d_frame_need_gb` is the VAE's weights plus `activation_headroom_gb`
for one latent frame (the 2 GB working base plus the per-pixel spike), over the 0.75 fraction of the
free VRAM that every decode sizer keeps. It reads `effective_free_vram_gb` and, once something left,
hands the idle pool back with `_release_idle_pool`. This runs on every card. A card that kept the
text encoder or another family's components resident sends them to RAM before a decode whose frame
needs more than its free VRAM, and the next generation that uses them reloads them from RAM.

**Whole, chunked or tiled.** With the VAE on the card, `_decode_pixels` reads the effective free
VRAM again:

- When one latent frame fits, the clip decodes whole, or in temporal chunks sized by
  `causal3d_chunk_frames` (`chunked_decode_causal3d`, one `feat_cache` across the chunks). Chunks
  leave the pixels unchanged.
- When it does not, the clip decodes in spatial tiles sized on the free VRAM by
  `causal3d_decode_tile`, the function the engine's own causal-3D tiled decode calls (the decoded
  window is the tile plus its overlap, about 1.125 tile edges wide). Each tile decodes one latent
  frame at a time with its own `feat_cache`:
  `tiled_decode_causal3d(chunk_latent_frames=1, accumulate_device="cpu")`. Tiles change the pixels
  near the seams.
- Chunks and tiles are assembled on the host. A tiled decode holds about three float32 copies of the
  clip in host RAM: 1.2 GB for 81 frames at 832×480, 3.9 GB for 121 frames at 1280×704, 12 GB for
  161 frames at 1920×1080.

**Fallbacks.** Both go through `errors.retry_after_cuda_oom`, so under WSL2 the first driver-error
OOM is fatal here too, at every tile size including the smallest. A whole or chunked decode that runs
out of VRAM clears the card (`offload_all` of the foreign residents, then the idle pool) and decodes
again one latent frame at a time. A tiled decode that runs out clears the card and halves its tile,
down to 16 latent px, then raises.

**Cost per VAE.** The sizers price the decode spike per latent pixel.
`causal3d_decode_mb_per_latent_px(spatial_downscale, latent_format)` returns the
`decode_mb_per_latent_px` a spec's `latent_format` declares. Otherwise it returns 1.2 MB per latent
pixel (`CAUSAL3D_DECODE_MB_PER_LATENT_PX`, the value of the engine's own constant), calibrated on the
8× Wan 2.1 VAE, times `(spatial_downscale / 8)²`: 4.8 for an undeclared 16× VAE, an upper bound that
errs towards tiles rather than towards an OOM, which is fatal under WSL2. The Wan 2.2 5B
(`wan_ti2v_5b`) declares 2.6, the worst allocated cost of its later latent frames rounded up to the
tenth. The 8× specs that load the Wan 2.1 VAE (`wan_t2v_14b`, `wan_i2v_14b`, `wan22_i2v_14b`, the
Wan 2.1 1.3B included) declare 0.8, measured the same way in bf16; fp16, the compute dtype of a card
without bf16, has the same element size and is not measured. The engine's own causal-3D decode, which
other families that load this VAE go through, keeps 1.2. The chain pipe hands the decode the spec of
the set a segment runs on, so a 5B segment is priced like a 5B text-to-video.

The declared costs are measured on a 12 GB RTX 4070 Ti under WSL 2, torch 2.12.1, with the calls of
this page (`vae.decode`, `chunked_decode_causal3d(accumulate_device="cpu")`,
`tiled_decode_causal3d(chunk_latent_frames=1, accumulate_device="cpu")`), the VAE loaded by the
engine's loader (bf16 compute) and five random latent frames. Peaks are `max_memory_allocated` and
`max_memory_reserved` after `reset_peak_memory_stats`, over the resident weights, the same on the
whole card and under an 8 GiB allocator cap wherever the window fits:

| VAE | Window decoded | Latent px | First latent frame, allocated | Later latent frames, allocated | Later frames, reserved (expandable segments / default allocator) |
|---|---|---:|---:|---:|---:|
| Wan 2.2 (5B) | 832×480 whole | 1,560 | 1,461 MiB | 3,951 MiB (2.53 per px) | 4,583 / 5,548 MiB |
| Wan 2.2 (5B) | 1280×704 whole | 3,520 | 3,280 MiB | 8,897 MiB (2.53 per px) | 9,122 MiB / out of memory under 9 GiB |
| Wan 2.2 (5B) | 40 px tile | 1,600 | | 4,011 MiB (2.51 per px) | 4,616 / 5,690 MiB |
| Wan 2.2 (5B) | 24 px tile | 576 | | 1,453 MiB (2.52 per px) | 1,837 / 1,916 MiB |
| Wan 2.1 | 832×480 whole | 6,240 | 883 MiB | 4,040 MiB (0.65 per px) | 4,915 / 4,982 MiB |
| Wan 2.1 | 40 px tile | 1,600 | | 1,026 MiB (0.64 per px) | 1,204 / 1,454 MiB |
| Wan 2.1 | 32 px tile | 1,024 | | 657 MiB (0.64 per px) | 783 / 878 MiB |

The first latent frame decodes to one pixel frame and costs a third of the later ones, which decode
to four pixel frames each and keep the causal `feat_cache`. From the third latent frame on, the peak
stops growing in the chunked and tiled paths. A 2 GB working base covers every reserved peak of the
table under both allocators.

The decode tiles below these free VRAM readings, with the VAE on the card (all frame counts):

| VAE, cost | 832×480 | 960×544 | 1280×720 | 1488×624 | 1920×1080 |
|---|---:|---:|---:|---:|---:|
| 8× (Wan 2.1 VAE), 0.8, declared | 9.17 GB | 11.17 GB | 17.67 GB | 17.78 GB | 36.42 GB |
| 8× undeclared, 1.2 | 12.42 GB | 15.42 GB | 25.17 GB | 25.34 GB | 53.29 GB |

| VAE, cost | 832×480 | 1280×704 | 1920×1088 |
|---|---:|---:|---:|
| 16× (Wan 2.2 VAE, 5B), 2.6 | 7.95 GB | 14.58 GB | 30.29 GB |

A 12 GB card has about 9.4 GB free with the VAE on it: at 0.8 an 832×480 clip decodes in chunks of one
latent frame, as a whole decode, and tiles from 960×544. Before the decode, the eviction targets the
same frame plus the VAE's weights: at 0.8, 17.99 GB for a 1280×720 frame and 36.74 GB for a 1920×1080
frame.

### Running out of VRAM, and the driver-error OOM

What counts as running out of VRAM is decided in one place, `errors.is_cuda_oom`: a
`torch.cuda.OutOfMemoryError`, a `torch.AcceleratorError` that says "out of memory", and the
`RuntimeError: CUDA driver error: device not ready` (or `... out of memory`) raised by the
expandable-segments allocator, wherever the policy below retries it. Under WSL2 (Docker Desktop)
with expandable segments, that driver error is how an exhausted card shows up, measured on a 12 GB
RTX 4070 Ti: never an `OutOfMemoryError`. Every OOM fallback in the engine and the pipes catches
`RuntimeError`, keeps only what `errors.retry_after_cuda_oom` accepts and re-raises the rest
unchanged. `cuda_allocator.record_driver_oom`, which that function calls, is the one place the damage
below is recorded; `is_cuda_oom` and `cuda_allocator.needs_gpu_restart` only read it.

`cuda_env.py` turns expandable segments on outside Windows, WSL2 included, and only when none of
`PYTORCH_CUDA_ALLOC_CONF`, `PYTORCH_HIP_ALLOC_CONF` and `PYTORCH_ALLOC_CONF` is set: torch reads
the first of the three that exists, so a default written into the first would hide the user's own
setting in the last. They stay on under WSL2 because the default allocator is worse there: with
driver 617.42 an exhausted card raises nothing and spills into Windows shared memory instead (a
SeedVR2 1080p encode reached 47.9 GB of shared memory without finishing, on a 12 GB card), so no
fallback ever runs.

A driver-error OOM under expandable segments damages the process, though. When an expandable
segment grows, torch 2.12 rolls the new pages back only if `cuMemCreate` returns
`CUDA_ERROR_OUT_OF_MEMORY`; that clean case reaches the caller as `torch.OutOfMemoryError`. Any other
failure of `cuMemCreate`, `cuMemMap` or `cuMemSetAccess` (the `device not ready` above, or an
out-of-memory from `cuMemSetAccess`) raises with the pages still recorded, and the next growth over
them fails on `!handles_.at(i) INTERNAL ASSERT FAILED`, in any later allocation, until the process
restarts (pytorch/pytorch#188008, #166234).

So under WSL2 the first driver-error OOM is fatal to the process (`src/platform/runtime/cuda_allocator.py`):
it is not retried, the segments stop growing for the rest of the process
(`torch.cuda.memory._set_allocator_settings`, the other allocator options kept), and the generation
fails under its own error code, `gpu_restart_required`: admins read that the process can no longer
use the GPU and get Restart Backend as the only hint, other users are told to ask an administrator
to restart the backend. A failure read from its text alone (`classify_error_text`: a stored row) asks
for a restart only while this process is damaged, reads as `cuda_oom` otherwise, and never records
anything; a worker's failure never asks for this process's restart at all
(`worker_failure_category`). Until the process restarts, every run with an enabled pipe on `cuda`
is refused before its first pipe with that same code, `GET /health` and the native backend's health
report `degraded` with the same reason, and nothing restarts on its own. What the operator sees and
does is in [Backends → Troubleshooting](/admin?tab=docs&doc=dev/backends).
`POTIONUI_CUDA_DRIVER_OOM=retry` brings back the retry in the same process,
`POTIONUI_CUDA_DRIVER_OOM=fatal` applies the fatal path outside WSL2 too; with expandable segments
off, driver-error OOMs are always retried. The same error under native Linux keeps entering the
fallbacks, and a `torch.OutOfMemoryError` keeps being retried everywhere, a damaged process included.

A `native.remote` worker (`./potionui worker start` under WSL2, or the worker image under Docker
Desktop on Windows, whose container sees the WSL2 kernel and so counts as WSL2 for `is_wsl()`) runs
the same allocator code but never `GenerationEngine`, so it carries the fatal path itself
(`src/features/remote_execution/worker/executor.py`): once the allocator is damaged, every enabled pipe
on `cuda`, by the same rule (`pipe_config_runs_on_cuda`), is refused before it is built under the
worker's own code `worker_gpu_restart_required`, and the exception a pipe raises is classified on the
worker (`classify_generation_error`), so the host receives a code instead of reading the message. The
worker's `GET /health` and the remote native backend's health report `degraded`, and the hint names the
worker process or container rather than Restart Backend, which only replaces the host's own process.
A worker under native Linux keeps retrying as before. What the operator sees and does is in
[Remote Native](/admin?tab=docs&doc=dev/remote-native).

## The extension contract — adding model N+1

This is the section that matters most. Everything above is infrastructure; this is how you use it.
The invariant the whole design protects: **adding a new model family must never require editing
`sampling/denoise_loop.py`, anything under `kernels/`, `io/safetensors_loader.py`, or `memory/tiering.py`.**
Those four are the shared core; a new model expresses itself entirely through data (a
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
   see `sampling/denoise_loop.py:make_guidance`), `latent_format` (scale/shift factors — get the
   real numbers from ComfyUI's `latent_formats.py`, never guess them), `clip_targets` / `vae_target`
   (which TE(s)/VAE this family pairs with), and `expected_missing_keys` /
   `expected_unexpected_keys` (fnmatch globs for the load-integrity allowlist — quant sidecars like
   `*.weight_scale` go here so a non-fp8 load never trips on them).

3. **Arch module + `post_load`** (`arch/<family>/`). Vendor (or write) the module as a
   `NativeArchModule` subclass: `from_config(config, operations)` builds empty weights under
   `torch.device("meta")`, wiring every parameterised layer through `operations.*` (never bare
   `torch.nn.Linear`/`Conv2d`/etc. — that's the seam the fp8/cast layers in `kernels/` depend on).
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
   stay documented no-ops. Every loop over transformer blocks in a forward calls
   `raise_if_cancelled(i)` before its block: that check is what stops a cancelled run before the
   next block instead of at the end of the sampling step. It reads one `ContextVar` and calls the
   run's probe, and regional `torch.compile` only wraps the blocks, so the loop stays Python.

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
   gets a key-rename map instead of a new module — see `vae/key_convert.py`. A tile loop calls
   `raise_if_cancelled(i)` before each tile, like a DiT block loop.

6. **Conditioning assembly.** However the family's TE output (`context`/`pooled`/`attention_mask`)
   maps onto the arch's `forward(x, timestep, context, y, guidance)` signature is the generator-side
   adapter's job, not the sampling core's — `sampling/denoise_loop.py`'s `model_forward` callable is
   exactly this adapter, and it's the one new piece of *code* (not data) each family typically needs.
   It stays outside `denoise_loop.py` itself; see the Wan dual-expert note in that file's docstring
   for the general pattern (wrap `model_forward`, don't branch inside the loop).

7. **Hardware-dependent behaviour stays in `kernels/`.** A family's files call
   `kernels.attention(...)`, read `kernels.capabilities().device(d)`, `kernels.gemm_support(d)` or
   `kernels.fast_path(layer)`, and take their layers from `pick_operations`; they never probe the
   device or call `torch.nn.functional.scaled_dot_product_attention` themselves (the boundary rules
   above). A new attention backend or GEMM fast path is added inside `kernels/` (a backend function
   in `dispatch.py` and its availability in `hardware.py`; a forward in `fp8_linear.py` or
   `nvfp4_linear.py`), and a new on-disk quant format's descriptor parsing and dequantiser go in
   `io/quant_formats.py` — never in a family's files.

If a new family needs a genuinely new *guidance* shape (not covered by `EmbeddedGuidance`/
`TrueCFG`/`NoCFG`), add a class satisfying `cfg.GuidanceStrategy`'s `Protocol` and pick it in
`denoise_loop.make_guidance` by a new `sampling_settings["guidance"]` value — still data-driven,
still no branch on family name.

## Quantization support matrix

| Format | Storage | Status | Notes |
|---|---|---|---|
| bf16 / fp16 | native dtype | **Supported** | `disable_weight_init` (storage == compute) or `manual_cast` (storage != compute) namespace from `kernels/casting.py`, picked by `kernels/operations.py:pick_operations` over the dtypes `ops/dtype.py:pick_dtypes` returns. |
| fp8-scaled (legacy `scale_weight`) | `float8_e4m3fn`/`e5m2` + one f32 scale per Linear | **Supported** | `fp8_ops.Linear` (`kernels/fp8_linear.py:Fp8ScaledLinear`) dequantises on forward: `weight.to(compute) * weight_scale`. Detected via a `*.scale_weight` sidecar key or a top-level `scaled_fp8` marker tensor. |
| fp8-scaled (modern `weight_scale`/`input_scale`) | as above, per-tensor `weight_scale` + `input_scale` | **Supported** | Same `Fp8ScaledLinear`; `input_scale` is captured for a future native-fp8 matmul path but unused by the current dequant-on-forward path. Detected via a `_quantization_metadata` header entry or `*.weight_scale`/`*.input_scale` sidecar keys — `kernels/operations.py:detect_quant_format`. Verified against the real `flux-2-klein-9b-fp8.safetensors` checkpoint. |
| nvfp4 | 4-bit packed U8 weight + fp8 block scale + f32 tensor scale (`*.weight_scale_2` sidecar) | **Supported** | `io/quant_formats.py:dequantize_nvfp4` + `kernels/nvfp4_linear.py:Nvfp4Linear` (extends `Fp8ScaledLinear`): un-swizzles NVIDIA's `to_blocked` scale layout, dequantises `e2m1_LUT * unblock(scale) * weight_scale_2` per 16-element block. A checkpoint may be *mixed* fp8/nvfp4 across layers (the local `qwen_3_8b_fp8mixed` is one) — `pick_operations` routes the whole module to `fp8_ops` and each Linear independently detects its own format from which sidecar keys it has. Tested against a `Nvfp4Linear` load+forward-parity case and a mixed fp8/nvfp4/plain module (`tests/core/native/test_nvfp4.py`), and **verified end-to-end** via the real `qwen_3_8b_fp8mixed` TE in the GPU-validated Klein run (the 8B TE is exactly this mixed fp8/nvfp4 checkpoint). |
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
| `yue2` | `yue2_3b` | `vae2llm.weight` + `model.layers.0.nar_self_attn.q_proj.weight` on the checkpoint-native AR/NAR backbone (a separate Comfy-repack layout is keyed independently on `vae2llm.weight` + `model.layers.0.self_attn.qkv_proj.weight`); the VAE separately on `decoder.layers.0.weight_v` + `decoder.layers.8.weight_v` | Two sequential passes, one seed: an autoregressive stage (`arch/yue2/ar_loop.py`) samples a codec token per frame at 25 fps (`Sampling` defaults `temperature=1.0`, `top_p=0.95`, `top_k=100`, `repetition_penalty=1.2`), CFG 1.01 when `cot="off"` else 1.0, optionally preceded by an ABC-transcription pass (`cot="melody"`/`"full"`, its own sampling defaults) the codec stage conditions on; a non-autoregressive flow-matching stage (`arch/yue2/nar.py`, midpoint-Euler, 32 steps default) turns the sampled codec tokens into a 64-channel latent | None — the same AR/NAR backbone (`YuE2Model`) both samples codec tokens and is its own conditioning; style tags + lyrics are tokenized directly into the prompt (`arch/yue2/protocol.py:build_prompt_ids`), never routed through a separate encoder module | `yue2_vae` (`arch/yue2/vae.py`, decode-only Oobleck decoder, fp32 compute, hop 1920 → 48 kHz) | **GPU validation pending; no local weight files; detection signatures unverified against real weight headers.** Arch, detection, the VAE and the pipes (`model_loader/yue2` + `generator/audio_yue2`) are built and CPU-tested; `content/presets/marketplace/YuE2` landed with a single `song` mode. Text-to-music only — no audio encoder ships, so no style/reference/extend/repaint mode exists for this family. The detection keys above were derived from the upstream architecture's known tensor names, not confirmed against a downloaded checkpoint's actual state-dict keys. Weights (both the backbone and the VAE) are CC BY-NC 4.0 — non-commercial use only, attribution required — see `docs/models/yue2.md` |

> **Krea-2's VAE has a real numerical subtlety, not a missing piece anymore.** The
> `krea2_turbo` spec's `latent_format` carries per-channel `latents_mean`/`latents_std`
> (ComfyUI's `Wan21` latent format) that `vae/causal_3d.py` also exports as
> `LATENTS_MEAN`/`LATENTS_STD` -- keep both in sync if either changes. The **Wan 2.2** VAE has no
> such per-channel constants to port: ComfyUI's `Wan22` latent-format class inherits `Wan21`'s
> `process_in`/`process_out` (which read `self.latents_mean`/`self.latents_std`) but its own
> `__init__` never sets them -- verified by reading the class, not assumed. Wan 2.2 normalization
> is plain `scale_factor=1.0` (a no-op), nothing per-channel.

## Live previews

Sampling streams a preview of the running `x0` estimate to the workbench every fifth step (the first
and last step always). Two sources produce it:

- **Latent colour projection** (default). A per-family `C x 3` linear map from ComfyUI's
  `latent_formats.py`, applied at latent resolution (`sampling/preview.py`). It costs one matmul and
  shows only the first frame of a video. MiniMax-H3 has no trained table, so its colours are a
  placeholder.
- **Preview decoder.** A TAEHV tiny decoder ([madebyollin/taehv](https://github.com/madebyollin/taehv),
  MIT, vendored under `vendor/taehv/`). It decodes the running latent to real colour, under 0.5 GB of
  VRAM and several times faster than the full VAE, and the preview shows the middle frame of the clip.

The decoder is an ordinary model slot, not a setting. A preset declares an optional `preview_decoder`
model field (`model_type: preview_decoder`, depot folder `vae_approx/`, the same folder ComfyUI uses),
admins can set its default or hide it like any other model field, and nothing is downloaded behind the
user's back: the weights come from the field's recommendation or the `src/std/models/preview_decoders.yml`
catalog. When the field is empty, missing, unreadable or built for another latent width than the model
samples, the hook silently keeps the colour projection.

| Weights | Latent | Use with |
|---|---|---|
| `taeh3` | 24 ch | MiniMax-H3 |
| `taeltx_2` | 128 ch | LTX-2 |
| `taeltx2_3` | 128 ch | LTX-2.3, LTX-2.5 |
| `taew2_1` | 16 ch | Wan 2.1, Wan 2.2 14B, Qwen-Image |
| `taew2_2` | 48 ch | Wan 2.2 5B |

The architecture is read from the weights (latent width and layer shapes), never from the file name, so
a renamed file still loads. A decoder is loaded once through `ModelLifecycle` under the key
`native/preview_decoder/<path>`, runs in fp16 on CUDA and fp32 on CPU, and goes back to the CPU when the
sampling loop ends. To keep the cost flat on long clips, a step decodes a window of three latent frames
around the middle one, at the smallest spatial scale that still reaches the 512 px preview cap.
Its weights and one decode are reserved before DiT placement (see *Sequence-length activation reserve*).
`sampling/preview_decoder.py` also exposes `decode_clip`, which decodes every frame of a latent
(optionally at reduced spatial scale) for callers that want the whole low-resolution clip.

To add the slot to another mode, copy the `preview_decoder` field into that mode's Models section and
add `preview_decoder: "{{ form.preview_decoder | default('') }}"` to each generator pipe's
`configuration:`.

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
  APG, SLG), NAG, RIFLEx, the registry-driven sampler/schedule menu, FBCache, temporal-chunked VAE decode, prompt-
  embedding caching, new attention backends, and the fp8 GEMM fast path layered on top of this base.
