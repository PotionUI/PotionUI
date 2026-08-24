"""Regional ``torch.compile`` for native DiTs — opt-in, resident-only, reversible.

Naive ``torch.compile(model)`` pays a multi-minute full-graph warm-up that is
miserable in an interactive app. Regional compilation compiles the *repeated
transformer block* instead: one shared compiled artifact reused for every block,
so warm-up is a single block's graph and the outer control flow (FBCache's
first-block skip, guidance branches) stays in eager Python and never graph-breaks
the whole forward.

Hard constraints for THIS engine, all enforced by :func:`maybe_compile_dit`:

  * **Resident only.** The partial-residency streamer swaps a leaf's ``weight.data``
    to a freshly staged GPU tensor per forward (see ``memory/partial.py``); a
    compiled graph guards on weight identity/device and would recompile or break
    every step. So compile only when the DiT is *fully* GPU-resident.
  * **No runtime LoRA deltas.** In-place-patched LoRA is plain-tensor and
    compile-friendly, but the cast-mode ``lora_deltas`` path rebuilds the weight
    per forward — graph-breaks. Skip if any leaf carries a non-empty ``lora_deltas``.
  * **No quantized Linears (v1).** ``Fp8ScaledLinear`` / ``Nvfp4Linear`` dequantise
    on forward; that belongs to the fp8-matmul lane (§3.3), not here. Skip if the
    DiT is quantised.

The compile is **reversible**: :func:`maybe_compile_dit` records a
:class:`CompileHandle` on the ``NativeModel`` and ``restore()`` puts the original
block modules back into their ``ModuleList``s. The engine calls ``restore()``
whenever the DiT leaves the GPU (offload / unload / move-to-CPU), so the
RAM-cached copy the model lifecycle keeps is always a plain, un-compiled module —
no dynamo graph or CUDA guard state survives an offload/reload cycle.

Toggle: the admin-set ``native_torch_compile`` setting (held in memory via
:func:`set_torch_compile_override`, mirroring the attention-backend pin) wins;
with no explicit setting the ``$NATIVE_TORCH_COMPILE`` env var decides
(``off`` default | ``on`` | ``auto``==on, same shape as ``$NATIVE_FP8_MATMUL``).
There is no catalog entry: the optimizations catalog models *installable
attention backends* (its ``active`` state is ``probe.active_backend``), and
``torch.compile`` is neither installable nor an attention backend.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

import torch
import torch.nn as nn

from vendor.gpl.comfyui.ops import CastWeightBiasOp, Fp8ScaledLinear

logger = logging.getLogger(__name__)

# Neither cause below changes mid-process (a bad env value stays bad; a
# persistently failing compile keeps failing the same way), so each is warned
# about once instead of on every model residency placement / compile attempt.
_warned_bad_compile_env = False
_warned_compile_failed = False

NATIVE_TORCH_COMPILE_ENV = "NATIVE_TORCH_COMPILE"

# In-memory admin override (Admin -> Backends -> Optimizations). Seeded from the
# `native_torch_compile` setting at app startup and updated live by the
# engine-flags endpoint; never read from the DB inside the hot path.
_compile_override: bool | None = None


def set_torch_compile_override(policy: str | None) -> None:
    """Force torch.compile on/off from outside a single call.

    ``"on"`` forces on, ``"off"`` forces off; ``None``/``""``/``"auto"`` clear
    the override so ``$NATIVE_TORCH_COMPILE`` decides again.
    """
    global _compile_override
    if policy is None:
        _compile_override = None
        return
    normalized = policy.strip().lower()
    if normalized == "on":
        _compile_override = True
    elif normalized == "off":
        _compile_override = False
    else:
        _compile_override = None


def get_torch_compile_override() -> bool | None:
    return _compile_override


def torch_compile_enabled() -> bool:
    """Whether regional ``torch.compile`` is enabled.

    The admin override (seeded from the ``native_torch_compile`` setting) wins;
    otherwise ``$NATIVE_TORCH_COMPILE`` decides. ``auto`` behaves like ``on``
    (no extra heuristic yet); an unknown value is treated as ``off``, mirroring
    ``vendor.gpl.comfyui.ops._fp8_matmul_enabled``.
    """
    if _compile_override is not None:
        return _compile_override
    policy = os.environ.get(NATIVE_TORCH_COMPILE_ENV, "off").strip().lower()
    if policy == "off":
        return False
    if policy not in ("on", "auto"):
        global _warned_bad_compile_env
        if not _warned_bad_compile_env:
            _warned_bad_compile_env = True
            logger.warning("torch.compile: unknown %s=%r; treating as 'off'", NATIVE_TORCH_COMPILE_ENV, policy)
        return False
    return True


def is_compiled(module: nn.Module) -> bool:
    """True if ``module`` is a ``torch.compile`` wrapper (``OptimizedModule``)."""
    return hasattr(module, "_orig_mod")


def find_block_lists(module: nn.Module) -> list[nn.ModuleList]:
    """The repeated-transformer-block ``ModuleList``s of a DiT, discovered generically.

    A direct child that is an ``nn.ModuleList`` of ≥2 elements all of the SAME
    class is a regional-compile target (Flux's ``double_blocks`` + ``single_blocks``;
    a ``blocks`` list on other families). Deliberately direct-children-only and
    homogeneous — it never descends into a block's own internals — so it targets
    the coarse block loop and nothing finer. Families that nest their block list
    deeper simply yield nothing here and are log-skipped by the caller.
    """
    lists: list[nn.ModuleList] = []
    for _name, child in module.named_children():
        if not isinstance(child, nn.ModuleList) or len(child) < 2:
            continue
        classes = {type(m) for m in child}
        if len(classes) == 1 and any(p is not None for p in child[0].parameters()):
            lists.append(child)
    return lists


@dataclass
class CompileHandle:
    """Undo record for a regional compile: restore the original blocks in place."""

    entries: list[tuple[nn.ModuleList, int, nn.Module]] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return bool(self.entries)

    def restore(self) -> None:
        """Put every original (un-compiled) block back into its ``ModuleList``."""
        for module_list, index, original in self.entries:
            module_list[index] = original
        self.entries = []


def _quantized(module: nn.Module) -> bool:
    return any(isinstance(m, Fp8ScaledLinear) for m in module.modules())


def _has_runtime_lora(module: nn.Module) -> bool:
    return any(
        isinstance(m, CastWeightBiasOp) and getattr(m, "lora_deltas", None)
        for m in module.modules()
    )


def compile_gate(native_model, *, resident: bool, is_cuda: bool) -> tuple[bool, str]:
    """Pure gate: may this ``NativeModel``'s DiT be regionally compiled? ``(ok, reason)``.

    Every condition must hold; ``reason`` names the first that fails (for logging /
    tests). Kept free of side effects so it is unit-testable with mock modules.
    """
    if not torch_compile_enabled():
        return False, "disabled"
    if not hasattr(torch, "compile"):
        return False, "torch-no-compile"
    if not is_cuda:
        return False, "cpu"
    if not resident:
        return False, "streaming"
    if getattr(native_model, "quant_format", None) is not None:
        return False, "quantized"
    module = native_model.module
    if module is None:
        return False, "no-module"
    if _quantized(module):
        return False, "quantized-linear"
    if _has_runtime_lora(module):
        return False, "runtime-lora"
    if not find_block_lists(module):
        return False, "no-block-lists"
    return True, "ok"


def compile_blocks(module: nn.Module) -> CompileHandle:
    """Regionally compile every discovered block ``ModuleList`` IN PLACE.

    Idempotent: an already-compiled block is left as-is (not re-wrapped). Returns a
    :class:`CompileHandle` recording only the blocks this call wrapped, so
    ``restore()`` is exact. ``mode="default"``, ``dynamic=True`` — resolution
    changes re-guard rather than hard-recompiling; ``mark_dynamic`` is intentionally
    not used in v1 (add only if warm benchmarking shows real recompiles).
    """
    handle = CompileHandle()
    total = 0
    try:
        for module_list in find_block_lists(module):
            for index in range(len(module_list)):
                block = module_list[index]
                if is_compiled(block):
                    continue
                module_list[index] = torch.compile(block, mode="default", dynamic=True)
                handle.entries.append((module_list, index, block))
                total += 1
    except Exception:
        # A failure partway through must not leave untracked compiled wrappers
        # installed (offload/unload could then never restore them): undo the wraps
        # done so far and re-raise for maybe_compile_dit to log + run fully eager.
        handle.restore()
        raise
    if total:
        logger.info("torch.compile: regionally compiled %d transformer block(s)", total)
    return handle


def maybe_compile_dit(native_model, *, resident: bool, is_cuda: bool) -> None:
    """Compile the DiT's blocks in place if the gate passes; record the undo handle.

    No-op (and leaves the model untouched) when any gate fails or when already
    compiled. Any failure inside ``torch.compile`` degrades to eager: the partial
    handle is restored and nothing is left half-wrapped.
    """
    existing = getattr(native_model, "_compiled", None)
    if existing is not None and existing.active:
        return  # already compiled this resident placement — idempotent
    ok, reason = compile_gate(native_model, resident=resident, is_cuda=is_cuda)
    if not ok:
        if reason not in ("disabled", "cpu", "streaming"):
            logger.debug("torch.compile: skipped (%s)", reason)
        return
    # torch.compile wraps lazily: Dynamo/Inductor actually compile on the FIRST
    # forward, outside this try. suppress_errors makes that lazy compilation fall
    # back to eager (with a log) instead of raising mid-denoise, so an unsupported
    # op or an Inductor OOM degrades gracefully rather than aborting a generation.
    try:
        import torch._dynamo

        torch._dynamo.config.suppress_errors = True
    except Exception:  # pragma: no cover - dynamo config surface is torch-version dependent
        logger.debug("torch.compile: could not set dynamo suppress_errors", exc_info=True)
    try:
        handle = compile_blocks(native_model.module)
    except Exception:  # noqa: BLE001 - compile is an optimisation; never fatal
        global _warned_compile_failed
        if not _warned_compile_failed:
            _warned_compile_failed = True
            logger.warning("torch.compile: compilation failed; running eager", exc_info=True)
        existing = getattr(native_model, "_compiled", None)
        if existing is not None:
            existing.restore()
        native_model._compiled = None
        return
    native_model._compiled = handle


def restore_compiled(native_model) -> None:
    """Undo any regional compile on ``native_model`` (called when it leaves the GPU)."""
    handle = getattr(native_model, "_compiled", None)
    if handle is not None and handle.active:
        handle.restore()
    native_model._compiled = None


# -- MiniMax-Music3 AR core: whole-module reduce-overhead compile -------------
#
# The block-list machinery above regionally compiles a repeated block's own
# internals, reused at ONE shape (a DiT's block forward is called at the same
# resolution/sequence length every step of a generation). The AR core's own
# hot step doesn't fit that shape: the depth decoder's WHOLE ``forward`` (pos
# embedding, causal mask build, 4 blocks, final norm — arch/minimax_music3/
# depth_decoder.py's ``DepthDecoderModule.forward``) is called 7 times per
# frame with ``token_embeds.shape[1]`` growing 2 -> 8, and Python-level launch
# overhead across THAT whole call (not just one block) is the measured cost
# (see this module's own callers for the profiled numbers). So the target
# here is compiling the WHOLE module with ``mode="reduce-overhead"`` (CUDA
# graphs), swapped onto its owner's attribute in place — the same undo-by-
# reassignment idea as :class:`CompileHandle`, generalised to an attribute
# name instead of a ``ModuleList`` index (:class:`AttrCompileHandle`).
#
# ``dynamic=False``: this call site only ever presents 7 distinct sequence
# lengths (2..8), and they recur IDENTICALLY every frame. ``dynamic=True``/
# ``mark_dynamic`` exists to avoid a recompile blow-up over an UNBOUNDED shape
# range — irrelevant to a fixed set of 7. Letting Dynamo specialize per shape
# gives each length its own captured CUDA graph after frame 0's warm-up, and
# every later frame is 7 graph replays — exactly what "reduce-overhead" is
# for; ``dynamic=True`` would instead trace ``T`` symbolically, defeating full
# CUDA-graph capture for no benefit at this cardinality. The dynamo
# per-function recompile-cache limit is raised so this call site's own
# 7-shape steady state can never trip the default ceiling and silently fall
# back to eager mid-song.
#
# The global LM's own per-position decode step (``MiniMaxMusic3AudioLM.step``,
# see ``arch/minimax_music3/lm.py``) is deliberately NOT compiled here: its
# cache-write slice (``cache_k[:, :, pos:pos + 1, :] = ...``) and its RoPE
# table lookup both key off ``pos``, a plain python int that advances by ONE
# on every frame up to ``max_frames`` (up to 9000, ``prompt.MAX_AUDIO_FRAMES``)
# — passed as a python scalar, not a tensor. Under Dynamo that either becomes
# a per-value recompile trigger (thousands of specializations over one song,
# the opposite of "reduce-overhead"), or needs ``pos`` re-expressed as a
# 0-dim tensor before compiling — a real change to ``lm.py``'s cache contract,
# not something safe to bolt on from this module. Left eager.

_MUSIC3_DEPTH_DECODER_SHAPE_COUNT = 7  # seq_len 2..8 -- NUM_RESIDUAL_CODEBOOKS in arch/minimax_music3/depth_decoder.py.


@dataclass
class AttrCompileHandle:
    """Undo record for a whole-module compile swapped onto an owner's
    attribute (rather than a ``ModuleList`` slot — see :class:`CompileHandle`).
    Duck-types the same ``active``/``restore()`` interface, so
    :func:`restore_compiled` (and every ``NativeModel`` call site that already
    calls it on offload/stream_to/unload) restores either kind unmodified.
    """

    entries: list[tuple[Any, str, Any]] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return bool(self.entries)

    def restore(self) -> None:
        for owner, name, original in self.entries:
            setattr(owner, name, original)
        self.entries = []


def music3_ar_compile_gate(native_lm, *, resident: bool, is_cuda: bool) -> tuple[bool, str]:
    """Pure gate for the MiniMax-Music3 AR core's depth-decoder compile —
    same env/device/residency/quantization/LoRA conditions as
    :func:`compile_gate`, minus the DiT-only block-list requirement (the
    depth decoder is compiled as ONE whole module, not per-block).
    """
    if not torch_compile_enabled():
        return False, "disabled"
    if not hasattr(torch, "compile"):
        return False, "torch-no-compile"
    if not is_cuda:
        return False, "cpu"
    if not resident:
        return False, "streaming"
    if getattr(native_lm, "quant_format", None) is not None:
        return False, "quantized"
    module = getattr(native_lm, "module", None)
    decoder = getattr(getattr(module, "model", None), "audio_decoder", None)
    if decoder is None:
        return False, "no-audio-decoder"
    if _quantized(decoder):
        return False, "quantized-linear"
    if _has_runtime_lora(decoder):
        return False, "runtime-lora"
    return True, "ok"


def compile_music3_depth_decoder(module) -> AttrCompileHandle:
    """Whole-module ``reduce-overhead`` compile of ``module.model.audio_decoder``
    in place (``module``: a MiniMax-Music3 ``MiniMaxMusic3AudioLM``). Idempotent
    — an already-compiled decoder is left as-is. See this section's module-level
    comment for why a whole-module compile (not :func:`compile_blocks`) is the
    right shape here, and why ``dynamic=False``.
    """
    handle = AttrCompileHandle()
    decoder = module.model.audio_decoder
    if is_compiled(decoder):
        return handle
    try:
        import torch._dynamo

        if torch._dynamo.config.cache_size_limit < _MUSIC3_DEPTH_DECODER_SHAPE_COUNT * 2:
            torch._dynamo.config.cache_size_limit = _MUSIC3_DEPTH_DECODER_SHAPE_COUNT * 2
    except Exception:  # pragma: no cover - dynamo config surface is torch-version dependent
        logger.debug("torch.compile: could not raise dynamo cache_size_limit for the depth decoder", exc_info=True)
    compiled = torch.compile(decoder, mode="reduce-overhead", dynamic=False)
    module.model.audio_decoder = compiled
    handle.entries.append((module.model, "audio_decoder", decoder))
    return handle


def maybe_compile_music3_ar(native_lm, *, resident: bool, is_cuda: bool) -> None:
    """Compile the depth decoder in place if the gate passes.

    The undo handle is stored on ``native_lm._compiled`` — the SAME field
    :func:`maybe_compile_dit`/:func:`restore_compiled` use, so ``NativeModel``'s
    own ``move_to("cpu")``/``stream_to``/``unload`` (``engine.py``) already
    restore it on every path the AR core leaves the GPU through; no separate
    undo call site is needed at this function's caller. Mirrors
    :func:`maybe_compile_dit`.
    """
    existing = getattr(native_lm, "_compiled", None)
    if existing is not None and existing.active:
        return  # already compiled this resident placement — idempotent
    ok, reason = music3_ar_compile_gate(native_lm, resident=resident, is_cuda=is_cuda)
    if not ok:
        if reason not in ("disabled", "cpu", "streaming"):
            logger.debug("torch.compile: music3 AR skipped (%s)", reason)
        return
    try:
        import torch._dynamo

        torch._dynamo.config.suppress_errors = True
    except Exception:  # pragma: no cover - dynamo config surface is torch-version dependent
        logger.debug("torch.compile: could not set dynamo suppress_errors", exc_info=True)
    try:
        handle = compile_music3_depth_decoder(native_lm.module)
    except Exception:  # noqa: BLE001 - compile is an optimisation; never fatal
        global _warned_compile_failed
        if not _warned_compile_failed:
            _warned_compile_failed = True
            logger.warning("torch.compile: music3 AR compilation failed; running eager", exc_info=True)
        existing = getattr(native_lm, "_compiled", None)
        if existing is not None:
            existing.restore()
        native_lm._compiled = None
        return
    native_lm._compiled = handle
