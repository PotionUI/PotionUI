"""Sequence-length-aware DiT placement for LTX video generation.

Root cause: ``generator/txt2vid_ltx`` and ``generator/video_ltx`` full-pin the
~23.3GB fp8 LTX-2.3 DiT for the whole sampling loop via a plain
``bundle.dit.move_to(device)`` (see each pipe's ``generate_one``). That leaves
a FIXED activation budget (~8-9GB on a 32GB card) while the DiT's own
attention/RoPE/hidden-state activations grow ~linearly with the video's
sequence length ``S`` (video tokens, plus appended audio tokens when audio
generation is on -- both ride the same packed sampler state, see
``video_ltx/main.py``'s module docstring). A few seconds of 720x1280 fits
easily; ~40s does not -- the fixed budget is exceeded and the attention
forward OOMs.

ComfyUI survives this by pinning only as many weights as fit and streaming the
rest per-layer from CPU once the estimated activation need says so (its
"lowvram" machinery). PotionUI's native engine already has the equivalent
building blocks -- ``NativeModel.stream_to`` + ``memory/partial.py``'s
leaf-splitting -- but the two LTX generator pipes never called them; they have
no ``NativeGenerator`` instance to ask (unlike every other native family, they
run their own denoise loop directly against the loaded bundle).

:func:`place_dit_for_sequence` is the fix: estimate THIS generation's
activation reserve from its actual sequence length
(:func:`estimate_activation_reserve_gb`), and only stream the excess DiT
weight past what fits given the reserve -- mirroring
``NativeGenerator._move_dit_to_gpu`` / ``_stream_dit_to_gpu``'s OOM-degrade
ladder (``src/platform/runtime/native/engine.py``), but parameterised by an
externally supplied reserve instead of a resolution-only headroom, and
deliberately re-implemented here (not imported) since those are private
methods on ``NativeGenerator`` shared by every native family -- refactoring
them was out of scope for an LTX-only fix. Every actual mechanism this
delegates to (``NativeModel.move_to``/``stream_to``, ``memory/partial.py``'s
greedy leaf split, ``GpuResidencyRegistry``, ``free_vram_gb``,
``minimum_inference_memory_gb``) is reused unchanged.

Short clips (a few seconds) always see the exact previous behaviour: the full
DiT comfortably fits the resolution-scaled reserve, so the decision is
"resident" and the call sequence is byte-identical to the old plain
``move_to``. Only once the sequence length pushes the estimated reserve high
enough that the full checkpoint would not leave room for it does this degrade
to partial residency.

This module deliberately does NOT touch ``src/platform/runtime/native/arch/
ltx/rope.py`` or the RoPE construction in ``arch/ltx/model.py`` -- a parallel
effort owns RoPE caching there. The RoPE-related reserve terms below are
independently re-derived constants, not imports from that code.

Whenever the decision comes out "resident" (fresh or the warm-residency fast
path), :func:`place_dit_for_sequence` also calls
``optimizations/compile.maybe_compile_dit`` -- the same gated, reversible
regional ``torch.compile`` ``NativeGenerator.sample`` applies to the image
path (``engine.py``'s own ``_maybe_compile``). These BYO-loop pipes have no
``NativeGenerator`` instance to call that private method on, so this is the
seam that gives LTX/DFR/MiniMax-H3 compile parity with the image families;
"partial" residency is never compiled (the gate itself would refuse it too --
this is a fast local skip, not a second source of truth).

:func:`place_dit_for_sequence` also takes an optional ``reserve_gb`` on top of
the token-derived ``activation_reserve``. The token-only reserve is wrong for a
caller whose GPU work AFTER placement isn't proportional to sequence length --
the detailer's per-tube VAE decode: a tiny
tube (~4k tokens) gets a near-floor activation reserve (0.5GB), so the DiT
went FULLY resident (its 23GB weight comfortably fit ``free - 0.5GB``), which
is CORRECT for the denoise step that follows immediately, but leaves the
tube's subsequent VAE decode with almost no headroom (observed: 27.87GB
allocated, 109MB free, decode OOM'd on a ~136MB allocation). ``reserve_gb``
lets a caller fold in headroom for GPU work that ISN'T the DiT forward this
function is sizing for -- it's added straight onto ``activation_reserve``
before the resident-vs-partial decision, so a caller with real post-placement
VRAM needs (like a decode) can force partial residency (streaming just
``weight_budget`` GB of the DiT) even when the token count alone would have
said "plenty of room, pin it all." Zero-token / decode-less callers
(txt2vid_ltx, video_ltx) never pass it, so their behavior is unchanged
(``reserve_gb`` defaults to 0.0).

**The estimate never refuses a generation.** It budgets placement and, when
the request over-commits the card, warns and streams anyway. An estimate is a
model; this one has been wrong in both directions, and the version that
raised turned away clips that had been running. The real allocator gets the
last word in :func:`guard_sampling_oom`, the ladder every caller wraps its
sampling forward in: reclaim the allocator pool, shed every resident weight
to host RAM, retry after each, and only then raise
:class:`SamplingOutOfMemory` with numbers measured at the failure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Literal

import torch

from src.pipelines.outputs import GenerationExecutionError
from src.platform.observability.profiling import get_profiler
from src.platform.runtime.native.memory.residency import (
    effective_free_vram_gb,
    free_vram_gb,
    get_residency_registry,
    minimum_inference_memory_gb,
)
from src.platform.runtime.native.optimizations.compile import maybe_compile_dit
from vendor.gpl.comfyui.ops import (
    _fp8_matmul_enabled,
    _nvfp4_matmul_enabled,
    partition_output_branch_deltas,
)

logger = logging.getLogger(__name__)


class SamplingOutOfMemory(GenerationExecutionError):
    """The sampling forward ran out of VRAM and the degrade ladder is spent --
    it OOM'd with every DiT weight already streamed from host RAM, so there is
    no weight left to shed and no retry left to make.

    This is the ONLY place a token-count/VRAM mismatch becomes a user-facing
    failure. The pre-flight estimate deliberately does not refuse: it sizes
    the weight budget and warns when the request over-commits the card, then
    lets placement stream as much as it must and lets the real allocator have
    the final word (:func:`guard_sampling_oom`). An estimate is a model and
    models are wrong in both directions; refusing on one turned away clips
    that ran fine.

    Every number on it is MEASURED at the moment of failure, not predicted:
    ``free_raw_gb``/``free_effective_gb`` are read after the last retry died.
    ``activation_reserve_gb``/``extra_reserve_gb`` are carried along as what
    the estimate HAD thought, so a report can be compared against reality.

    Derives from :class:`GenerationExecutionError` (not a bare
    ``RuntimeError``) so ``GenerationEngine`` surfaces ``message`` to the user
    verbatim -- ``error_classification.classify_generation_error`` only
    substitutes its own generic summary when an exception carries no
    ``.detail``.
    """

    def __init__(
        self, message: str, *, detail: str, free_raw_gb: float, free_effective_gb: float,
        activation_reserve_gb: float, extra_reserve_gb: float,
        video_tokens: int, audio_tokens: int,
    ) -> None:
        super().__init__(message, detail=detail)
        self.free_raw_gb = free_raw_gb
        self.free_effective_gb = free_effective_gb
        self.activation_reserve_gb = activation_reserve_gb
        self.extra_reserve_gb = extra_reserve_gb
        self.video_tokens = video_tokens
        self.audio_tokens = audio_tokens


_BYTES_PER_GB = 1024 ** 3

# A family's "what WOULD fit this card" sentence. Called with the free VRAM to
# judge against plus the reserve breakdown and token total that free number has
# to cover, so ONE function serves both consumers: the over-commit warning
# (free = the pre-flight reading) and the post-OOM error (free = what was
# actually measured when sampling died). Core has none of its own -- only a
# family knows how its tokens map back to seconds and pixels
# (MiniMax-H3's ``_fit_hint``/``_format_fit_hint``).
FitHint = Callable[..., str]


def _render_fit_hint(
    fit_hint: "FitHint | None", free_gb: float, *,
    activation_reserve_gb: float, extra_reserve_gb: float, tokens: int,
) -> str:
    """The family's hint text, or ``""``. A hint that raises is swallowed: it
    is a nicety appended to a message, and must never replace the real one."""
    if fit_hint is None:
        return ""
    try:
        return fit_hint(
            free_gb, activation_reserve_gb=activation_reserve_gb,
            extra_reserve_gb=extra_reserve_gb, tokens=tokens,
        ) or ""
    except Exception:  # noqa: BLE001 -- see the docstring
        logger.debug("[LTX PLACEMENT] fit hint failed; continuing without it", exc_info=True)
        return ""

# LTX DiT inner (hidden) dimension shared by the 19B and 22B (2.3) variants
# (docs/models/ltx.md). Video and (when audio generation is on) audio tokens
# both ride this same dim through attention -- video_ltx/main.py's module
# docstring: "LTX's video token dim ... equals its packed audio dim". This is
# the DEFAULT for :func:`estimate_activation_reserve_gb`/
# :func:`place_dit_for_sequence`'s ``inner_dim`` parameter, so every existing
# LTX call site (which never passes it) sees byte-identical behaviour; a
# family whose attention inner dim differs from its residual-stream width
# (MiniMax-H3: 7168 attn inner vs. 5376 hidden -- the attention transient and
# per-token hidden-state buffers are sized off the WIDER of the two, since
# that upper-bounds every per-token allocation this reserve accounts for)
# passes its own value explicitly.
_LTX_INNER_DIM = 4096
# RoPE operates on half the inner dim (real/imag pair per rotated axis).
_LTX_ROPE_HALF_DIM = _LTX_INNER_DIM // 2

# Per-token activation-reserve terms (bytes/token), calibrated against a
# measured 40s/720x1280 LTX-2.3 OOM: S ~= 110,880
# tokens peaked ~9.4GB over the resident DiT weights, decomposed as:
#   attention transient    ~6 * S * inner_dim * 2B (bf16)      ~= 5.2GB
#   RoPE fp32 build spike  ~3 * S * rope_half_dim * 4B (fp32)  ~= 2.6GB
#   held bf16 cos/sin      ~2 * S * rope_half_dim * 2B (bf16)  ~= 0.9GB
#   hidden states (x)      ~1 * S * inner_dim * 2B (bf16)      ~= 0.9GB
# batch=1 throughout: PotionUI's true-CFG guider runs the cond/uncond branches
# SEQUENTIALLY (sampling/denoise_loop.py), never as a batched forward, so a
# batch=2 term would reserve for a branch that is never actually resident
# alongside its sibling. Kept as a function of ``inner_dim`` (RoPE half-dim
# derived as ``inner_dim // 2``, the same ratio LTX's own constants use) so a
# non-LTX caller gets a reserve scaled to ITS attention width rather than
# LTX's -- see :data:`_LTX_INNER_DIM`'s docstring above.
def _activation_reserve_bytes_per_token(inner_dim: int, ffn_dim: int | None = None) -> int:
    rope_half_dim = inner_dim // 2
    total = (
        6 * inner_dim * 2         # attention transient (bf16)
        + 3 * rope_half_dim * 4   # RoPE fp32 build spike
        + 2 * rope_half_dim * 2   # held bf16 cos/sin
        + 1 * inner_dim * 2       # hidden states (x)
    )
    if ffn_dim:
        total += _ffn_transient_bytes_per_token(ffn_dim)
    return total


_ACTIVATION_RESERVE_BYTES_PER_TOKEN = _activation_reserve_bytes_per_token(_LTX_INNER_DIM)

# SwiGLU FFN transient (follow-up to a real H3 turbo-LoRA OOM, GPU trace:
# dit_weight_gb=19.52, activation_reserve_gb=4.44 (this term absent), free
# ~27.4GB -> chose "resident"; died on "Tried to allocate 1.03 GiB" with the
# allocated-at-death byte count EXACTLY matching `S * 2*ffn_dim * 2B` -- the
# SwiGLU `fc1` fused value|gate output, which this family's own
# attention-shaped reserve above never modeled at all). `None`/0 (the
# default everywhere this isn't explicitly passed) is a no-op -- LTX's own
# FeedForward is a plain GELU projection, not a SwiGLU, and every existing
# LTX call site never passes `ffn_dim`, so this is additive-only for
# families that opt in.
#
# Three eager-mode allocations are alive at the SwiGLU peak, all `bf16`:
# `fc1`'s fused `value|gate` output (`2*ffn_dim` wide), `SiLU(gate)`
# (`ffn_dim` wide), and the `value * SiLU(gate)` product (`ffn_dim` wide) --
# `(2 + 1 + 1) * ffn_dim = 4*ffn_dim` elements/token, ahead of `fc2`'s much
# narrower (`hidden_size`-wide) output. Not invented: for H3's own
# `ffn_dim=14336`, `2*ffn_dim*2B` alone (just the fc1 output, the first and
# largest of the three) already reproduces the observed 1.03 GiB failing
# allocation at `S~=19284` bit-for-bit -- the full `4*ffn_dim` chain is a
# conservative bound built on top of a verified single term, not a guess.
def _ffn_transient_bytes_per_token(ffn_dim: int) -> int:
    return 4 * ffn_dim * 2

# LoRA cost terms. A resident runtime LoRA delta (quantized-storage Linears
# -- lora/apply.py's ``_needs_runtime_deltas``) is applied per forward by
# ``vendor/gpl/comfyui/ops.py`` on one of two sides, and the two cost
# completely different shapes of memory:
#
#   * ACTIVATION side, the default for every plain (non-LoKr) delta:
#     ``linear_with_lora_deltas`` -> ``_add_lora_output_branch`` accumulates
#     ``(x @ down.T) @ up.T`` straight INTO the layer's own ``F.linear``
#     output, walking the token rows in chunks bounded by
#     ``_NVFP4_LORA_BRANCH_CHUNK_BYTES`` (32MiB). Nothing here scales with
#     ``S``: the live intermediates are ``(chunk_rows, rank)`` and
#     ``(chunk_rows, out_features)``, tens of MB whatever the token count,
#     already covered by :data:`_ACTIVATION_RESERVE_FLOOR_GB`. Per-token
#     cost: ZERO.
#   * ACTIVATION side through a native GEMM fast path
#     (``Fp8ScaledLinear._forward_scaled_mm`` /
#     ``Nvfp4Linear._forward_nvfp4_scaled_mm``, each behind its own env gate
#     and hardware probe, both ``off`` by default): those add
#     ``_lora_output_branch``'s RETURN VALUE to the raw GEMM output, and
#     that function does still preallocate a full ``(S, out_features)``
#     ``total`` at compute dtype -- with the ``out + total`` sum a second
#     buffer of the same shape alive at the same moment, hence
#     :data:`_LORA_OUTPUT_BUFFER_ALLOCATIONS`. Per-token cost: that, for the
#     WIDEST Linear that can actually reach the fast path
#     (:func:`_linear_takes_gemm_fast_path`).
#   * WEIGHT side, the fallback for a delta with no output-side form (LoKr,
#     which ``_deltas_output_branch_ok`` rejects): ``apply_lora_deltas``
#     clones the materialised ``(out, in)`` weight and builds an
#     ``(out, in)`` delta beside it. Weight-shaped, NOT token-shaped -- a
#     flat reserve of twice the widest such Linear's weight, independent of
#     ``S``, and only one Linear's forward is alive at a time.
#
# The estimate used to charge a flat ``4 * inner_dim * 2`` bytes/token
# whenever ANY runtime LoRA was resident, back when every quantized-Linear
# delta went through the weight-side path and only the GEMM fast paths had
# an output branch. On an 8s MiniMax-H3 clip (S~61.5k, inner_dim 7168) that
# stale term alone was ~3.8GB, enough to refuse a clip that fits.
_COMPUTE_DTYPE_BYTES = 2
# ``total`` inside ``_lora_output_branch`` plus the ``out + total`` result at
# its call site -- both ``(S, out_features)``, both alive at once.
_LORA_OUTPUT_BUFFER_ALLOCATIONS = 2
# ``weight.clone()`` plus the ``(out, in)`` delta built beside it in
# ``apply_lora_deltas``.
_LORA_WEIGHT_SIDE_ALLOCATIONS = 2


@dataclass(frozen=True)
class DitLoraProfile:
    """What the runtime LoRA deltas resident on a DiT actually cost.

    Produced by :func:`_dit_lora_profile` from a walk of the DiT's Linears;
    the default instance (:data:`_NO_LORA`) is what a DiT with no runtime
    LoRA yields and contributes nothing to any estimate. In-place-baked LoRA
    (float storage) never sets ``lora_deltas`` at all, so it correctly
    profiles as inactive -- matching its zero forward-time cost.

    ``output_buffer_out_features`` is 0 unless some Linear can genuinely
    reach the preallocating ``_lora_output_branch``; ``weight_side_bytes``
    is 0 unless a LoKr-shaped delta is resident; ``delta_bytes`` is the
    ``up``/``down`` pairs' own resident VRAM, invisible to a DiT's
    ``estimated_vram_gb`` (the base checkpoint's file size) but genuinely
    occupying the card alongside the base weights.
    """

    output_buffer_out_features: int = 0
    weight_side_bytes: int = 0
    delta_bytes: int = 0
    active: bool = False

    @property
    def delta_gb(self) -> float:
        return self.delta_bytes / _BYTES_PER_GB

    @property
    def output_buffer_bytes_per_token(self) -> int:
        return _LORA_OUTPUT_BUFFER_ALLOCATIONS * self.output_buffer_out_features * _COMPUTE_DTYPE_BYTES


_NO_LORA = DitLoraProfile()

# Multiplicative safety margin over the raw per-token estimate -- covers
# allocator fragmentation, cuBLAS/cuDNN workspace, and any minor uncounted
# term. Multiplicative (not a flat add) so it scales with S instead of
# vanishing at the high end where it matters most.
_ACTIVATION_SAFETY_MARGIN = 1.15

# Floor for tiny sequences (a handful of low-res frames) -- allocator/cuBLAS
# scratch never shrinks to zero regardless of S.
_ACTIVATION_RESERVE_FLOOR_GB = 0.5


def estimate_activation_reserve_gb(
    video_tokens: int, audio_tokens: int = 0, *, lora: DitLoraProfile = _NO_LORA,
    inner_dim: int = _LTX_INNER_DIM, ffn_dim: int | None = None,
) -> float:
    """Estimate the DiT-forward activation VRAM reserve for one sampling step.

    ``video_tokens`` is the base + appended-conditioning video token count
    (``t_lat * h_lat * w_lat`` for ``txt2vid_ltx``; ``prepared.base_tokens +
    prepared.n_extra`` for ``video_ltx``); ``audio_tokens`` is the appended
    audio token count when audio generation is enabled (0 otherwise) -- both
    ride the SAME packed sampler state and so share this one reserve.

    ``lora`` is the placed DiT's :class:`DitLoraProfile` (see the LoRA cost
    terms above): a Linear that can reach the preallocating GEMM-fast-path
    output branch adds a per-token term, a resident LoKr delta adds a flat
    weight-shaped one, and a plain delta on the chunked activation-side path
    -- the common case -- adds neither. The default empty profile reproduces
    the no-LoRA formula exactly.

    ``inner_dim`` defaults to LTX's own attention inner dimension (see
    :data:`_LTX_INNER_DIM`'s docstring) so every existing call site is
    byte-identical; a non-LTX caller passes its own attention inner
    dimension to size the per-token reserve for its own DiT instead.

    ``ffn_dim`` (default ``None``, a no-op) adds the SwiGLU FFN transient
    (see :func:`_ffn_transient_bytes_per_token`) for a family whose
    feed-forward peaks at a width the attention-shaped terms above don't
    cover -- MiniMax-H3's own ``14336``, verified against a real OOM trace.
    Every existing LTX call site never passes this, so their estimate is
    unchanged.
    """
    s = max(0, int(video_tokens)) + max(0, int(audio_tokens))
    bytes_per_token = _activation_reserve_bytes_per_token(inner_dim, ffn_dim)
    bytes_per_token += lora.output_buffer_bytes_per_token
    raw_gb = (s * bytes_per_token + lora.weight_side_bytes) / _BYTES_PER_GB
    return max(_ACTIVATION_RESERVE_FLOOR_GB, raw_gb * _ACTIVATION_SAFETY_MARGIN)


def _linear_takes_gemm_fast_path(linear: Any) -> bool:
    """True iff ``linear``'s forward can reach ``vendor/gpl/comfyui/ops.py``'s
    ``_lora_output_branch`` -- the one LoRA path that still preallocates an
    ``(S, out_features)`` buffer.

    Only the two native ``_scaled_mm`` GEMM fast paths call it, and each is
    reached solely when its own env gate plus hardware probe say so; every
    other quantized Linear takes the dequant path, whose plain deltas ride
    the chunked ``_add_lora_output_branch`` and allocate nothing
    S-proportional. The gates are asked through the vendored predicates
    rather than re-read here, so the policy has one source of truth. A layer
    is nvfp4 or fp8-scaled by the same state its own forward branches on
    (``_is_nvfp4``, ``weight_scale``), never by family or class name.
    """
    if getattr(linear, "_is_nvfp4", False):
        return _nvfp4_matmul_enabled()
    if getattr(linear, "weight_scale", None) is not None:
        return _fp8_matmul_enabled()
    return False


def _dit_lora_profile(dit: Any) -> DitLoraProfile:
    """Profile the runtime LoRA deltas resident on ``dit.module``'s Linears
    (``lora_deltas``, set by ``lora/apply.py``'s ``_needs_runtime_deltas``
    path for quantized-storage weights).

    Each Linear's deltas are split the way its own forward splits them, with
    ``partition_output_branch_deltas`` -- the same function
    ``linear_with_lora_deltas`` calls -- so a stack mixing one LoKr adapter
    with two plain ones is charged for exactly what each side costs rather
    than for whichever kind happens to be first.

    ``delta_bytes`` is the reason a resident-vs-partial decision cannot judge
    on ``dit.estimated_vram_gb`` alone: that is the base checkpoint's own
    file size and has no way to know a LoRA was ever applied, so it
    under-budgets by exactly the delta bytes whenever one is (a real H3
    turbo-LoRA OOM: ~1.4GB of bf16 deltas, invisible to the prior budget).

    Returns :data:`_NO_LORA` when there is nothing to walk -- ``dit.module``
    unset, or a bare callable test double rather than an ``nn.Module``.
    """
    module = getattr(dit, "module", None)
    walk = getattr(module, "modules", None)
    if not callable(walk):
        return _NO_LORA
    active = False
    delta_bytes = 0
    output_buffer_out_features = 0
    weight_side_bytes = 0
    for m in walk():
        deltas = getattr(m, "lora_deltas", None)
        if not deltas:
            continue
        active = True
        for delta in deltas:
            for tensor in (getattr(delta, "down", None), getattr(delta, "up", None)):
                if isinstance(tensor, torch.Tensor):
                    delta_bytes += tensor.numel() * tensor.element_size()
        out_features = int(getattr(m, "out_features", 0) or 0)
        in_features = int(getattr(m, "in_features", 0) or 0)
        if not out_features:
            continue
        output_side, weight_side = partition_output_branch_deltas(deltas, out_features)
        if output_side and _linear_takes_gemm_fast_path(m):
            output_buffer_out_features = max(output_buffer_out_features, out_features)
        if weight_side and in_features:
            weight_side_bytes = max(
                weight_side_bytes,
                _LORA_WEIGHT_SIDE_ALLOCATIONS * out_features * in_features * _COMPUTE_DTYPE_BYTES,
            )
    return DitLoraProfile(output_buffer_out_features, weight_side_bytes, delta_bytes, active)


def _dit_lora_delta_gb(dit: Any) -> float:
    """Resident VRAM (GB) of ``dit``'s runtime LoRA deltas -- the ``up``/
    ``down`` pairs themselves, which ``dit.estimated_vram_gb`` cannot see.
    The number a caller sizing its own weight budget has to add on top of
    ``estimated_vram_gb`` (``txt2vid_wan22``'s per-expert placement does);
    :func:`place_dit_for_sequence` reads it off the profile directly.
    """
    return _dit_lora_profile(dit).delta_gb


def _dit_is_fully_resident(dit: Any, device: str) -> bool:
    """True iff ``dit`` is ALREADY placed with FULL residency on ``device`` --
    typically a warm-start restore from a prior generation
    (``dit_restore.restore_dit_best_effort``), not this call's own doing.

    Partial residency (an active streamer) is deliberately excluded: that
    state still needs the normal recompute path below, not the fast no-op
    skip this gates -- the streamer's own resident/streamed leaf split was
    sized for a DIFFERENT sequence length and reserve, so it cannot be
    trusted to still be correct for THIS call without recomputing.
    """
    current = str(getattr(dit, "device", "") or "")
    if not current:
        return False
    try:
        same_type = torch.device(current).type == torch.device(device).type
    except (RuntimeError, ValueError):
        return False
    streamer = getattr(dit, "_streamer", None)
    streaming = bool(streamer is not None and getattr(streamer, "active", False))
    return same_type and not streaming


@dataclass(frozen=True)
class DitPlacementDecision:
    """What :func:`place_dit_for_sequence` decided, for logging + assertions."""

    mode: Literal["resident", "partial", "cpu"]
    dit_weight_gb: float
    activation_reserve_gb: float
    weight_budget_gb: float
    video_tokens: int
    audio_tokens: int
    extra_reserve_gb: float = 0.0
    lora_active: bool = False
    lora_weight_gb: float = 0.0
    # True when the fast warm-residency path fired: `dit` was ALREADY fully
    # resident on the target device and still fits, so NEITHER `move_to` NOR
    # `stream_to` was called at all -- the whole point of the fast path (see
    # `place_dit_for_sequence`'s "Warm residency" docstring section).
    kept_resident: bool = False


def place_dit_for_sequence(
    dit: Any,
    device: str,
    *,
    video_tokens: int,
    audio_tokens: int = 0,
    own_models: Iterable[Any] = (),
    reserve_gb: float = 0.0,
    inner_dim: int = _LTX_INNER_DIM,
    ffn_dim: int | None = None,
    fit_hint: "FitHint | None" = None,
) -> DitPlacementDecision:
    """Place ``dit`` on ``device`` for sampling, sized to THIS generation's
    token count instead of an unconditional full-pin ``move_to``.

    ``own_models`` lists this generation's own bundle components (dit, vae,
    audio_vae, vocoder, ...) so a foreign-resident eviction never reclaims
    them -- mirrors ``NativeGenerator._own_models()``'s exclusion contract.
    Materialised to a tuple up front: it is read by more than one eviction
    call below, so a one-shot generator passed in would silently exclude
    nothing on the second and later reads.

    ``reserve_gb`` (see the module docstring) adds
    extra headroom on top of the token-derived activation reserve, for a
    caller whose GPU work AFTER this placement isn't proportional to
    ``video_tokens`` (e.g. a VAE decode sized by pixel dimensions, not
    tokens). Defaults to 0.0 -- callers that don't pass it keep the exact
    prior behavior.

    ``inner_dim``/``ffn_dim`` (see :func:`estimate_activation_reserve_gb`)
    default to LTX's own attention inner dimension / a no-op, so every LTX
    call site is byte-identical; a non-LTX caller passes its own values.

    The comparison against ``weight_budget`` uses ``dit.estimated_vram_gb``
    PLUS any resident runtime LoRA delta (:func:`_dit_lora_profile`'s
    ``delta_gb``) -- invisible to ``estimated_vram_gb`` (the base
    checkpoint's own file size) but genuinely resident in VRAM once a LoRA
    is applied; see that function's docstring for the OOM this
    under-budgeting caused.

    **Warm residency.** A generation whose DiT was left fully resident by
    the PRIOR generation's warm-start restore (``dit_restore.
    restore_dit_best_effort``) hits ``free_vram_gb(device)`` with that DiT's
    own weight bytes already counted as "used", not "free" -- a naive
    fresh-placement computation then undercounts the true budget by exactly
    ``weight_gb`` and can conclude the (already comfortably resident) DiT no
    longer fits, offloading and re-streaming it from host RAM for every
    sampling step of a run that could have started instantly (observed: a
    ~5s warm generation whose sampling alone took 147s of a 196s total,
    ``weight_budget_gb=0.0`` logged on a card with ~31GB genuinely free).
    :func:`_dit_is_fully_resident` checks for exactly this state and, when
    the DiT's OWN footprint is credited back before judging whether it still
    fits, the fast path below returns WITHOUT calling ``move_to`` or
    ``stream_to`` at all -- the warm path is the fast path. When it does NOT
    fit even with that credit (something else grew since the DiT was placed),
    ``dit.offload()`` runs FIRST so the normal computation below measures
    genuinely free VRAM instead of being polluted by a copy that is about to
    be freed anyway -- a fresh placement decision, not a stale one.
    """
    own_models = tuple(own_models)
    lora = _dit_lora_profile(dit)
    lora_active = lora.active
    lora_weight_gb = lora.delta_gb
    weight_gb = float(getattr(dit, "estimated_vram_gb", None) or 0.0) + lora_weight_gb

    if not str(device).startswith("cuda"):
        dit.move_to(device)
        decision = DitPlacementDecision(
            "cpu", weight_gb, 0.0, weight_gb, video_tokens, audio_tokens, 0.0, lora_active, lora_weight_gb,
        )
        _log_decision(decision, device)
        return decision

    activation_reserve = estimate_activation_reserve_gb(
        video_tokens, audio_tokens, lora=lora, inner_dim=inner_dim, ffn_dim=ffn_dim,
    )
    extra_reserve = max(0.0, float(reserve_gb))
    total_reserve = activation_reserve + extra_reserve

    # Over-commit check, BEFORE any placement I/O: credit back this dit's own
    # currently-resident weight (if any -- mirrors the warm-residency fast
    # path's `free_crediting_self` below) so the comparison is against the
    # largest free VRAM this generation could ever see, i.e. with THIS dit's
    # own weight fully unloaded. If `total_reserve` still doesn't fit that,
    # the estimate says no weight-streaming ladder can rescue this -- but it
    # only WARNS and places anyway. The estimate is a model, wrong in both
    # directions, and refusing here turned away clips that ran fine; the real
    # allocator gets the last word in `guard_sampling_oom` instead. Placement
    # below then lands on a zero (or near-zero) weight budget, i.e. a fully
    # streamed DiT, which is the best shot at running this clip anyway.
    # Every fits-check in this function reads `effective_free_vram_gb`, never
    # `free_vram_gb`: `mem_get_info` counts OUR OWN caching allocator's
    # reserved-but-unallocated pool (the previous phase's activation buffers,
    # which the next cudaMalloc reclaims) as USED, so judging fit on the raw
    # number under-reports the budget. The raw number is still read, to
    # report both in the over-commit warning.
    free_raw = free_vram_gb(device) or 0.0
    free_if_dit_unloaded = effective_free_vram_gb(device) or 0.0
    if _dit_is_fully_resident(dit, device):
        free_raw += weight_gb
        free_if_dit_unloaded += weight_gb
    if total_reserve > free_if_dit_unloaded:
        _log_overcommit(
            total_reserve, activation_reserve, extra_reserve, free_if_dit_unloaded, free_raw,
            video_tokens, audio_tokens,
            _render_fit_hint(
                fit_hint, free_if_dit_unloaded, activation_reserve_gb=activation_reserve,
                extra_reserve_gb=extra_reserve, tokens=video_tokens + audio_tokens,
            ),
        )

    if _dit_is_fully_resident(dit, device):
        free_crediting_self = (effective_free_vram_gb(device) or 0.0) + weight_gb
        credited_budget = max(0.0, free_crediting_self - total_reserve)
        if weight_gb <= 0.0 or weight_gb <= credited_budget:
            maybe_compile_dit(dit, resident=True, is_cuda=True)
            decision = DitPlacementDecision(
                "resident", weight_gb, activation_reserve, credited_budget, video_tokens, audio_tokens,
                extra_reserve, lora_active, lora_weight_gb, kept_resident=True,
            )
            _log_decision(decision, device)
            return decision
        # Doesn't fit even crediting its own footprint back (something else
        # grew since this DiT was placed) -- offload the stale copy FIRST so
        # the normal computation below isn't polluted by weight bytes that
        # are about to be freed anyway, then fall through to a placement
        # decision byte-identical to the cold-start path.
        dit.offload()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    need_gb = weight_gb + minimum_inference_memory_gb() if weight_gb > 0.0 else 0.0
    _ensure_room_for(device, need_gb, own_models)
    free = effective_free_vram_gb(device) or 0.0
    weight_budget = max(0.0, free - total_reserve)

    if weight_gb <= 0.0 or weight_gb <= weight_budget:
        mode = _move_resident(dit, device, own_models)
    else:
        mode = _move_partial(dit, device, weight_budget, own_models)

    if mode == "resident":
        # Regional torch.compile only ever engages on a FULLY resident DiT
        # (compile.py's own gate re-checks quantization/runtime-LoRA/env); a
        # "partial" mode here means an OOM-degrade already ruled it out.
        maybe_compile_dit(dit, resident=True, is_cuda=True)

    decision = DitPlacementDecision(
        mode, weight_gb, activation_reserve, weight_budget, video_tokens, audio_tokens,
        extra_reserve, lora_active, lora_weight_gb,
    )
    _log_decision(decision, device)
    return decision


def guard_sampling_oom(
    forward: Callable[..., Any], *, dit: Any, device: str, decision: DitPlacementDecision,
    fit_hint: "FitHint | None" = None,
) -> Callable[..., Any]:
    """Wrap ``forward`` so its FIRST call degrades instead of dying.

    This is the other half of not refusing up front. The pre-flight estimate
    sizes the weight budget and warns; the real allocator decides. When the
    first sampling forward OOMs, the ladder reclaims the caching allocator's
    idle pool, then sheds every resident DiT weight to host RAM, retrying
    after each. Only when it OOMs with nothing left to shed does the user see
    :class:`SamplingOutOfMemory`, carrying numbers measured at that moment.

    Only the first call is guarded: once a forward has succeeded, this
    generation's peak allocation has been paid and every later step of the
    same sampling loop allocates the same shapes. The wrapper drops to a
    plain passthrough from then on, so it costs a bool per step.

    Retrying the first forward is safe against the step cache
    (``sampling/step_cache.py``): ``record_compute`` runs only after the
    output exists, so a forward that died mid-way recorded nothing, and
    ``should_skip`` refuses during warmup regardless. A future cache that
    commits state BEFORE its forward completes would break that and would
    need a reset hook here.

    ``fit_hint`` is the family's :data:`FitHint`, here rendered against the
    free VRAM MEASURED at failure rather than the pre-flight reading the
    over-commit warning used. Omitted, the message ends after the advice.
    """
    return _GuardedForward(forward, dit=dit, device=device, decision=decision, fit_hint=fit_hint)


class _GuardedForward:
    """Transparent proxy around a sampling forward.

    A proxy rather than a plain closure because these forwards are objects
    with state their pipe still reads THROUGH the value handed to the
    sampler -- ``ConditionedAVForward``'s ``t_lat`` and ``unpack_base`` are
    read off the same reference ``denoise_prenoised`` was given. Anything
    but ``__call__`` falls through to the wrapped object, so wrapping is
    invisible to every caller and every spy.
    """

    def __init__(
        self, forward: Callable[..., Any], *, dit: Any, device: str,
        decision: DitPlacementDecision, fit_hint: "FitHint | None",
    ) -> None:
        # Bypass __setattr__/__getattr__ ambiguity by keeping our own state
        # under names the wrapped object will never be asked for.
        object.__setattr__(self, "_guard_forward", forward)
        object.__setattr__(self, "_guard_dit", dit)
        object.__setattr__(self, "_guard_device", device)
        object.__setattr__(self, "_guard_decision", decision)
        object.__setattr__(self, "_guard_fit_hint", fit_hint)
        object.__setattr__(self, "_guard_armed", True)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        forward = object.__getattribute__(self, "_guard_forward")
        if not object.__getattribute__(self, "_guard_armed"):
            return forward(*args, **kwargs)
        result = _forward_with_degrade(
            forward, args, kwargs,
            dit=object.__getattribute__(self, "_guard_dit"),
            device=object.__getattribute__(self, "_guard_device"),
            decision=object.__getattribute__(self, "_guard_decision"),
            fit_hint=object.__getattribute__(self, "_guard_fit_hint"),
        )
        object.__setattr__(self, "_guard_armed", False)
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_guard_forward"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(object.__getattribute__(self, "_guard_forward"), name, value)


def _forward_with_degrade(
    forward: Callable[..., Any], args: tuple, kwargs: dict, *, dit: Any, device: str,
    decision: DitPlacementDecision, fit_hint: "FitHint | None",
) -> Any:
    try:
        return forward(*args, **kwargs)
    except torch.cuda.OutOfMemoryError:
        logger.warning(
            "[LTX PLACEMENT] first sampling forward OOM'd (S=%d video +%d audio, reserve %.2fGB); "
            "reclaiming the allocator pool and retrying",
            decision.video_tokens, decision.audio_tokens, decision.activation_reserve_gb,
        )

    # Tier 1: the allocator's reserved-but-unallocated pool is memory the next
    # cudaMalloc can already have; releasing it back to the driver costs a
    # sync and is often the whole shortfall.
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    try:
        return forward(*args, **kwargs)
    except torch.cuda.OutOfMemoryError:
        pass

    # Tier 2: hand the forward the whole card by streaming every DiT weight
    # from pinned host RAM. Skipped when nothing is resident to shed -- a
    # placement that already streamed everything has no tier 2.
    if _dit_holds_resident_weight(decision):
        logger.warning(
            "[LTX PLACEMENT] retry after empty_cache still OOM'd; streaming the full DiT "
            "(%.2fGB) from host RAM and retrying", decision.dit_weight_gb,
        )
        dit.offload()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        dit.stream_to(device, 0.0)
        try:
            return forward(*args, **kwargs)
        except torch.cuda.OutOfMemoryError:
            pass

    raise _sampling_oom(device=device, decision=decision, fit_hint=fit_hint)


def _dit_holds_resident_weight(decision: DitPlacementDecision) -> bool:
    """Whether the ladder has any DiT weight left to shed. A "partial"
    placement with a zero weight budget is already fully streamed."""
    if decision.mode == "resident":
        return True
    return decision.mode == "partial" and decision.weight_budget_gb > 0.0


def _sampling_oom(
    *, device: str, decision: DitPlacementDecision, fit_hint: "FitHint | None",
) -> SamplingOutOfMemory:
    free_raw = free_vram_gb(device) or 0.0
    free_effective = effective_free_vram_gb(device) or 0.0
    tokens = decision.video_tokens + decision.audio_tokens
    hint_text = _render_fit_hint(
        fit_hint, free_effective, activation_reserve_gb=decision.activation_reserve_gb,
        extra_reserve_gb=decision.extra_reserve_gb, tokens=tokens,
    )
    get_profiler().mark(
        "ltx.dit_placement.sampling_oom",
        free_raw_gb=round(free_raw, 2), free_effective_gb=round(free_effective, 2),
        activation_reserve_gb=round(decision.activation_reserve_gb, 2),
        extra_reserve_gb=round(decision.extra_reserve_gb, 2),
        video_tokens=decision.video_tokens, audio_tokens=decision.audio_tokens,
    )
    logger.warning(
        "[LTX PLACEMENT] sampling OOM with the DiT fully streamed: S=%d video (+%d audio), "
        "%.2fGB free (%.2fGB raw), estimate had %.2fGB activation + %.2fGB extra",
        decision.video_tokens, decision.audio_tokens, free_effective, free_raw,
        decision.activation_reserve_gb, decision.extra_reserve_gb,
    )
    return SamplingOutOfMemory(
        f"This clip ran out of VRAM at {tokens:,} tokens even with the model streamed from RAM "
        f"({free_effective:.1f} GB free at the time) -- shorten the clip or lower the "
        f"resolution.{' ' + hint_text if hint_text else ''}",
        detail=(
            f"free_raw_gb={free_raw:.2f} free_effective_gb={free_effective:.2f} "
            f"activation_reserve_gb={decision.activation_reserve_gb:.2f} "
            f"extra_reserve_gb={decision.extra_reserve_gb:.2f} "
            f"dit_weight_gb={decision.dit_weight_gb:.2f} mode={decision.mode} "
            f"video_tokens={decision.video_tokens} audio_tokens={decision.audio_tokens}"
        ),
        free_raw_gb=free_raw, free_effective_gb=free_effective,
        activation_reserve_gb=decision.activation_reserve_gb,
        extra_reserve_gb=decision.extra_reserve_gb,
        video_tokens=decision.video_tokens, audio_tokens=decision.audio_tokens,
    )


def _ensure_room_for(device: str, need_gb: float, own_models: Iterable[Any]) -> None:
    """Evict FOREIGN GPU-resident components (never ``own_models``) to make
    ``need_gb`` free on ``device``. Mirrors ``NativeGenerator._ensure_room_for``
    exactly (see engine.py) -- duplicated here rather than reused because that
    is a private method bound to a ``NativeGenerator`` instance these pipes
    don't have."""
    free = free_vram_gb(device)
    if free is None:
        return
    manager = get_residency_registry()
    if need_gb and need_gb > 0.0:
        offloaded = manager.ensure_free(device, need_gb, free, exclude=own_models)
    else:
        offloaded = manager.offload_all(device, exclude=own_models)
    if offloaded and torch.cuda.is_available():
        torch.cuda.empty_cache()


