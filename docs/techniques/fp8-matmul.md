---
type: technique
title: Native fp8 Matmul
category_group: Performance
status: needs-gpu-validation
families: [all-native]
authors: []
paper: null
reference_impl: null
knobs:
  - key: native_fp8_matmul
    surface: admin
    default: "off"
    effect: "Runs fp8-quantized linear layers as real fp8 GEMMs instead of dequantizing to bf16/fp16 first"
  - key: native_lora_fused
    surface: admin
    default: "on"
    effect: "Adds a layer's runtime LoRA adapters in one fused in-place step with factors prepared once; off restores the old per-adapter path for A/B"
related: [torch-compile]
---

# Native fp8 Matmul

Some checkpoints ship their weights quantized to 8-bit floating point (fp8) to save VRAM and disk
space. By default, PotionUI dequantizes those weights back to bf16/fp16 before running the matrix
multiply — the storage savings help memory, but the compute itself still runs at full precision.
This technique adds a real fp8 GEMM fast path using PyTorch's `torch._scaled_mm`, so on supported
hardware the matmul itself runs in fp8 instead of being upcast first.

The fast path only activates on a given linear layer when a specific set of conditions hold: the
layer's weight is stored as `float8_e4m3fn`, no active LoRA delta is patched into it (or the delta
is expressible as an output-side low-rank branch), the activation is on CUDA with a matching
float16/bfloat16 dtype, and the layer's dimensions are 16-aligned (a hardware requirement of
`_scaled_mm`). Any layer that doesn't meet these conditions transparently falls back to the
existing dequantize-and-multiply path — nothing breaks, it's just not accelerated for that layer.

**Streamed (partial-residency) weights.** A weight that isn't already CUDA-resident — a leaf
streamed from pinned CPU RAM under partial residency (see the native low-VRAM docs), not yet
staged by a prefetch hit — is no longer disqualifying on its own. The fast path stages that
weight to the activation's device for the current call only, reusing an already-resident or
already-prefetched weight as-is with no extra copy. The staged copy is a plain local reference:
it is never written back onto the layer, so the streamed leaf's own CPU/pinned storage and
residency plan are untouched, and nothing is retained across calls. A staging failure (e.g. an
out-of-memory error) releases the temporary immediately and falls back to the dequantize path for
that call, exactly like any other fast-path rejection. A CUDA driver-error OOM the driver-OOM policy
treats as fatal is the exception: it propagates, see
[Native Engine → Memory tiers & low-VRAM](/admin?tab=docs&doc=dev/native-engine).

## When to use it

Only relevant if you're running an fp8-quantized checkpoint (e.g. an fp8-scaled or mixed
fp8/nvfp4 model) on Ada, Hopper, or Blackwell-class GPUs, which have real fp8 tensor cores. It has
no effect on bf16/fp16 checkpoints, since there's no fp8 weight for the fast path to trigger on.

## How to enable it

Turn on **fp8 fast multiply** in Admin → Backends → *your native backend* → Optimizations → Speed
(setting `native_fp8_matmul`, off by default). It takes effect on the next generation, with no
restart. The hardware/torch capability probe still has to pass; on hardware that fails it, the
setting has no effect.

The setting is part of each native backend's configuration, so a local backend and each remote
native worker can differ; a remote worker receives its backend's value with every job, and the
worker's own environment never decides it.

The setting replaces the old `NATIVE_FP8_MATMUL` environment variable, which is no longer read. On
the first boot after the update every existing native backend is seeded once from that variable if
it was set (`auto` is seeded as on); backends created later start from the default, and a variable
that is still set only logs a startup line saying it is ignored.

## LoRA on the fast path

An fp8 or nvfp4 layer cannot have a LoRA baked into its weights without rounding most of the
LoRA away, so PotionUI adds the LoRA on the layer's output instead: `x @ down.T @ up.T`, scaled by
the adapter's strength. With several adapters on one layer, their factors are joined along the
rank axis, so the layer runs two small matrix products and adds the result into its output in
place, once, however many adapters it carries.

The joined factors are prepared on the first forward after a LoRA stack is applied and then reused
on every step. They are rebuilt when the stack changes: a different set of adapters, a changed
strength, or a step-window edge. An adapter outside its step window is not attached at all, and a
zero-strength adapter is skipped, so neither costs anything per step. The joined factors reuse the
adapters' own GPU memory when the LoRA file is already in the compute dtype (usually bf16). An
fp16 or fp32 LoRA file is cast once instead of on every forward.

Both the fp8 (**fp8 fast multiply**, `native_fp8_matmul`) and nvfp4 (**nvfp4 fast multiply**,
`native_nvfp4_matmul`, in the same Speed section) fast paths use this, and so does the dequantize
path.

LoKr adapters run on the activation side too. A LoKr's delta is the Kronecker product of two small
factors, so instead of building that weight-sized delta the layer contracts its input against both
factors in turn, in whichever order costs less, keeping each factor's own low-rank split when the
file has one. A layer that carries a LoKr therefore stays on the fp8, nvfp4 or dequantize path it
would take anyway. Two cases still fall back to the weight-side Kronecker delta, paid per forward: a
LoKr whose factors don't multiply out to the layer's output width or that targets only a slice of a
fused weight, and every LoKr while **Fuse LoRAs into the model** is off.

