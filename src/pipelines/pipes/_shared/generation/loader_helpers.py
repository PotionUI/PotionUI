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
from typing import Any, Dict, List, Optional

from src.platform.runtime.native.engine import NativeModel
from src.platform.runtime.native.io.safetensors_loader import load_torch_file
from src.platform.runtime.native.lora import apply_loras
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


def active_loras(loras: Any) -> List[Dict[str, Any]]:
    """Selected LoRA entries with a real file and a non-zero weight."""
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
        out.append({"file_path": str(path), "weight": float(weight)})
    return out


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
