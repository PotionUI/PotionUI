"""Shared model-loader helpers for the native single/multi-DiT loader pipes.

Every native model-loader pipe (Anima, Flux, Krea-2, Qwen, Wan22, Z-Image)
re-implemented the same private helpers for resolving a model-picker file
path, filtering active LoRAs, reading the VRAM budget off the ``GPU`` service,
and applying a LoRA stack to a loaded DiT. This module is the single copy;
callers pass a ``log_tag`` (e.g. ``"MODEL LOADER FLUX"``) to preserve their
existing per-preset log lines.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.platform.runtime.native.engine import NativeModel
from src.platform.runtime.native.io.safetensors_loader import load_torch_file
from src.platform.runtime.native.lora import AdapterApplication, apply_loras_with_report, parse_lora_window
from src.pipelines.contracts import logger
from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import (
    Icon,
    ModelGenerationOutput,
    ModelsGenerationOutput,
    Progress,
    ProgressGenerationOutput,
)

COLD_LOAD_NOTE = (
    ". Loading this model for the first time — this can take a few minutes. "
    "Later runs start in seconds."
)


def path_of(component: Optional[Dict[str, Any]]) -> Optional[str]:
    """Pull a non-empty ``file_path`` out of a model-picker config dict."""
    if not component:
        return None
    path = component.get("file_path")
    if path is None or str(path).strip() == "":
        return None
    return str(path)


class ComponentProgress:
    """Emits one ``ProgressGenerationOutput`` per component of a multi-
    ``MODELS.acquire()`` loader pipe, so the frontend gets a real N-of-``total``
    fraction across the minutes a cold multi-GB load can take instead of a
    single static message for the whole thing.

    ``advance()`` is called once per component, in acquire order, BEFORE that
    component's ``acquire()`` call — so the emitted state describes the
    component about to load, not the one just finished. When ``cache_key``
    has no live entry in ``models`` yet (a cold, from-disk load rather than a
    warm cache reuse), ``COLD_LOAD_NOTE`` is appended so the user isn't left
    staring at a bar that looks stuck.
    """

    def __init__(self, generation_outputs: callable, models: Optional[Any], label: str, total: int) -> None:
        self._generation_outputs = generation_outputs
        self._models = models
        self._label = label
        self._total = total
        self._step = 0

    def advance(self, component: str, cache_key: str) -> None:
        state = f"{self._label} — {component} ({self._step + 1} of {self._total})"
        is_cached = getattr(self._models, "is_cached", None)
        if callable(is_cached) and not is_cached(cache_key):
            state += COLD_LOAD_NOTE
        self._generation_outputs(ProgressGenerationOutput(state=state, progress=Progress(self._step, self._total)))
        self._step += 1


def active_loras(loras: Any, *, step_windows: bool = False, log_tag: str = "") -> List[Dict[str, Any]]:
    """Selected LoRA entries with a real file and a non-zero weight.

    Each returned entry carries ``window``: a
    :class:`~src.platform.runtime.native.lora.LoraStepWindow` when the raw entry
    asked for one via ``step_start``/``step_end``, else ``None`` (the entry is
    baked into the model at load time, exactly as before windows existed).

    ``step_windows`` is the caller's declaration that it can honour a window —
    i.e. that it hands windowed entries to the generator's step loop instead of
    baking them. A loader that leaves it False and receives a windowed entry
    raises: silently baking a LoRA the preset asked to switch off mid-run is
    the failure mode this contract exists to prevent (for the motivating
    ``krea2-turbo-sda``, an always-on application is a documented quality
    collapse, not a mild approximation).
    """
    out: List[Dict[str, Any]] = []
    for lora in loras or []:
        path = lora.get("file_path") or lora.get("model")
        weight = lora.get("weight", lora.get("strength"))
        if not path or str(path).strip() == "":
            continue
        try:
            if float(weight) == 0.0:
                continue
        except (TypeError, ValueError):
            continue
        window = parse_lora_window(lora)
        if window is not None and not step_windows:
            raise ValueError(
                f"[{log_tag or 'model_loader'}] LoRA {Path(str(path)).name} requests a step window "
                f"({window.describe()}), but this model family bakes LoRAs into the model at load "
                f"time and cannot switch one off mid-generation. Remove step_start/step_end, or use "
                f"a family whose generator supports step windows."
            )
        out.append({"file_path": str(path), "weight": float(weight), "window": window})
    return out


def partition_step_windows(
    loras: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split :func:`active_loras` output into ``(baked, windowed)``.

    ``baked`` is applied at load and folded into the model's LoRA fingerprint;
    ``windowed`` is deliberately kept OUT of both — a windowed LoRA patched
    into a cached model would leak past its window into every later generation
    that reuses the cache entry.
    """
    baked = [lora for lora in loras if lora.get("window") is None]
    windowed = [lora for lora in loras if lora.get("window") is not None]
    return baked, windowed