After each sampling run the log shows one INFO line with how many LoRA layers took each path:

```
lora forward paths (layers): fused-fast 196, fused-fast (+lokr) 28
lora forward paths (layers): dequant (fp8 matmul off) 196, weight-side (lokr) 28
```

`(+lokr)` marks layers whose LoKr ran activation-side on that path; `weight-side (lokr)` marks
layers that fell back to the weight-side Kronecker delta.

To compare against the old per-adapter path, turn off **Fuse LoRAs into the model** in Admin →
Backends → *your native backend* → Optimizations → Speed (setting `native_lora_fused`, on by
default). It takes effect on the next generation.

With it off, the fast paths add each adapter separately (`unfused-fast` in the log), the
dequantize path joins the factors again on every forward, and LoKr adapters go back to the
weight-side Kronecker delta. The setting replaces the old
`NATIVE_LORA_FUSED` environment variable, which is no longer read after the first boot seeds each
existing native backend from it.

## Implementation contracts

The layers live in `src/platform/runtime/native/kernels/` (`fp8_linear.py`, `nvfp4_linear.py`,
`operations.py`); `fast_path(layer)` in `linear.py` reports which branch a layer takes.

- **fp8 `_scaled_mm` preconditions** (any miss falls back to dequantize, and the first miss for each
  distinct reason is logged once per process): the live flag is on and the probe passes; every LoRA
  delta has an output-side form; the weight is `float8_e4m3fn` with a `weight_scale` (int8, ConvRot
  and e5m2 always dequantize); the activation is CUDA float16/bfloat16; both dimensions are
  multiples of 16. A CPU weight is allowed because the call stages it.
- **Kernel-level fallbacks** (the call returns `None` and the caller dequantizes): a weight or input
  scale that is not a single finite positive value, a failed on-demand staging, or a runtime
  `RuntimeError`/out-of-memory from `_scaled_mm`, other than a driver-error OOM the driver-OOM policy
  treats as fatal. Scale validity is memoized per tensor identity and
  version, so an unchanged scale is read from the device once, not every forward.
- **Layout:** the activation is row-major `(M, K)` and the weight is passed as its column-major
  transpose, which is free only when the weight is contiguous, so the call enforces that.
- **Staging:** a weight that is not CUDA-resident is copied to the activation's device for one call
  through the same cast the dequantize path uses; the temporary is a local reference and is dropped
  when the call returns, including on failure.
- **`prepared_linear(sample)`:** prepares the effective operand once for a caller-driven chunk loop
  that shares `sample`'s dtype and device, and nothing outlives the `with` block. Layers without a
  scale, nvfp4 layers and leaves carrying forward hooks (a streaming prefetcher's) pass through to
  ordinary per-chunk dispatch, because the prepared path never goes through `nn.Module.__call__`. A
  kernel rejection on one chunk releases the staged weight first, then downgrades the rest of the
  context to one dequantized operand and never retries the fast path inside it.
- **AWQ `pre_quant_scale`** multiplies the activation first on every path, once per call, never
  doubled by a downgrade.
- **nvfp4 GEMM** runs through `torch.nn.functional.scaled_mm` (`torch._scaled_mm_v2`) with the
  two-level recipe: a block-wise 1x16 e4m3 scale in the swizzled layout plus a per-tensor float32
  scale for each operand. The checkpoint's block scale is passed as stored, with no unblocking.
  Alignment is checked on the packed shapes, so `in_features` must be a multiple of 32 and
  `out_features` a multiple of 16. The packed weight must already be on the GPU (it is not staged),
  and the bias is added after the GEMM rather than passed to the kernel.
- **What `fast_path` does not see:** activation dtype and device, scale values, kernel rejection,
  forward hooks and grad mode. It answers the static question for a layer; the forward decides per
  call.

## Tradeoffs and limitations

- Requires specific hardware: CUDA with compute capability `(8, 9)` or higher (Ada/Hopper/Blackwell)
  and a `torch` build exposing `_scaled_mm`. On unsupported hardware the setting has no effect and the
  dequantize path is used regardless of the setting.
- Only matters for fp8-quantized checkpoints — on any other checkpoint this knob is a no-op since no
  layer is stored as `float8_e4m3fn`.
- No fast path when a runtime LoRA delta is patched onto the layer that can't be expressed on the
  activation side (e.g. a LoKr whose factors don't tile the layer's output, or any LoKr with LoRA
  fusing off); those layers use the dequantize path.
- On-demand weight staging adds one extra host-to-device copy per forward for a streamed leaf that
  isn't already resident/prefetched — the same copy the dequantize path would otherwise pay to
  bring the weight to the activation's device, just handed to the fp8 kernel instead of a dense
  multiply. It does not change what fits in VRAM: no additional weight is kept resident, and the
  temporary is released at the end of the call.
- Activation quantization uses the checkpoint's static `input_scale` when the checkpoint provides
  one (e.g. Klein-fp8), otherwise falls back to a dynamic per-tensor scale computed each forward —
  the latter costs an extra reduction per forward pass.
- Not yet benchmarked on real hardware: this has been unit-tested but not A/B validated for
  wall-clock speedup or output quality drift against the dequantize path on an actual GPU. Validate
  with a same-seed/prompt comparison (setting off vs. on) on your hardware before relying on it.
