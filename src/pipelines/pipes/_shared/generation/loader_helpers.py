"""Shared model-loader helpers for the native single/multi-DiT loader pipes.

Every native model-loader pipe (Anima, Flux, Krea-2, Qwen, Wan22, Z-Image)
re-implemented the same private helpers for resolving a model-picker file
path, filtering active LoRAs, reading the VRAM budget off the ``GPU`` service,
and applying a LoRA stack to a loaded DiT. This module is the single copy;
callers pass a ``log_tag`` (e.g. ``"MODEL LOADER FLUX"``) to preserve their
existing per-preset log lines.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.platform.runtime.native.engine import NativeModel
from src.platform.runtime.native.io.safetensors_loader import load_torch_file
from src.platform.runtime.native.lora import apply_loras, parse_lora_window
from src.pipelines.contracts import logger
from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import Progress, ProgressGenerationOutput

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
    """Load windowed entries into the ``(state_dict, strength, window)`` triples
    :class:`~src.platform.runtime.native.lora.LoraStepWindowHook` toggles."""
    return [
        (load_torch_file(lora["file_path"], device="cpu")[0], lora["weight"], lora["window"])
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


def apply_loras_to(dit_model: NativeModel, loras: List[Dict[str, Any]], log_tag: str) -> None:
    """Load and apply a LoRA stack onto ``dit_model``, logging under ``log_tag``."""
    if not loras:
        return
    stack = load_lora_stack(loras)
    patched, unmatched = apply_loras(dit_model.module, stack)
    if patched == 0:
        # A fully-unmatched stack means the LoRA had NO effect on the output —
        # silent-looking from the UI, so surface it loudly with enough detail
        # (the first unmatched stems) to identify the trainer's key dialect.
        names = ", ".join(Path(lora["file_path"]).name for lora in loras)
        logger.warning(
            "[%s] LoRA(s) had NO effect (%s): 0 params patched, %d unmatched keys. "
            "This architecture did not recognise the LoRA's key naming; first unmatched: %s",
            log_tag, names, len(unmatched), unmatched[:5],
        )
        return
    logger.info("[%s] applied %d LoRA(s): %d params patched, %d unmatched keys",
                log_tag, len(loras), patched, len(unmatched))
    if unmatched:
        # Show the shape of what didn't match at INFO — a large unmatched set is
        # usually a whole sub-model (e.g. a text-encoder LoRA half we don't
        # apply) or an unknown dialect, and the stems identify which.
        logger.info("[%s] first unmatched LoRA keys: %s", log_tag, unmatched[:6])
