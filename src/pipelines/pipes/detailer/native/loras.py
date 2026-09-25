from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Sequence

from src.pipelines.pipes._shared.generation.loader_helpers import (
    LORA_OPTION_STEP_WINDOW,
    active_loras,
    apply_loras_to,
    lora_stack_fingerprint,
)
from src.pipelines.pipes._shared.generation.loader_lifecycle import NO_LORAS, sync_loras

KEEP = "keep"
DROP = "drop"
SELECT = "select"
MODES = (KEEP, DROP, SELECT)

LOG_TAG = "FACE DETAILER"


def face_loras(entries: Optional[Sequence[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    return active_loras(entries, log_tag=LOG_TAG)


def baked_loras(entries: Optional[Sequence[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    return [
        lora for lora in active_loras(entries, supported_options={LORA_OPTION_STEP_WINDOW}, log_tag=LOG_TAG)
        if lora.get("window") is None
    ]


def _apply(dit_model, loras):
    return apply_loras_to(dit_model, loras, LOG_TAG)


def _switch(dit, loras: Sequence[Dict[str, Any]], what: str) -> None:
    stack = list(loras)
    fingerprint = lora_stack_fingerprint(stack) or NO_LORAS
    try:
        sync_loras(dit, stack, fingerprint, _apply, log_tag=LOG_TAG)
    except Exception as exc:
        raise RuntimeError(f"detailer/native: {what}: {exc}") from exc


@contextmanager
def face_lora_scope(dit, mode: str, loras: Sequence[Dict[str, Any]],
                    run_loras: Sequence[Dict[str, Any]]) -> Iterator[List[Dict[str, Any]]]:
    target = list(loras) if mode == SELECT else []
    if dit is None or mode not in (DROP, SELECT):
        yield []
        return

    _switch(dit, target, "could not switch LoRAs for the face pass")
    try:
        yield target
    finally:
        _switch(dit, run_loras, "could not restore the run's LoRAs after the face pass")
