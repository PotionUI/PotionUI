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
  - key: NATIVE_FP8_MATMUL
    surface: env
    default: "off"
    effect: "Runs fp8-quantized linear layers as real fp8 GEMMs instead of dequantizing to bf16/fp16 first"
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
that call, exactly like any other fast-path rejection.

## When to use it

Only relevant if you're running an fp8-quantized checkpoint (e.g. an fp8-scaled or mixed
fp8/nvfp4 model) on Ada, Hopper, or Blackwell-class GPUs, which have real fp8 tensor cores. It has
no effect on bf16/fp16 checkpoints, since there's no fp8 weight for the fast path to trigger on.

## How to enable it

Set the environment variable before starting the API server:

```bash
export NATIVE_FP8_MATMUL=on
```

`auto` currently behaves identically to `on` (both require the same hardware/torch capability probe
to pass; there is no separate VRAM-based heuristic for this knob). Any other value is treated as
`off` with a warning logged.

## Tradeoffs and limitations

- Requires specific hardware: CUDA with compute capability `(8, 9)` or higher (Ada/Hopper/Blackwell)
  and a `torch` build exposing `_scaled_mm`. On unsupported hardware the flag has no effect and the
  dequantize path is used regardless of the setting.
- Only matters for fp8-quantized checkpoints — on any other checkpoint this knob is a no-op since no
  layer is stored as `float8_e4m3fn`.
- No fast path when a runtime LoRA delta is patched onto the layer that can't be expressed as an
  output-side low-rank branch (e.g. LoKr); those layers always use the dequantize path.
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
  with a same-seed/prompt comparison (flag off vs. on) on your hardware before relying on it.
