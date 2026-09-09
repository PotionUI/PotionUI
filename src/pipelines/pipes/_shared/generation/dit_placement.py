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
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable, Literal

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

logger = logging.getLogger(__name__)


class DitPlacementInfeasible(GenerationExecutionError):
    """Raised when ``total_reserve`` (activation reserve + any caller
    ``reserve_gb``) exceeds free VRAM on its own -- no weight-streaming ladder
    can rescue this: the request needs more activation headroom than the card
    has even with ZERO DiT weight resident. Raised BEFORE any placement I/O
    (``move_to``/``stream_to``/``offload``) so a request that can never fit
    fails immediately instead of after a slow weight stream followed by an
    OOM on the first forward (the maintainer's MiniMax-H3 VDN trace: a
    34.6GB total reserve on a 31.4GB card streamed for 12s before dying on
    the first allocation).

    Derives from :class:`GenerationExecutionError` (not a bare
    ``RuntimeError``) so ``GenerationEngine`` surfaces ``message`` to the user
    verbatim -- ``error_classification.classify_generation_error`` only
    substitutes its own generic summary when an exception carries no
    ``.detail``.
    """

    def __init__(
        self, message: str, *, detail: str, total_reserve_gb: float, activation_reserve_gb: float,
        extra_reserve_gb: float, free_gb: float, video_tokens: int, audio_tokens: int,
    ) -> None:
        super().__init__(message, detail=detail)
        self.total_reserve_gb = total_reserve_gb
        self.activation_reserve_gb = activation_reserve_gb
        self.extra_reserve_gb = extra_reserve_gb
        self.free_gb = free_gb
        self.video_tokens = video_tokens
        self.audio_tokens = audio_tokens

_BYTES_PER_GB = 1024 ** 3

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
# FeedForward width is already folded into the LoRA-output-branch term below
# via `_LTX_FF_MULT`, and every existing LTX call site never passes
# `ffn_dim`, so this is additive-only for families that opt in.
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

# LoRA output-branch term (follow-up to 38b94c75): a resident runtime
# LoRA delta (quantized-storage Linears -- lora/apply.py's
# ``_needs_runtime_deltas``) can route through
# ``vendor/gpl/comfyui/ops.py``'s ``_nvfp4_lora_output_branch``, whose
# per-Linear-call footprint is dominated by its preallocated ``total`` output
# buffer: shape ``(S, out_features)`` at compute dtype -- the ONE allocation
# in that function NOT bounded by its own 32MiB token-chunking (only the
# per-chunk transients are, and those stay in the tens-of-MB regardless of S,
# already covered by ``_ACTIVATION_RESERVE_FLOOR_GB``). ``out_features`` is
# sized off the widest LoRA-eligible Linear in an LTX block -- the
# feed-forward up-projection (``FeedForward``'s ``_GELUApprox.proj``, model.py),
# ``dim * mult`` wide with ``mult=4`` -- since that upper-bounds every other
# Linear's (attention QKV/out, ff down-proj) narrower ``out_features``.
# In-place-baked LoRA (float storage) never sets ``lora_deltas`` at all, so
# gating on its presence correctly contributes zero when no runtime LoRA is
# resident.
_LTX_FF_MULT = 4


def _lora_output_buffer_bytes_per_token(inner_dim: int) -> int:
    return _LTX_FF_MULT * inner_dim * 2


_LORA_OUTPUT_BUFFER_BYTES_PER_TOKEN = _lora_output_buffer_bytes_per_token(_LTX_INNER_DIM)

# Multiplicative safety margin over the raw per-token estimate -- covers
# allocator fragmentation, cuBLAS/cuDNN workspace, and any minor uncounted
# term. Multiplicative (not a flat add) so it scales with S instead of
# vanishing at the high end where it matters most.
_ACTIVATION_SAFETY_MARGIN = 1.15

# Floor for tiny sequences (a handful of low-res frames) -- allocator/cuBLAS
# scratch never shrinks to zero regardless of S.
_ACTIVATION_RESERVE_FLOOR_GB = 0.5