def load_windowed_lora_stack(loras: List[Dict[str, Any]]) -> List[Any]:
    """Load windowed entries into the ``(state_dict, strength, window,
    file_path)`` 4-tuples :class:`~src.platform.runtime.native.lora.LoraStepWindowHook`
    toggles -- ``file_path`` is what the hook's application-evidence diagnostics
    use as the adapter's durable source identity (see ``AdapterApplication``);
    without it every windowed LoRA would report as an indistinguishable
    ``"window-lora[i]"`` placeholder."""
    return [
        (load_torch_file(lora["file_path"], device="cpu")[0], lora["weight"], lora["window"], lora["file_path"])
        for lora in loras
    ]


# The native engine's tiering models activation/decode spikes explicitly
# (memory/tiering.py, calibrated against measured peaks), so the GPU manager's
# default 15% multiplicative margin would double-count the same reserve — on a
# 32GB card that stacked ~4.6GB (margin) + headroom and pushed a 26.3GB
# Krea-2 into partial-residency streaming. 3% covers allocator/driver slack.
_NATIVE_SAFETY_MARGIN = 0.97


def vram_budget(pipe_input: PipeInput, vram_limit_gb: Any, log_tag: str) -> Optional[float]:
    """Read the VRAM budget off the ``GPU`` service, logging under ``log_tag``."""
    gpu = pipe_input.input.get("GPU", None)
    if gpu is None:
        return None
    budget = gpu.get_vram_budget(vram_limit_gb, safety_margin=_NATIVE_SAFETY_MARGIN)
    logger.info("[%s] VRAM budget: %s", log_tag, budget)
    return budget


def load_lora_stack(loras: List[Dict[str, Any]]) -> List[Any]:
    """Load each LoRA file's state dict from disk (CPU) into the
    ``(state_dict, strength)`` stack shape ``apply_loras``/
    ``temporarily_applied_loras`` expect."""
    return [(load_torch_file(lora["file_path"], device="cpu")[0], lora["weight"]) for lora in loras]


def apply_loras_to(
    dit_model: NativeModel,
    loras: List[Dict[str, Any]],
    log_tag: str,
    generation_outputs: Optional[Callable] = None,
) -> List[AdapterApplication]:
    """Load and apply a LoRA stack onto ``dit_model``, logging under ``log_tag``.

    Returns the per-file application evidence (see ``AdapterApplication``) in
    ``loras`` order — ``[]`` for an empty stack. When ``generation_outputs``
    is given, a stack carrying any zero-effect or partially-ignored adapter
    also gets surfaced through it (see ``emit_lora_application_diagnostics``).
    Most callers pass ``None`` here and instead stash the returned evidence
    on ``dit_model`` and surface it once, after the acquire, via
    ``reemit_lora_application_diagnostics`` — that path also covers a
    ``MODELS`` cache HIT, which never calls this function at all.
    """
    if not loras:
        return []
    stack = load_lora_stack(loras)
    file_paths = [lora["file_path"] for lora in loras]
    patched, unmatched, reports = apply_loras_with_report(dit_model.module, stack, names=file_paths)
    if patched == 0:
        # A fully-unmatched stack means the LoRA had NO effect on the output —
        # silent-looking from the UI, so surface it loudly with enough detail
        # (the first unmatched stems) to identify the trainer's key dialect.
        names = ", ".join(Path(path).name for path in file_paths)
        logger.warning(
            "[%s] LoRA(s) had NO effect (%s): 0 params patched, %d unmatched keys. "
            "This architecture did not recognise the LoRA's key naming; first unmatched: %s",
            log_tag, names, len(unmatched), unmatched[:5],
        )
        emit_lora_application_diagnostics(generation_outputs, reports, log_tag)
        return reports
    logger.info("[%s] applied %d LoRA(s): %d params patched, %d unmatched keys",
                log_tag, len(loras), patched, len(unmatched))
    if unmatched:
        # Show the shape of what didn't match at INFO — a large unmatched set is
        # usually a whole sub-model (e.g. a text-encoder LoRA half we don't
        # apply) or an unknown dialect, and the stems identify which.
        logger.info("[%s] first unmatched LoRA keys: %s", log_tag, unmatched[:6])
    emit_lora_application_diagnostics(generation_outputs, reports, log_tag)
    return reports