def _move_resident(dit: Any, device: str, own_models: Iterable[Any]) -> str:
    """Full-pin ``dit`` on ``device``, degrading to partial residency on a
    persisting OOM. Mirrors ``NativeGenerator._move_dit_to_gpu``'s 3-tier
    ladder: try, evict-foreign-and-retry, degrade-to-partial-against-live-free."""
    try:
        dit.move_to(device)
        return "resident"
    except torch.cuda.OutOfMemoryError:
        logger.warning("[LTX PLACEMENT] DiT move to %s OOM'd; evicting foreign residents and retrying", device)
        get_residency_registry().offload_all(device, exclude=own_models)
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        try:
            dit.move_to(device)
            return "resident"
        except torch.cuda.OutOfMemoryError:
            free = free_vram_gb(device) or 0.0
            budget = max(0.0, free - minimum_inference_memory_gb())
            logger.warning(
                "[LTX PLACEMENT] DiT full move still OOM (co-tenant?); degrading to partial "
                "residency with %.1fGB weights budget", budget,
            )
            dit.offload()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            dit.stream_to(device, budget)
            return "partial"


def _move_partial(dit: Any, device: str, weight_budget_gb: float, own_models: Iterable[Any]) -> str:
    """Partial-residency placement, degrading to a fully-streamed DiT on a
    persisting OOM. Mirrors ``NativeGenerator._stream_dit_to_gpu``."""
    try:
        dit.stream_to(device, weight_budget_gb)
        return "partial"
    except torch.cuda.OutOfMemoryError:
        logger.warning(
            "[LTX PLACEMENT] partial-residency DiT placement OOM'd (budget %.1fGB); "
            "evicting foreign residents and streaming fully", weight_budget_gb,
        )
        dit.offload()
        get_residency_registry().offload_all(device, exclude=own_models)
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        dit.stream_to(device, 0.0)
        return "partial"


