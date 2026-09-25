"""Runtime LoRA support for the native engine (Flux family v1)."""

from __future__ import annotations

from .apply import (
    AdapterApplication,
    IgnoredContribution,
    LoraStateSnapshot,
    apply_loras,
    apply_loras_with_report,
    remove_loras,
    restore_lora_state,
    snapshot_lora_state,
    temporarily_applied_loras,
)
from .key_mapping import (
    LoraDelta,
    build_flux_lora_key_map,
    build_krea2_lora_key_map,
    build_minimax_h3_lora_key_map,
    map_lora_keys,
)
from .row_mask import (
    FULL,
    ROWS,
    SKIP,
    RowMaskTarget,
    confine_bias_deltas,
    lora_row_mask,
    lora_row_window,
)
from .step_window import (
    WINDOW_KEYS,
    LoraStepWindow,
    LoraStepWindowHook,
    has_lora_window,
    parse_lora_window,
)

__all__ = [
    "FULL",
    "ROWS",
    "SKIP",
    "RowMaskTarget",
    "confine_bias_deltas",
    "lora_row_mask",
    "lora_row_window",
    "AdapterApplication",
    "IgnoredContribution",
    "LoraDelta",
    "LoraStateSnapshot",
    "LoraStepWindow",
    "LoraStepWindowHook",
    "WINDOW_KEYS",
    "apply_loras",
    "apply_loras_with_report",
    "build_flux_lora_key_map",
    "build_krea2_lora_key_map",
    "build_minimax_h3_lora_key_map",
    "has_lora_window",
    "map_lora_keys",
    "parse_lora_window",
    "remove_loras",
    "restore_lora_state",
    "snapshot_lora_state",
    "temporarily_applied_loras",
]