def _bounded_source_id(source: str) -> str:
    """A bounded, directory-disambiguating identifier for a LoRA's ``source``.

    ``ModelGenerationOutput.name`` (``Path(source).stem``) is a friendly
    label, not an identifier — two trainers both shipping a
    ``v1.safetensors`` collapse to the same display name, and evidence keyed
    only on that name for two different files is not durable evidence at
    all. This is the last TWO path segments (parent dir + filename) plus an
    8-hex-char hash of the FULL source string, so it stays short (bounded)
    while remaining unique per distinct source even when the friendly name
    collides. A non-path source (the step-window hook's positional
    ``"window-lora[i]"`` placeholder, absent a real file) degrades to just
    that string plus its own hash — still bounded, still unique per label.
    """
    parts = Path(source).parts
    short = "/".join(parts[-2:]) if len(parts) >= 2 else (parts[-1] if parts else source)
    digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:8]
    return f"{short}#{digest}"


def emit_lora_application_diagnostics(
    generation_outputs: Optional[Callable],
    reports: Sequence[AdapterApplication],
    log_tag: str,
) -> None:
    """Surface per-adapter LoRA application evidence to the generation stream.

    A no-op when ``generation_outputs`` is ``None`` (a caller that has none
    to thread through, e.g. a direct/unit-test call) or when every adapter
    fully matched with nothing ignored — the common case stays exactly as
    quiet as before this existed. Otherwise
    emits ONE ``ProgressGenerationOutput`` warning, the same
    ``icon=Icon(name="alert-triangle")`` idiom generator pipes already use
    for a generation-visible caveat, plus one ``ModelsGenerationOutput``
    carrying the per-adapter evidence — captured durably into the
    generation's run report by the existing pipe_artifact recording, and
    distinct from the *requested* stack identity a loader's
    ``describe_models()`` emits up front. Each entry carries both the
    friendly ``name`` AND the unambiguous ``source_id`` (see
    :func:`_bounded_source_id`) plus a bounded sample of its unmatched keys,
    so the durable evidence distinguishes same-named files in different
    directories and keeps enough of the "why" to diagnose without the raw
    request needing to be replayed.
    """
    if generation_outputs is None or not reports:
        return
    flagged = [r for r in reports if r.zero_effect or r.unmatched_keys or r.ignored]
    if not flagged:
        return
    message = (
        f"[{log_tag}] one or more LoRAs had no effect on the output"
        if any(r.zero_effect for r in flagged)
        else f"[{log_tag}] one or more LoRAs applied only partially"
    )
    generation_outputs(ProgressGenerationOutput(state=message, icon=Icon(name="alert-triangle")))
    generation_outputs(ModelsGenerationOutput(models=[
        ModelGenerationOutput(
            name=Path(r.source).stem,
            type="lora",
            matched_params=r.matched_params,
            unmatched_keys=r.unmatched_keys,
            zero_effect=r.zero_effect,
            ignored=[f"{ic.kind}×{ic.count}" for ic in r.ignored] or None,
            source_id=_bounded_source_id(r.source),
            unmatched_sample=list(r.unmatched_sample) or None,
            reason=r.reason,
        )
        for r in reports
    ]))


def reemit_lora_application_diagnostics(
    dit_model: NativeModel,
    generation_outputs: Optional[Callable],
    log_tag: str,
) -> None:
    """Re-surface whatever LoRA application evidence is stashed on
    ``dit_model`` (``_active_lora_application`` — set by ``apply_loras_to``/
    ``sync_loras`` the last time this DiT's stack was actually applied),
    once per generation, regardless of whether THIS acquire was a fresh load
    or a cache hit reusing already-patched weights.

    For the families whose ``MODELS`` fingerprint folds in the LoRA stack
    (Anima/Qwen/Z-Image/LTX/MiniMax-H3/Wan22 — a stack change busts the cache
    and reloads), a cache HIT never re-runs ``load_dit``/``apply_loras_to``
    at all, so nothing about that stack would otherwise be re-announced on a
    later generation reusing it; this call, right after acquiring the DiT
    component, is the loader's one fixed point for surfacing it regardless
    of hit or miss. Flux/Krea-2 get the same guarantee from ``sync_loras``
    itself (its own cache-HIT no-op branch) and must NOT also call this on
    the path ``sync_loras`` already ran on — only on the uncached direct-load
    branch neither of them ever reaches.
    """
    emit_lora_application_diagnostics(
        generation_outputs, getattr(dit_model, "_active_lora_application", ()), log_tag,
    )
