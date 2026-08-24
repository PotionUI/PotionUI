---
title: Model Inference Path
category: Presets / Models
category_order: 70
order: 30
---

# Model Inference Path

This page follows one generation from the **Generate** action to its saved output. It explains
which layer owns each decision, how a model reaches an inference runtime, and why “native model
loading” can mean two different things in the current codebase.

The most important rule is that a preset is not a model and an engine is not a backend:

| Term | What it owns |
|---|---|
| **Preset** | A versioned UI and ordered pipeline blueprint: engine, modes, forms, variables, media, and pipes. |
| **Model** | A logical asset selected through form data. Its backend-specific locations are stored separately. |
| **Engine** | The protocol spoken by the preset's pipes, such as `native` or `comfyui`. |
| **Backend** | One enabled, configured instance of an engine. The backend supplies execution policy or a remote connection. |

Selecting a preset does not directly choose a particular model or backend. The preset declares an
engine; the request supplies model references; the orchestrator then chooses a compatible backend.

## End-to-end path

```text
Preset + mode + prompts + form values
  -> load the preset and read its engine
  -> find a compatible backend for every selected model
  -> rewrite logical model IDs to that backend's native references
  -> run generation.before_start hooks
  -> persist and queue the generation
  -> expand prompts and resolve a random seed at dispatch
  -> render the selected preset pipeline with Jinja
  -> inject backend configuration
  -> validate and execute enabled pipes in order
  -> run native-v2, SDXL/Diffusers, ComfyUI, or a plugin engine
  -> stream progress and outputs through the orchestrator
  -> persist artifacts, update status, and release the backend queue slot
```

`GenerationOrchestrator` owns routing, persistence, queueing, and output handling.
`PipelineBuilder` is the one form-to-pipeline path used by both real execution and graph preview.
`GenerationManager` validates and executes the resulting pipes.

## 1. Backend and model routing

The orchestrator first loads the requested preset and reads `preset.engine`. There is no automatic
cross-engine fallback: a `comfyui` preset cannot silently execute on `native`, or vice versa.

It then recursively finds `model:<id>` values in form data, including model references nested in
LoRA lists. Availability filtering behaves as follows:

1. If the request contains no logical model references, all enabled backends for the preset's
   engine remain candidates.
2. If no backend for that engine has ever been indexed, availability filtering is skipped. This
   preserves operation before the first index.
3. Once at least one backend for the engine has been indexed, a candidate must have an availability
   row for **every** selected model.
4. An explicitly requested `backend_id` wins if it remains a candidate.
5. Otherwise the engine's configured default backend wins.
6. Otherwise the highest-priority candidate wins.

“Enabled” is the routing criterion here; selection does not perform a new live health check. One
subtle consequence of the indexing rule is that, once filtering is active, an unindexed backend
normally has no availability rows and is excluded.

After selection, each logical model ID is replaced with the exact reference expected by that
backend. A native backend normally receives a local path; a ComfyUI backend receives a
server-relative name. If the selected backend has never been indexed, resolution may fall back to
the model's local path or filename. If it has been indexed but lacks the selected model, generation
fails before pipeline execution.

The relevant implementation lives in:

- `src/features/generation/orchestrator.py`
- `src/features/backends/backend_registry.py`
- `src/features/models/form_refs.py`
- `src/features/models/availability.py`

## 2. From preset data to executable pipes

The selected mode's `pipeline.yml` is rendered by `PresetProcessor`. Its Jinja context contains the
expanded prompts, form values, preset variables, mode, and preset/shared paths. Rendering produces
an ordered list in which the pipe name, enabled state, inputs, and configuration can all be
templated.

The generation row, detached rich prompt segments, tags, and pending status are stored before the
job enters the queue. The queue is FIFO with one execution slot per backend: work for two idle
backends may run in parallel, while work for the same backend remains ordered.

At dispatch, `seed: -1` is resolved and prompt variants are expanded reproducibly. The processed
pipeline is then handed to the chosen backend:

- `NativeBackend.prepare_pipes()` fills missing `device`, `dtype`, and `vram_limit_gb` values.
- `ComfyUIBackend.prepare_pipes()` injects the selected server's connection configuration.
- A plugin backend can provide its own preparation step.

