from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from src.features.models.recommendations import format_size
from src.features.recipes.executors.base import LintContext
from src.features.recipes.mode_slots import derive_mode_slots
from src.features.recipes.schema import Recipe


def _ids(value: Any) -> List[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _plan_steps(recipe: Recipe):
    return [step for step in recipe.steps if step.kind == "modes.plan"]


def _mode_issues(recipe: Recipe, step, preset, errors: List[str], warnings: List[str]) -> None:
    where = f"step '{step.key}'"
    declared = {ref.preset_id for ref in recipe.presets}
    if step.params.get("preset_id") not in declared:
        errors.append(f"{where}: preset_id must also be listed under presets: (R1)")
    modes = step.params.get("modes", "all")
    listed = list(preset.modes) if modes == "all" else _ids(modes)
    selected = _ids(step.params.get("selected"))
    locked = _ids(step.params.get("locked"))
    for key, values in (("modes", [] if modes == "all" else listed), ("selected", selected), ("locked", locked)):
        for mode in values:
            if mode not in (preset.modes or {}):
                errors.append(f"{where}: params.{key} names '{mode}', which is not a mode of '{preset.name}' (R1)")
    plan = derive_mode_slots(preset, [m for m in listed if m in (preset.modes or {})])
    for mode_id in selected:
        mode = plan.modes.get(mode_id)
        if mode is None:
            continue
        for slot_id in mode.slot_ids:
            slot = plan.slots[slot_id]
            if not slot.manual:
                continue
            message = f"{where}: mode '{mode_id}' needs '{slot.label}', which has no downloadable recommendation (R3)"
            (errors if mode_id in locked else warnings).append(message)
    offered_extras = {sid for mode in plan.modes.values() for sid in mode.extra_slot_ids}
    for slot_id in _ids(step.params.get("preselect_extras")):
        if slot_id not in offered_extras:
            errors.append(f"{where}: params.preselect_extras names '{slot_id}', which is not an extra file of these modes")
    derived_names = {
        variant.filename.lower()
        for slot in plan.slots.values()
        if slot.artifact is not None
        for variant in slot.artifact.variants
    }
    for artifact in recipe.artifacts:
        names = [artifact.filename] + [v.filename for v in artifact.variants]
        if any(name and name.lower() in derived_names for name in names):
            warnings.append(f"artifact '{artifact.id}': {where} already derives this file from the preset; drop it (R4)")
    if recipe.smoke and recipe.smoke.preset_id == step.params.get("preset_id") and recipe.smoke.mode not in locked:
        errors.append(f"smoke: mode '{recipe.smoke.mode}' must be in {where} params.locked, so it is always installed (R6)")


def lint_recipe(
    recipe: Recipe,
    preset_index: Dict[str, Any],
    executor_for: Optional[Callable[[str], Any]] = None,
    core_kinds: Optional[set] = None,
) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    for step in _plan_steps(recipe):
        preset = preset_index.get(step.params.get("preset_id"))
        if preset is None:
            errors.append(f"step '{step.key}': preset_id '{step.params.get('preset_id')}' does not exist (R1)")
            continue
        _mode_issues(recipe, step, preset, errors, warnings)
    referenced = {
        artifact_id
        for step in recipe.steps
        for artifact_id in _ids(step.params.get("artifact_ids"))
    }
    for artifact in recipe.artifacts:
        if artifact.id not in referenced:
            warnings.append(f"artifact '{artifact.id}': no step fetches it, so it is never downloaded (R5)")
    if executor_for is not None:
        context = LintContext(preset_index=preset_index)
        for step in recipe.steps:
            if core_kinds is not None and step.kind in core_kinds:
                continue
            executor = executor_for(step.kind)
            validate = getattr(executor, "validate_params", None)
            if validate is None:
                continue
            try:
                issues = validate(dict(step.params), recipe, context) or []
            except Exception as exc:
                issues = [f"its params check raised {exc}"]
            errors.extend(f"step '{step.key}' ({step.kind}): {issue} (R7)" for issue in issues)
    return errors, warnings


def explain_recipe(recipe: Recipe, preset_index: Dict[str, Any]) -> List[str]:
    lines = [f"{recipe.id} ({recipe.name})"]
    steps = _plan_steps(recipe)
    if not steps:
        lines.append("  no modes.plan step: files come from the recipe's own artifacts")
        for artifact in recipe.artifacts:
            lines.append(f"  - {artifact.id}: {artifact.filename} {format_size(artifact.size_bytes)}".rstrip())
        return lines
    for step in steps:
        preset = preset_index.get(step.params.get("preset_id"))
        if preset is None:
            lines.append(f"  step '{step.key}': preset '{step.params.get('preset_id')}' not found")
            continue
        modes = step.params.get("modes", "all")
        listed = list(preset.modes) if modes == "all" else _ids(modes)
        plan = derive_mode_slots(preset, [m for m in listed if m in (preset.modes or {})])
        selected = set(_ids(step.params.get("selected")))
        locked = set(_ids(step.params.get("locked")))
        lines.append(f"  step '{step.key}': {preset.name} ({len(plan.modes)} modes)")
        for mode in plan.modes.values():
            flags = "locked" if mode.id in locked else "selected" if mode.id in selected else "offered"
            lines.append(f"    mode {mode.id} [{flags}] - {mode.label}")
            for slot_id in mode.slot_ids:
                lines.extend(_slot_lines(plan.slots[slot_id], "required"))
            for slot_id in mode.extra_slot_ids:
                lines.extend(_slot_lines(plan.slots[slot_id], "extra"))
    return lines


def _slot_lines(slot, role: str) -> List[str]:
    if slot.manual:
        return [f"      {slot.id} ({role}, {slot.model_type}): manual - no downloadable recommendation"]
    lines = [f"      {slot.id} ({role}, {slot.model_type}, used by {len(slot.used_by)} mode(s))"]
    for variant in slot.artifact.variants:
        source = variant.provider_hint.get("source") or ("link" if variant.provider_hint.get("download_url") else "?")
        marker = "*" if variant.default else " "
        lines.append(
            f"       {marker} {variant.id} [{variant.precision or '-'}] {variant.filename} "
            f"{format_size(variant.size_bytes) or 'size unknown'} via {source}"
        )
    return lines
