---
type: technique
title: Sol-Attn
category_group: Performance
status: needs-gpu-validation
families: [minimax_h3]
authors: []
paper: null
reference_impl: {name: "sol_attn", url: "vendor/sol_attn/", license: "Apache-2.0 (vendored)"}
knobs:
  - key: sparse_attn
    surface: preset
    default: "off"
    effect: "Sparse-attention method select: \"off\", \"sol\" (this technique) or \"sla\" (see sla-attn)."
  - key: sol_attn_tau
    surface: preset
    default: 1.0
    effect: "Sparsity temperature. Larger skips more KV blocks (faster, less accurate)."
  - key: sparse_attn_dense_last_steps
    surface: preset
    default: 2
    effect: "Forces the trailing N sampling steps to run on the normal dense path instead of sparse (shared with SLA)."
related: [sla-attn, first-block-cache, attention-backends]
---

# Sol-Attn

Sol-Attn is an opt-in block-sparse attention path for the native engine, vendored at
`vendor/sol_attn/` (Apache-2.0) and wired into `src/platform/runtime/native/sol_attn.py`. It
summarises every 128-token KV block by its mean vector, scores those summaries against pooled query
blocks, and computes exact attention only over the blocks whose score clears a per-query-block
threshold — plus a local diagonal window and, optionally, a sequence prefix ("sink"), both of which
always get exact attention. It is an **approximation**: the same inputs produce a genuinely
different output than the dense backends in `src/platform/runtime/native/attention`, not merely a
differently-rounded one. That's why nothing runs through it unless a caller explicitly builds a
`SolAttnContext`, and why every preset that exposes it defaults to off.

Today it is wired into exactly one family: MiniMax-H3 (`src/pipelines/pipes/generator/video_minimax_h3/main.py`),
whose packed video+text+audio sequence is long enough for block-sparse routing to pay off.

## When to use it

Use it on MiniMax-H3 generations with a long packed sequence, where trading a small amount of
attention fidelity for speed is acceptable — the same iteration/preview tradeoff FBCache makes.
Leave it off for final renders, or where you need output as close as possible to the dense-attention
baseline.

## How to enable it

```yaml
- name: "generator/video_minimax_h3"
  configuration:
    sparse_attn: "sol"
    sol_attn_tau: 1.0
    sparse_attn_dense_last_steps: 2
```

`sparse_attn` is a method select shared with [SLA](sla-attn.md) — the two backends sit behind one
dispatcher (`src/platform/runtime/native/sparse_attn.py`) and one arch-forward kwarg, so a preset
picks at most one. `sol_attn_tau` is upstream's sparsity temperature (`1.0` is upstream's default; larger skips more KV
blocks). `sparse_attn_dense_last_steps` forces the last N sampling steps to run dense rather than
sparse — the pipe's `is_dense_step` flips `SolAttnContext.dense` per step, so the final steps of the
trajectory (where sparse error is most visible, since there's no remaining noise to mask it) stay
exact.

## Constraints and hardware requirements

Sol-Attn only ever runs when every one of these holds; anything else means the call silently falls
back to the normal dense path:

- CUDA device, `bfloat16` activations only.
- `head_dim` exactly `128` (both backends are written for this width; MiniMax-H3's own attention
  inner dim is 56 heads × 128 = 7168, which is what makes it a fit).
- Compute capability 8.0 or higher.
- Sequence length ≥ 256 tokens (`_MIN_TOKENS`) — below two full routing blocks there's nothing to
  route, so it's skipped rather than disabled.

Two backends exist, selected by the `NATIVE_SOL_ATTN_BACKEND` env var (`flex` default, or `kernel`):
`flex` (`vendor/sol_attn/flex.py`) routes in plain torch through `torch.nn.attention.flex_attention`
and is the only backend that honors `sink_tokens`; `kernel` (`vendor/sol_attn/interface.py`) is
upstream's original CuTe DSL / Triton kernels — upstream itself measures the Triton reference as
*slower* than SDPA on long sequences, so it exists for A/B comparison rather than as the default.

## Failure contract

`sol_attention()` never raises and never propagates a backend failure into the generation. The first
time anything goes wrong for any reason — no CUDA, an unsupported dtype/head_dim, a missing
`triton`, a torch too old for `flex_attention`, a kernel that refuses the GPU, a compile error — it
logs ONE warning naming the reason and **latches off for the rest of the process**
(`sol_attn_disabled_reason()` reports why). Every later call in that process returns `None`
immediately and the caller falls back to its normal dense attention; a generation on unsupported
hardware just runs slower with one log line, never a crash.

## VRAM accounting

Sol-Attn's routing and padding allocate several full-size extra QKV copies the dense path never
needs. `estimate_transient_gb()` (`sol_attn.py`) counts those allocations directly off
`vendor/sol_attn/flex.py`'s own materializations (a worst-case estimate — SDPA's own strided-input
materialization on the dense path can make the true marginal lower, never higher) and the pipe feeds
it to `place_dit_for_sequence`'s `reserve_gb` so the DiT is placed leaving room for the extra copies.
Skipping this reservation is what produces an OOM partway through sampling rather than at load time.

## Tradeoffs and limitations

- Approximate by design: routing decisions are made per query/KV block, not per token, so it can
  miss fine-grained attention patterns a dense pass would catch.
- MiniMax-H3 only today — no other native family wires a sparse-attention context through its arch
  forward.
- Requires bf16 + head_dim 128 + compute capability 8.0+; anything else silently falls back to dense
  with no configuration needed to handle it.
- Measured once on real hardware: 1.29x wall-clock over the sage2 dense baseline at ~43k rows
  (including one-time compile warmup), where its ~5.3 GB transient reserve likely forced partial DiT
  residency — see [SLA](sla-attn.md) for the alternative whose reserve is an order of magnitude
  smaller. Output-quality judgment is still open.