def _log_overcommit(
    total_reserve_gb: float, activation_reserve_gb: float, extra_reserve_gb: float,
    free_gb: float, free_raw_gb: float, video_tokens: int, audio_tokens: int,
    hint_text: str = "",
) -> None:
    """Warn that the estimate does not fit this card, and proceed anyway --
    see the over-commit check in :func:`place_dit_for_sequence`."""
    get_profiler().mark(
        "ltx.dit_placement.overcommit",
        total_reserve_gb=round(total_reserve_gb, 2), activation_reserve_gb=round(activation_reserve_gb, 2),
        extra_reserve_gb=round(extra_reserve_gb, 2), free_gb=round(free_gb, 2),
        free_raw_gb=round(free_raw_gb, 2),
        video_tokens=video_tokens, audio_tokens=audio_tokens,
    )
    logger.warning(
        "[LTX PLACEMENT] over-committed: total reserve %.2fGB (activation %.2fGB + extra %.2fGB) "
        "exceeds %.2fGB free with zero DiT weight resident (%.2fGB raw, the rest held by the "
        "caching allocator), S=%d video (+%d audio) -- streaming the DiT and sampling anyway.%s",
        total_reserve_gb, activation_reserve_gb, extra_reserve_gb, free_gb, free_raw_gb,
        video_tokens, audio_tokens, f" {hint_text}" if hint_text else "",
    )