def estimate_activation_reserve_gb(
    video_tokens: int, audio_tokens: int = 0, *, lora_active: bool = False,
    inner_dim: int = _LTX_INNER_DIM, ffn_dim: int | None = None,
) -> float:
    """Estimate the DiT-forward activation VRAM reserve for one sampling step.

    ``video_tokens`` is the base + appended-conditioning video token count
    (``t_lat * h_lat * w_lat`` for ``txt2vid_ltx``; ``prepared.base_tokens +
    prepared.n_extra`` for ``video_ltx``); ``audio_tokens`` is the appended
    audio token count when audio generation is enabled (0 otherwise) -- both
    ride the SAME packed sampler state and so share this one reserve.

    ``lora_active`` adds :data:`_LORA_OUTPUT_BUFFER_BYTES_PER_TOKEN` to the
    per-token rate (see its definition) when the placed DiT has a resident
    runtime LoRA delta; ``False`` (the default) reproduces the exact prior
    formula.

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
    if lora_active:
        bytes_per_token += _lora_output_buffer_bytes_per_token(inner_dim)
    raw_gb = (s * bytes_per_token) / _BYTES_PER_GB
    return max(_ACTIVATION_RESERVE_FLOOR_GB, raw_gb * _ACTIVATION_SAFETY_MARGIN)


def _dit_has_active_lora(dit: Any) -> bool:
    """True iff any ``Linear`` on ``dit.module`` carries a resident runtime
    LoRA delta (``lora_deltas``, set by ``lora/apply.py``'s
    ``_needs_runtime_deltas`` path for quantized-storage weights). In-place-
    baked LoRA (float storage) never sets this attribute, so it correctly
    reports ``False`` for that case -- matching the zero forward-time cost of
    a baked delta.
    """
    module = getattr(dit, "module", None)
    walk = getattr(module, "modules", None)
    if not callable(walk):
        # Not an ``nn.Module`` (e.g. a bare callable test double, or an
        # unset/None ``.module``) -- nothing to walk, so no LoRA to detect.
        return False
    return any(getattr(m, "lora_deltas", None) for m in walk())


def _dit_lora_delta_gb(dit: Any) -> float:
    """Actual resident VRAM (GB) of every ``Linear``'s runtime LoRA delta on
    ``dit.module`` -- the low-rank ``up``/``down`` pairs ``lora/apply.py``'s
    ``_needs_runtime_deltas`` path keeps unbaked (``lora_deltas``, a list of
    ``LoraDelta``) for a quantized-storage weight.

    Follow-up to the same H3 turbo-LoRA OOM :func:`_ffn_transient_bytes_per_
    token` documents: ``dit.estimated_vram_gb`` is the BASE checkpoint's own
    file size and has no way to know a LoRA was ever applied, so a resident-
    vs-partial decision that only looks at ``estimated_vram_gb`` silently
    under-budgets by exactly this many GB whenever ``lora_active`` -- the
    trace's own turbo deltas were ~1.4GB bf16, invisible to the prior budget
    while genuinely resident in VRAM right alongside the fp8 base weights.
    Returns ``0.0`` (a no-op) whenever :func:`_dit_has_active_lora` would too
    (nothing to walk, or no resident delta found).
    """
    module = getattr(dit, "module", None)
    walk = getattr(module, "modules", None)
    if not callable(walk):
        return 0.0
    total_bytes = 0
    for m in walk():
        deltas = getattr(m, "lora_deltas", None)
        if not deltas:
            continue
        for delta in deltas:
            for tensor in (getattr(delta, "down", None), getattr(delta, "up", None)):
                if isinstance(tensor, torch.Tensor):
                    total_bytes += tensor.numel() * tensor.element_size()
    return total_bytes / _BYTES_PER_GB


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
    PLUS any resident runtime LoRA delta (:func:`_dit_lora_delta_gb`) --
    invisible to ``estimated_vram_gb`` (the base checkpoint's own file size)
    but genuinely resident in VRAM once a LoRA is applied; see that
    function's docstring for the OOM this under-budgeting caused.

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
    lora_active = _dit_has_active_lora(dit)
    lora_weight_gb = _dit_lora_delta_gb(dit) if lora_active else 0.0
    weight_gb = float(getattr(dit, "estimated_vram_gb", None) or 0.0) + lora_weight_gb

    if not str(device).startswith("cuda"):
        dit.move_to(device)
        decision = DitPlacementDecision(
            "cpu", weight_gb, 0.0, weight_gb, video_tokens, audio_tokens, 0.0, lora_active, lora_weight_gb,
        )
        _log_decision(decision, device)
        return decision

    activation_reserve = estimate_activation_reserve_gb(
        video_tokens, audio_tokens, lora_active=lora_active, inner_dim=inner_dim, ffn_dim=ffn_dim,
    )
    extra_reserve = max(0.0, float(reserve_gb))
    total_reserve = activation_reserve + extra_reserve

    # Feasibility gate, BEFORE any placement I/O: credit back this dit's own
    # currently-resident weight (if any -- mirrors the warm-residency fast
    # path's `free_crediting_self` below) so the comparison is against the
    # largest free VRAM this generation could ever see, i.e. with THIS dit's
    # own weight fully unloaded. If `total_reserve` still doesn't fit that,
    # no amount of weight-streaming can rescue it -- refuse now rather than
    # discovering it after a slow stream and an OOM on the first forward.
    # Every fits-check in this function reads `effective_free_vram_gb`, never
    # `free_vram_gb`: `mem_get_info` counts OUR OWN caching allocator's
    # reserved-but-unallocated pool (the previous phase's activation buffers,
    # which the next cudaMalloc reclaims) as USED, so judging fit on the raw
    # number refuses clips that fit -- and this gate raises before any
    # placement I/O, so nothing downstream can rescue it. The raw number is
    # still read, to report both on refusal.
    free_raw = free_vram_gb(device) or 0.0
    free_if_dit_unloaded = effective_free_vram_gb(device) or 0.0
    if _dit_is_fully_resident(dit, device):
        free_raw += weight_gb
        free_if_dit_unloaded += weight_gb
    if total_reserve > free_if_dit_unloaded:
        _log_refusal(
            total_reserve, activation_reserve, extra_reserve, free_if_dit_unloaded, free_raw,
            video_tokens, audio_tokens,
        )
        raise DitPlacementInfeasible(
            f"This clip needs about {total_reserve:.1f} GB of VRAM for activations at "
            f"{video_tokens:,} video tokens (+{audio_tokens:,} audio), but only "
            f"{free_if_dit_unloaded:.1f} GB is free -- shorten the clip or lower the resolution.",
            detail=(
                f"activation_reserve_gb={activation_reserve:.2f} extra_reserve_gb={extra_reserve:.2f} "
                f"total_reserve_gb={total_reserve:.2f} free_gb={free_if_dit_unloaded:.2f} "
                f"free_raw_gb={free_raw:.2f} free_effective_gb={free_if_dit_unloaded:.2f} "
                f"video_tokens={video_tokens} audio_tokens={audio_tokens}"
            ),
            total_reserve_gb=total_reserve, activation_reserve_gb=activation_reserve,
            extra_reserve_gb=extra_reserve, free_gb=free_if_dit_unloaded,
            video_tokens=video_tokens, audio_tokens=audio_tokens,
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


def _log_refusal(
    total_reserve_gb: float, activation_reserve_gb: float, extra_reserve_gb: float,
    free_gb: float, free_raw_gb: float, video_tokens: int, audio_tokens: int,
) -> None:
    get_profiler().mark(
        "ltx.dit_placement.refused",
        total_reserve_gb=round(total_reserve_gb, 2), activation_reserve_gb=round(activation_reserve_gb, 2),
        extra_reserve_gb=round(extra_reserve_gb, 2), free_gb=round(free_gb, 2),
        free_raw_gb=round(free_raw_gb, 2),
        video_tokens=video_tokens, audio_tokens=audio_tokens,
    )
    logger.warning(
        "[LTX PLACEMENT] refused: total reserve %.2fGB (activation %.2fGB + extra %.2fGB) exceeds "
        "%.2fGB free with zero DiT weight resident (%.2fGB raw, the rest held by the caching "
        "allocator), S=%d video (+%d audio)",
        total_reserve_gb, activation_reserve_gb, extra_reserve_gb, free_gb, free_raw_gb,
        video_tokens, audio_tokens,
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