`GenerationManager` overlays the processed configuration onto each pipe class's defaults, validates
the declared values, resolves inputs from earlier pipe outputs, and injects requested services:
`GPU`, `SYSTEM`, `MEMORY`, `LLM`, `MODELS`, and `ASSETS`. Enabled pipes run sequentially, with
`pipe.before_execute` and `pipe.after_execute` hooks around each call.

The old pipeline-level `cache:` key is deliberately inert and is removed by `PipelineBuilder`.
Model reuse is owned by `ModelLifecycleManager` instead.

## 3. What actually performs inference

There are three important execution paths. They share orchestration and pipe execution, but they do
not share a model loader or memory strategy.

### Native engine v2

Modern Flux, Krea, Qwen, Wan, Anima, Z-Image, and related pipelines use family-specific
`model_loader/*` and `generator/*` pipes backed by `src/platform/runtime/native/`.

A typical image pipeline is:

```text
model_loader/<family>
  -> prompt_encoder
  -> seed_generator
  -> generator/<family>
  -> gallery
```

#### Loading and reuse

Modern loaders acquire the heavy components independently: diffusion transformer (DiT), text
encoder, and VAE. Each entry has a stable cache key plus a fingerprint containing the state that
changes its output, such as file paths, precision label, and LoRA stack. Matching key and
fingerprint produces a cache hit; a changed fingerprint evicts and reloads that slot.

Separating components matters. A LoRA change can invalidate only the DiT while retaining a shared
text encoder and VAE. Under host-RAM pressure, the lifecycle manager evicts least-recently-used
entries and aims to retain at least the greater of 8 GB or 10% of total system RAM. It warns and
continues if eviction cannot create that headroom.

Native-v2 accepts safetensors (`.safetensors` or `.sft`), not pickle checkpoints or GGUF. A DiT is:

1. read into CPU memory;
2. detected from structural signature keys and tensor shapes;
3. matched to a `ModelSpec` describing architecture, latent format, guidance, and schedule;
4. assigned a storage/compute dtype and operations implementation;
5. constructed empty on the `meta` device and assign-loaded;
6. rejected if load integrity, remaining-meta, or sampled finite-value checks fail.

This is intentionally strict: an incomplete load fails instead of producing a plausible-looking
but invalid image.

#### Placement modes

Native-v2 uses **fit-first placement** when component sizes are known. It preferentially retains
the DiT, then the text encoder, then the VAE. The named VRAM tiers are a fallback for unknown sizes,
not a hard model-size classification.

| Fallback VRAM tier | Placement name | Default shape when sizes are unknown |
|---:|---|---|
| `< 8 GB` | `streaming` | DiT, text encoder, and VAE are non-resident. |
| `8–12 GB` | `component_offload` | DiT resident; text encoder and VAE phase-loaded. |
| `12–16 GB` | `vae_offload` | DiT and text encoder resident; VAE phase-loaded. |
| `>= 16 GB` | `resident` | All three components may remain resident. |

“Non-resident DiT” does not mean moving the whole network for every step. Partial residency keeps
as many streamable leaf weights on the GPU as fit after activation headroom is reserved, then
streams the remaining leaves from pinned CPU memory during forward passes. Fixed and
non-streamable tensors stay resident. If a whole-model move unexpectedly OOMs, the engine can
evict foreign residents and retry with partial residency.

On multi-GPU systems, a large DiT remains whole on one device; this is not tensor parallelism. If
the DiT would dominate its selected GPU, the device planner may place the entire text encoder on
the other GPU with the most free memory. The VAE stays with the DiT.

#### Inference phases

Native-v2 treats inference as memory phases rather than assuming every component must coexist:

1. **Prompt encoding** moves or retains the text encoder long enough to produce conditioning. A
   conditioning-cache hit can bypass encoding. Under pressure, older GPU residents are evicted;
   CPU encoding is the final fallback.
2. **Sampling** computes placement for the actual latent resolution, creates deterministic seeded
   noise, builds the family-specific sigma schedule and guidance strategy, and runs the requested
   sampler.
3. **Decode** makes room for the VAE independently. The engine can retain or offload the DiT,
   automatically tile 2D or causal-3D decoding, and retry causal-3D decode with smaller tiles after
   an OOM.

Successful generations do not indiscriminately clear every GPU resident. Reusable components may
remain available for the next request and are evicted when pressure requires it. Error paths
explicitly offload the failed generation's models.

