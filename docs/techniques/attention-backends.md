---
type: technique
title: Attention Backends
category_group: Performance
status: stable
families: [all-native]
authors: []
paper: null
reference_impl: null
knobs:
  - key: NATIVE_ATTENTION
    surface: env
    default: unset (auto-select)
    effect: "Pin a specific attention backend by name (sdpa, sage, sage2, sage3, flash, sparge)"
  - key: admin_pin
    surface: admin
    default: unset (auto-select)
    effect: "Pin a backend from Admin -> Backends -> Optimizations without setting an env var"
related: [nan-watchdog]
---

# Attention Backends

Every native diffusion family routes its attention computation through one shared dispatcher.
Instead of hard-coding a single attention implementation, PotionUI probes what your hardware and
Python environment actually support, then picks the fastest option automatically. Faster attention
kernels reduce generation time without changing the sampler, schedule, or any other setting — the
speedup is "free" as long as the kernel is available and the call shape qualifies (no attention
mask, fp16/bf16 activations).

Five backends are eligible for automatic selection, in priority order from fastest to the universal
fallback: `sage3` > `sage2` > `sage` > `flash` > `sdpa`. `sdpa` (PyTorch's built-in
`scaled_dot_product_attention`) is always available and is the numerical reference every other
backend is checked against — a masked or fp32 call transparently falls back to it regardless of
which backend is selected, so correctness never depends on which kernel ran. A sixth backend,
`sparge`, exists but is never chosen automatically; it must be pinned explicitly (see below).

Backend requirements, high to low priority:

- **`sage3`** (SageAttention3) needs an exact-match Blackwell compute capability — `(10,0)`
  (datacenter B100/B200), `(12,0)` (consumer RTX 50-series), or `(12,1)` (Blackwell Ultra) — plus
  CUDA runtime >= 12.8 and Python >= 3.13. It uses hardware FP4 tensor cores unique to those dies,
  so it will not run on Ampere/Ada/Hopper or older Blackwell-adjacent cards, and a Python
  interpreter below 3.13 is treated as unsupported rather than silently degrading.
- **`sage2`** needs an SM 8.0+ (Ampere or newer) GPU and the `sageattention` package installed.
- **`sage`** (the original SageAttention) has a lower hardware floor than sage2/sage3.
- **`flash`** needs FlashAttention installed and a supported GPU.
- **`sdpa`** always works — CPU or GPU, any hardware.

If none of the accelerated kernels are installed or supported, generation silently runs on `sdpa`.

## When to use it

Leave the backend on auto-select for normal use — PotionUI already picks the fastest kernel your
machine supports, and every candidate is numerically near-lossless (masked/fp32 calls fall back to
`sdpa` automatically, so switching backends should not change output quality in the common case).
Pin a specific backend only when you need to: force `sdpa` to rule out an attention-kernel-specific
bug while debugging, benchmark backends against each other, or opt into `sparge` for extra speed on
supported hardware after checking its outputs.

## How to enable it

No action is needed for automatic selection — this is on by default. To pin a backend explicitly:

```bash
export NATIVE_ATTENTION=sage2   # or sdpa, sage, sage3, flash, sparge
```

Or pin it from the admin panel: **Admin -> Backends -> Optimizations**, under the attention backend
setting for the relevant backend instance. The panel accepts the same backend names, including
`sparge`, which auto-selection never picks on its own.

### Using `sparge` (SpargeAttention)

`sparge` is training-free **sparse** attention: a two-stage online filter predicts and skips
low-contribution attention blocks. Unlike the other backends, it is approximate rather than
numerically near-lossless — output quality depends on content and on its `topk` sparsity setting
(defaults to `0.5`, the upstream tune-free default). Because of that, it is only ever used when you
explicitly request it (`NATIVE_ATTENTION=sparge` or an admin pin) — auto-selection will never choose
it for you, no matter how fast or well-supported it is on your hardware. It additionally requires a
sequence length >= 128 and a head dimension of 64 or 128, and only compiles for Ampere/Ada/Hopper
(compute capability major 8 or 9) — it does not currently support Blackwell. Calls outside those
bounds fall back to `sdpa`.

## Tradeoffs and limitations

- Backend choice does not change how you configure a generation — it only affects speed and, for
  `sparge`, a real quality/speed tradeoff you opt into knowingly.
- `sage3` and `sparge` both have narrow hardware/software gates; most setups will land on `sage2`,
  `sage`, `flash`, or the universal `sdpa` fallback.
- `sage` needed a numerical workaround for one architecture: its kernel can overflow fp16 internally
  on very large activations at large joint-sequence attention shapes, producing NaNs. PotionUI's
  dispatcher pre-scales the value tensor down by a constant factor before the call and scales the
  output back up afterward (mathematically exact, no extra GPU sync), with a final `nan_to_num` as a
  zero-cost safety net — so this is handled for you and does not require any setting.
- `sparge`'s quality varies by content; benchmark and inspect outputs on your own workloads before
  relying on it for production generations.
