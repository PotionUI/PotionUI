---
type: technique
title: Streaming Prefetch Overlap
category_group: Performance
status: needs-gpu-validation
families: [all-native]
authors: []
paper: null
reference_impl: null
knobs:
  - key: NATIVE_STREAM_PREFETCH
    surface: env
    default: "off"
    effect: "Overlaps the next streamed layer's host-to-device weight copy with the current layer's compute"
related: [torch-compile]
---

# Streaming Prefetch Overlap

When a model doesn't fully fit in VRAM, PotionUI keeps as many layers resident on the GPU as fit and
streams the rest from pinned host RAM on demand — copying each streamed layer's weights to the GPU
right before it runs, then freeing them again. By default that copy happens synchronously: the GPU
sits idle while the next layer's weights are transferred in. Streaming prefetch overlap changes
this by staging the *next* streamed layer's weights on a separate CUDA stream while the *current*
layer is still computing, so the transfer and the compute happen concurrently instead of back to
back.

This only affects generations running under partial (streamed) GPU residency — the low-VRAM path.
When a model is fully resident on the GPU, there's nothing to stream, and this setting has no
effect.

## When to use it

Use it when generating with a model that doesn't fit entirely in VRAM (partial-residency/streamed
loading) and you want to reduce the per-layer transfer stall. It has no benefit on generations where
the model fits fully on the GPU.

## How to enable it

Set the environment variable before starting the API server:

```bash
export NATIVE_STREAM_PREFETCH=on
```

`auto` currently behaves identically to `on` (no separate heuristic yet). Any other value is treated
as `off` with a warning logged.

## Tradeoffs and limitations

- Only applies during partial-residency (streamed) generation; irrelevant when the model is fully
  GPU-resident.
- Requires enough free GPU memory to hold one extra layer's weights at a time (the "in-flight"
  prefetched layer alongside the currently computing one) — on an already tightly-budgeted
  low-VRAM configuration this could increase peak VRAM slightly compared to the non-overlapped path.
- Not yet benchmarked on real hardware: the CUDA stream/event choreography is unit-tested with fakes
  but the actual wall-clock improvement on a streamed generation has not been measured on a GPU as
  of this writing. Validate by comparing wall-clock with the flag off vs. on on a real
  partial-residency generation before relying on it.