### Legacy native SDXL/Diffusers

SDXL also uses the `native` engine, but its checkpoint loader constructs a Diffusers-style pipeline;
it does **not** use native-v2 partial layer residency. It exposes explicit memory strategy values:

- `auto`
- `cpu_offload`
- `sequential_offload`
- `gpu_only`

With `auto`, the shared `MemoryPolicy` maps VRAM to these behaviors:

| VRAM | Offload | Attention slicing | VAE behavior |
|---:|---|---|---|
| `< 8 GB` | Sequential CPU offload | Maximum | Slicing and tiling |
| `8–12 GB` | Model CPU offload | Automatic | Slicing and tiling |
| `12–16 GB` | None | None | Slicing |
| `>= 16 GB` | None | None | Fully resident defaults |

This path also enables TF32 on CUDA, uses PyTorch 2 attention by default, can opt into xFormers,
and applies VAE/attention slicing according to the tier. Other older model-specific pipes may own
their own details; do not assume native-v2 placement merely because a preset says `engine: native`.

### ComfyUI

For `engine: comfyui`, PotionUI still runs the orchestration pipe locally, but the configured
ComfyUI server owns model loading and inference.

The backend injects host, port, secure-transport, client ID, and timeout information. The ComfyUI
pipe loads and copies a workflow, uploads mapped inputs, casts and applies form values, performs
node manipulations, connects over WebSocket, submits the graph to `/prompt`, streams progress and
previews, and retrieves the produced artifacts.

ComfyUI owns its model cache, node cache, offload policy, samplers, schedulers, custom nodes, and
GPU optimizations. Native-v2 placement tiers and the SDXL local memory policy do not apply to the
remote server. Cancellation is forwarded to ComfyUI's `/interrupt` endpoint.

## 4. Parameters and precedence

There is no universal inference parameter set. The form decides what a preset exposes; Jinja
decides where each value is mapped; a pipe schema defines accepted values and defaults; and
`ModelSpec` gives family-specific meaning to guidance, schedules, architecture, and latent layout.

Final precedence, from lowest to highest, is:

| Precedence | Source | Behavior |
|---:|---|---|
| 1 | Pipe class defaults | Used when no processed configuration supplies a value. |
| 2 | Backend defaults | Native fills only missing device/precision/VRAM keys. ComfyUI always supplies the selected connection. |
| 3 | Rendered preset configuration | Values produced by mode YAML and Jinja override class defaults and, for native keys, survive backend `setdefault`. |
| 4 | `pipe.before_execute` hook | May modify pipe configuration and inputs immediately before execution. |

Chronologically, preset rendering occurs before backend preparation; the table describes the
resulting **value precedence**. `GenerationManager` then merges the processed configuration over
the class defaults and validates it.

Common controls are owned as follows:

| Control | Owner and meaning |
|---|---|
| Prompt / negative prompt | Expanded before pipeline rendering, then encoded according to the model family's guidance strategy. Not every family consumes a negative prompt. |
| Model / VAE / text encoder / LoRAs | Form values become backend-specific references. Loader fingerprints decide which cached components must reload. |
| Seed / quantity | The orchestrator and seed pipe resolve deterministic per-output seeds; the generator iterates them. |
| Resolution | The generator derives the latent shape. Native-v2 snaps unsupported dimensions to the family's patch granularity. |
| Steps | Length of the denoising schedule exposed by that generator. |
| Sampler | Must be supported by the selected generator. Engine support can be broader than the choices exposed by one preset. |
| Guidance | Family-specific: true CFG, embedded guidance, or no CFG. A single numeric label does not imply the same computation for every model. |
| Shift / schedule | Usually comes from `ModelSpec`; a preset may expose a supported override. Turbo families can intentionally omit it. |
| Denoise strength | For img2img, truncates the schedule and blends the encoded initial latent with seeded noise. |

The shared native denoiser currently contains Euler, DPM++ 2M, UniPC, Euler SDE, and Euler Restart
implementations plus shift, beta, and exponential schedule support. A particular generator or
preset may deliberately expose only a subset.

## 5. Optimizations

### Native-v2 attention

The automatic attention priority is:

```text
SageAttention 2 -> SageAttention -> FlashAttention -> PyTorch SDPA
```