def _log_decision(decision: DitPlacementDecision, device: str) -> None:
    get_profiler().mark(
        "ltx.dit_placement", mode=decision.mode, device=str(device),
        dit_weight_gb=round(decision.dit_weight_gb, 2),
        activation_reserve_gb=round(decision.activation_reserve_gb, 2),
        extra_reserve_gb=round(decision.extra_reserve_gb, 2),
        weight_budget_gb=round(decision.weight_budget_gb, 2),
        video_tokens=decision.video_tokens, audio_tokens=decision.audio_tokens,
        lora_active=decision.lora_active, lora_weight_gb=round(decision.lora_weight_gb, 2),
        kept_resident=decision.kept_resident,
    )
    logger.debug(
        "[LTX PLACEMENT] %s%s: DiT %.2fGB (incl. %.2fGB LoRA), S=%d video (+%d audio), activation reserve "
        "%.2fGB (+%.2fGB extra), weight budget %.2fGB, lora_active=%s",
        decision.mode, " (kept, no move)" if decision.kept_resident else "",
        decision.dit_weight_gb, decision.lora_weight_gb, decision.video_tokens,
        decision.audio_tokens, decision.activation_reserve_gb, decision.extra_reserve_gb, decision.weight_budget_gb,
        decision.lora_active,
    )
