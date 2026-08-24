---
type: technique
title: SLA (Sparse–Linear Attention)
category_group: Performance
status: needs-gpu-validation
families: [minimax_h3]
authors: []
paper: {name: "SLA (thu-ml)", url: "https://github.com/thu-ml/SLA"}
reference_impl: {name: "sla_attn", url: "vendor/sla_attn/", license: "Apache-2.0 (LightX2V kernels, vendored via PlagueKind's adaptation)"}
knobs:
  - key: sparse_attn
    surface: preset
    default: "off"
    effect: "Sparse-attention method select: \"off\", \"sol\" (see sol-attn) or \"sla\" (this technique)."
  - key: sla_sparsity
    surface: preset
    default: 0.9
    effect: "Fraction of key blocks skipped. Top-k keeps the rest; the SLA turbo LoRA is distilled against ~0.85."
  - key: sla_block_size
    surface: preset
    default: 64
    effect: "Sequence tokens per routing block (64 or 128). 64 is the audio-safe choice."
  - key: sparse_attn_dense_last_steps
    surface: preset
    default: 2
    effect: "Forces the trailing N sampling steps to run on the normal dense path instead of sparse (shared with Sol-Attn)."
related: [sol-attn, first-block-cache, attention-backends]
---

# SLA (Sparse–Linear Attention)

SLA is the second opt-in block-sparse attention path for the native engine, vendored at
`vendor/sla_attn/` (the LightX2V forward kernel and block-selection routing, Apache-2.0, taken via
PlagueKind's adaptation which fixes three upstream bugs and adds a consumer-Blackwell launch-config
ladder) and wired into `src/platform/runtime/native/sla_attn.py`. Where [Sol-Attn](sol-attn.md)
scores KV-block summaries against a per-query-block *threshold*, SLA mean-pools queries and a
smoothed key into blocks, scores them with one small matmul, and keeps a fixed **top-k fraction**
(`1 - sla_sparsity`) of key blocks per query block. Nothing is trained and nothing is loaded — the
sparsity pattern is decided at runtime from q and k.

The reason it exists alongside Sol-Attn is twofold:

1. **It is the inference half of the lightx2v SLA turbo LoRA.** `lightx2v/Minimax-h3-Turbo-SLA`
   is a 4-step distillation trained to *tolerate* ~85% attention sparsity — the LoRA alone gives no
   speedup, and this kernel is the sparse execution it was distilled against. Upstream reports
   ~2.5x end-to-end on an RTX 5090 for the pair.
2. **Its transient VRAM cost is an order of magnitude smaller.** The kernel consumes the model's
   own pre-transpose `(B, S, H, D)` layout directly: no padding, no permute copies — the only
   full-size materialization is one `v.contiguous()` (v arrives as a chunk view of the fused qkv
   projection). `estimate_transient_gb()` counts ~0.81 GB at 43k rows against Sol-Attn's ~5.3 GB,
   which at long sequence lengths is the difference between a fully-resident DiT and streaming.

The packed `[text | cond | audio]` prefix is pinned into every query block's selection
(`SlaAttnContext.prefix_tokens`, derived from the window layout the same way Sol-Attn's sink is).
Without the pin, plain top-k routinely starves the audio rows — they are ~1% of the packed sequence,
so nothing keeps them and the soundtrack degrades while the video still looks fine. Pinned blocks
are added on top of the top-k budget rather than displacing video blocks.

## When to use it

The primary pairing is 4-step turbo runs with the SLA turbo LoRA loaded, at 0.85–0.90 sparsity.
Without that LoRA it is the same speed-for-fidelity trade Sol-Attn is — never a quality improvement,
off by default, and not for final renders. Prefer it over Sol-Attn when VRAM headroom is tight
(the reserve difference above); keep Sol-Attn for A/B and for threshold-style routing.

## How to enable it

```yaml
- name: "generator/video_minimax_h3"
  configuration:
    sparse_attn: "sla"
    sla_sparsity: 0.9
    sla_block_size: 64
    sparse_attn_dense_last_steps: 2
```

`sla_block_size` matters far more for audio than video: MiniMax-H3 packs audio at 80 rows per
second, so a 128-row block forces ~1.6 s of audio through one attention pattern — upstream testing
found speech turned robotic at 128 and clean at 64, for about 2% more time. Use 128 only when the
audio track does not matter. Below roughly 0.6 sparsity the kernel is *slower* than dense attention
— a low value is a loss, not a safe fallback.

## Constraints and hardware requirements

SLA only ever runs when every one of these holds; anything else silently falls back to dense:

- CUDA device, `bfloat16` or `float16` activations.
- `head_dim` exactly `128`, compute capability 8.0+.
- `triton` importable (the kernels are Triton; the launch-config ladder probes
  warp/stage configs so consumer GPUs with less shared memory than datacentre parts still launch).
- Sequence length ≥ 8192 tokens — below that, block selection costs more than it saves, so the call
  is skipped rather than the feature disabled.
- `sla_sparsity > 0` — zero sparsity means nothing to skip, so the exact dense path runs instead.

## Failure contract

Identical to Sol-Attn's: `sla_attention()` never raises. The first failure of any kind logs ONE
warning naming the reason and latches the feature off for the rest of the process
(`sla_attn_disabled_reason()` reports why); every later call returns `None` immediately and the
caller falls back to dense. The two methods latch independently.

## Tradeoffs and limitations

- Approximate by design, and the approximation differs from Sol-Attn's: a fixed top-k budget per
  query block rather than a threshold, so its error is bounded per block but blind to how many
  blocks *should* have qualified.
- MiniMax-H3 only today, main DiT blocks only (the token refiner always runs dense).
- Not yet benchmarked on this codebase's hardware — upstream numbers (~44 → ~25 s/it at 768p/15s
  on a 5090 at 0.90 sparsity with the SLA LoRA; attention is only part of the step, Amdahl ceiling
  ~3.2x) are the reference until a local A/B lands.