An explicit call override, `NATIVE_ATTENTION`, or the admin in-memory pin can request a backend;
otherwise the fastest available implementation is selected. Accelerated kernels are eligible only
for CUDA fp16/bf16 calls without a dense mask. CPU, fp32, and masked calls transparently fall back
to SDPA. Therefore “installed” does not mean every attention operation uses that kernel.

The admin optimization catalog currently provides SageAttention 2, FlashAttention, and a matching
CUDA toolchain installer. xFormers is part of the legacy SDXL path, not a native-v2 dispatcher
backend.

### Precision and FP8

Native-v2 can load existing scaled-FP8 checkpoints. For a compatible full-precision DiT,
load-time quantization supports `auto`, `off`, and `force` through the loader policy (or
`NATIVE_FP8_QUANTIZE`); the default is `auto`. Automatic conversion occurs only when the original
precision would not fit resident but the estimated FP8 representation would. Large linear weights
are quantized while precision-sensitive tensors remain at their original dtype.

FP8 storage and FP8 matrix multiplication are distinct. `NATIVE_FP8_MATMUL` controls the optional
matrix-multiply fast path and currently defaults to off; without it, scaled FP8 weights use the
established dequantize-on-forward path.

### Other reuse and memory optimizations

- Component-level lifecycle caching avoids disk reloads.
- Prompt-conditioning caching can skip repeated text encoding.
- LoRA-aware fingerprints reload only affected components.
- Partial residency trades PCIe traffic for lower VRAM use.
- Phase-specific offload avoids co-residency when components are not used together.
- Resolution-aware VAE tiling reduces decode spikes and has OOM retry behavior.
- GPU-resident components use pressure-driven LRU eviction.
- Per-backend queue serialization prevents two jobs from competing inside one backend slot.

## 6. Current implementation boundaries

Two native backend settings have narrower effects than their names currently suggest:

1. **`dtype` is not a native-v2 compute override.** The backend injects it and modern loaders include
   the label in cache fingerprints, but `NativeEngineLoader` chooses compute dtype from checkpoint
   storage and hardware capability. Legacy loaders may handle the setting differently.
2. **`gpu_max_vram` is not currently a hard native-v2 runtime cap.** It reaches the loader and can
   influence FP8/load decisions, but the standard flow generator does not pass that budget into
   `NativeGenerator`. Runtime placement therefore falls back to physical device total memory.

These are current-code limitations, not configuration recommendations. Keep them visible when
changing backend controls so the UI and runtime contract can be corrected together.

## 7. Debugging map

When a generation behaves unexpectedly, inspect the layers in this order:

1. **Preset discovery and mode:** `src/features/presets/loader.py`, `src/features/presets/processor.py`
2. **Backend/model routing:** `src/features/generation/orchestrator.py`,
   `src/features/backends/backend_registry.py`, `src/features/models/`
3. **Rendered pipeline:** `src/features/generation/pipeline_builder.py`
4. **Pipe validation/execution:** `src/features/generation/generation.py`
5. **Lifecycle cache:** `src/platform/runtime/model_lifecycle/manager.py`
6. **Native-v2 loading/placement:** `src/platform/runtime/native/engine.py`, `src/platform/runtime/native/memory/`
7. **Native-v2 sampling:** `src/platform/runtime/native/sampling/`
8. **Legacy SDXL memory policy:** `src/pipelines/pipes/checkpoint_loader/sdxl/`
9. **ComfyUI submission:** `content/plugins/marketplace/comfyui-backend/backend/`

Useful log markers include `[MODEL_LIFECYCLE]` for cache hits, misses, and evictions; native
placement and attention dispatch messages; `[MEMORY STRATEGY]` for SDXL; and
`[COMFYUI_BACKEND]` for remote connection preparation.

## Deeper references

- [Preset Authoring Guide](/admin?tab=docs&doc=dev/presets) — manifest, forms, modes, Jinja, and pipeline authoring.
- [Models and Backend Availability](/admin?tab=docs&doc=dev/models) — logical model identity, indexing, and backend refs.
- [Backends and Engines](/admin?tab=docs&doc=dev/backends) — engine registration and backend configuration.
- [Native Engine v2](/admin?tab=docs&doc=dev/native-engine) — architecture detection, loading integrity, memory, sampling,
  and the model-family extension contract.
