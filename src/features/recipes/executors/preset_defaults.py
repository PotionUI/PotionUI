from __future__ import annotations

from typing import Any, Callable, Dict, List

from src.features.presets.mode_fields import mode_inventory
from src.features.recipes.executors.base import StepContext, StepResult


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == []


class PresetDefaultsExecutor:
    def __init__(self, preset_loader: Any, preset_db_repo: Any, model_available: Callable[[Any, str], bool]):
        self.preset_loader = preset_loader
        self.preset_db_repo = preset_db_repo
        self.model_available = model_available

    def execute(self, context: StepContext) -> StepResult:
        plans = context.plans(context.step.params.get("plan"))
        if not plans:
            return StepResult.fail(
                "PRESET_DEFAULTS_NO_PLAN",
                "There is no approved install plan to take the default files from.",
            )
        applied: List[Dict[str, Any]] = []
        kept: List[Dict[str, Any]] = []
        for plan in plans:
            preset_id = plan.get("preset_id")
            preset = self.preset_loader.load_preset_by_id(preset_id)
            if preset is None:
                return StepResult.fail("PRESET_MISSING_ON_DISK", f"The preset '{preset_id}' isn't available on this installation.")
            if not self.preset_db_repo.is_preset_installed(preset_id):
                return StepResult.fail(
                    "PRESET_NOT_INSTALLED",
                    f"'{preset.name}' isn't installed yet, so its default files can't be set.",
                    suggested_repair="Run the step that installs the preset first, then retry.",
                )
            stored = self.preset_db_repo.get_preset_form_overrides(preset_id) or {}
            changed = False
            chosen_modes = set(plan.get("modes") or [])
            for chosen in (plan.get("chosen") or {}).values():
                filename = chosen.get("filename")
                model_type = chosen.get("model_type") or ""
                if not filename:
                    continue
                for mode, field_name in (chosen.get("fields") or {}).items():
                    if mode not in chosen_modes or mode not in (preset.modes or {}):
                        continue
                    spec = mode_inventory(preset.modes[mode]).get(field_name)
                    if spec is None:
                        continue
                    entry = dict((stored.get(mode) or {}).get(field_name) or {})
                    current = entry.get("default")
                    effective = spec.default if _empty(current) else current
                    record = {"mode": mode, "field": field_name, "value": filename}
                    if not _empty(effective) and self.model_available(effective, model_type):
                        kept.append({**record, "value": effective})
                        continue
                    entry["default"] = filename
                    stored.setdefault(mode, {})[field_name] = entry
                    applied.append(record)
                    changed = True
            if changed:
                self.preset_db_repo.set_preset_form_overrides(preset_id, stored)
        return StepResult.ok({"set": applied, "kept": kept})
