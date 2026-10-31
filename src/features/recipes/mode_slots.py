from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from src.features.models.recommendations import Recommendation
from src.features.presets.mode_fields import (
    field_model_type,
    field_recommendations,
    mode_requirement_fields,
    model_fields,
)
from src.features.presets.mode_labels import mode_display_name
from src.features.presets.templates import FieldTemplate, ModeTemplate, PresetTemplate, default_form_name
from src.features.recipes.schema import (
    RecipeArtifact,
    RecipeArtifactVariant,
    RecipeChecksum,
    RecipeVariantRule,
)

class PlanApprovalError(ValueError):
    pass


_VARIANT_SLUG_RE = re.compile(r"[^a-z0-9_-]+")
_SUFFIX_RE = re.compile(r"\.(safetensors|ckpt|pt|pth|bin|gguf|sft)$", re.IGNORECASE)


@dataclass(frozen=True)
class DerivedSlot:
    id: str
    label: str
    model_type: str
    fields: Dict[str, str]
    required_in: Tuple[str, ...]
    extra_in: Tuple[str, ...]
    used_by: Tuple[str, ...]
    artifact: Optional[RecipeArtifact]
    recommendations: Tuple[Recommendation, ...] = ()

    @property
    def manual(self) -> bool:
        return self.artifact is None

    @property
    def required(self) -> bool:
        return bool(self.required_in)

    @property
    def extra(self) -> bool:
        return not self.required_in and bool(self.extra_in)

    def field_for(self, mode: str) -> Optional[str]:
        return self.fields.get(mode)


@dataclass(frozen=True)
class DerivedMode:
    id: str
    label: str
    short_description: Optional[str]
    slot_ids: Tuple[str, ...]
    extra_slot_ids: Tuple[str, ...]
    any_of: Tuple[Tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class ModeSlotPlan:
    preset_id: str
    preset_name: str
    engine: str
    modes: Dict[str, DerivedMode] = field(default_factory=dict)
    slots: Dict[str, DerivedSlot] = field(default_factory=dict)

    def mode_slots(self, mode_id: str) -> List[DerivedSlot]:
        mode = self.modes.get(mode_id)
        return [self.slots[sid] for sid in mode.slot_ids] if mode else []

    @property
    def downloadable(self) -> bool:
        return any(not slot.manual for slot in self.slots.values())


def variant_id_for(filename: str) -> str:
    stem = _SUFFIX_RE.sub("", filename).lower()
    slug = _VARIANT_SLUG_RE.sub("-", stem).strip("-_")
    return slug or "file"


def _rules(rec: Recommendation) -> Tuple[RecipeVariantRule, ...]:
    return tuple(
        RecipeVariantRule(min_vram_gb=rule.min_vram_gb, generations=rule.generations)
        for rule in rec.recommended_for
    )


def _variant_label(rec: Recommendation) -> str:
    return rec.precision or _SUFFIX_RE.sub("", rec.filename)


def _artifact_for(slot_id: str, label: str, model_type: str, recs: Sequence[Recommendation]) -> Optional[RecipeArtifact]:
    downloadable = [rec for rec in recs if rec.downloadable]
    if not downloadable:
        return None
    default_index = next((i for i, rec in enumerate(downloadable) if rec.default), 0)
    variants: List[RecipeArtifactVariant] = []
    used_ids: set = set()
    for index, rec in enumerate(downloadable):
        base = variant_id_for(rec.filename)
        variant_id = base
        suffix = 2
        while variant_id in used_ids:
            variant_id = f"{base}-{suffix}"
            suffix += 1
        used_ids.add(variant_id)
        variants.append(
            RecipeArtifactVariant(
                id=variant_id,
                label=_variant_label(rec),
                precision=rec.precision or "",
                filename=rec.filename,
                size_bytes=rec.known_size_bytes,
                checksum=RecipeChecksum(algorithm="sha256", value=rec.sha256) if rec.sha256 else None,
                provider_hint=rec.provider_hint(),
                gated=rec.gated,
                license_url=rec.license_url,
                uploader="",
                source_url=None,
                recommended_for=_rules(rec),
                default=index == default_index,
            )
        )
    default = variants[default_index]
    return RecipeArtifact(
        id=slot_id,
        kind=model_type,
        model_type=model_type,
        filename=default.filename,
        display_name=label,
        required=True,
        size_bytes=default.size_bytes,
        checksum=default.checksum,
        capability="model-lookup",
        provider_hint=dict(default.provider_hint),
        gated=default.gated,
        license_url=default.license_url,
        variants=tuple(variants),
    )


def _short_description(mode: ModeTemplate) -> Optional[str]:
    default_name = default_form_name(mode)
    for form in mode.forms or []:
        if form.name == default_name:
            return form.short_description
    return None


@dataclass
class _Group:
    signature: Tuple
    field_name: str
    label: str
    model_type: str
    recs: Tuple[Recommendation, ...]
    fields: Dict[str, str]
    required_in: List[str]
    extra_in: List[str]


def _signature(model_type: str, spec: FieldTemplate, recs: Sequence[Recommendation]) -> Tuple:
    downloadable = [rec for rec in recs if rec.downloadable]
    if not downloadable:
        return ("manual", model_type, spec.name)
    return ("files", model_type, frozenset(rec.filename.lower() for rec in downloadable))


def derive_mode_slots(preset: PresetTemplate, modes: Optional[Iterable[str]] = None) -> ModeSlotPlan:
    wanted = list(modes) if modes is not None else list((preset.modes or {}).keys())
    groups: Dict[Tuple, _Group] = {}
    mode_entries: Dict[str, Dict[str, List[Tuple]]] = {}
    any_of_fields: Dict[str, List[List[str]]] = {}
    for mode_id in wanted:
        mode = (preset.modes or {}).get(mode_id)
        if mode is None:
            continue
        named = mode_requirement_fields(mode)
        required_names = set(named["required"])
        group_names = {name for group in named["any_of"] for name in group}
        any_of_fields[mode_id] = [list(group) for group in named["any_of"]]
        entries: Dict[str, List[Tuple]] = {"required": [], "extra": [], "by_field": []}
        for field_name, spec in model_fields(mode).items():
            recs = tuple(field_recommendations(spec))
            is_required = bool(spec.required) or field_name in required_names
            downloadable = any(rec.downloadable for rec in recs)
            if not is_required and not downloadable:
                continue
            if field_name in group_names and not spec.required:
                is_required = False
            model_type = field_model_type(spec)
            signature = _signature(model_type, spec, recs)
            if signature in groups and mode_id in groups[signature].fields:
                signature = signature + (field_name,)
            group = groups.get(signature)
            if group is None:
                group = _Group(
                    signature=signature,
                    field_name=field_name,
                    label=spec.label or field_name,
                    model_type=model_type,
                    recs=recs,
                    fields={},
                    required_in=[],
                    extra_in=[],
                )
                groups[signature] = group
            group.fields[mode_id] = field_name
            (group.required_in if is_required else group.extra_in).append(mode_id)
            entries["required" if is_required else "extra"].append(signature)
            entries["by_field"].append((field_name, signature))
        mode_entries[mode_id] = entries

    ordered = list(groups.values())
    widths: Dict[str, List[int]] = {}
    for group in ordered:
        widths.setdefault(group.field_name, []).append(len(group.fields))
    bare: Dict[str, Tuple] = {}
    for group in ordered:
        sizes = sorted(widths[group.field_name], reverse=True)
        if len(group.fields) == sizes[0] and (len(sizes) == 1 or sizes[1] < sizes[0]):
            bare.setdefault(group.field_name, group.signature)

    slot_ids: Dict[Tuple, str] = {}
    slots: Dict[str, DerivedSlot] = {}
    for group in ordered:
        if bare.get(group.field_name) == group.signature:
            slot_id = group.field_name
        else:
            slot_id = f"{group.field_name}@{next(iter(group.fields))}"
        slot_ids[group.signature] = slot_id
        used_by = tuple(m for m in wanted if m in group.fields)
        slots[slot_id] = DerivedSlot(
            id=slot_id,
            label=group.label,
            model_type=group.model_type,
            fields=dict(group.fields),
            required_in=tuple(m for m in wanted if m in group.required_in),
            extra_in=tuple(m for m in wanted if m in group.extra_in),
            used_by=used_by,
            artifact=_artifact_for(slot_id, group.label, group.model_type, group.recs),
            recommendations=group.recs,
        )

    derived_modes: Dict[str, DerivedMode] = {}
    for mode_id in wanted:
        mode = (preset.modes or {}).get(mode_id)
        entries = mode_entries.get(mode_id)
        if mode is None or entries is None:
            continue
        by_field = {name: slot_ids[sig] for name, sig in entries["by_field"]}
        groups_for_mode = tuple(
            tuple(by_field[name] for name in group if name in by_field)
            for group in any_of_fields.get(mode_id, [])
        )
        derived_modes[mode_id] = DerivedMode(
            id=mode_id,
            label=mode_display_name(mode_id, mode.mode_label),
            short_description=_short_description(mode),
            slot_ids=tuple(dict.fromkeys(slot_ids[sig] for sig in entries["required"])),
            extra_slot_ids=tuple(dict.fromkeys(slot_ids[sig] for sig in entries["extra"])),
            any_of=tuple(group for group in groups_for_mode if group),
        )

    return ModeSlotPlan(
        preset_id=preset.id,
        preset_name=preset.name,
        engine=preset.engine or "",
        modes=derived_modes,
        slots=slots,
    )


def has_downloadable_modes(preset: PresetTemplate) -> bool:
    for mode in (preset.modes or {}).values():
        for spec in model_fields(mode).values():
            if any(rec.downloadable for rec in field_recommendations(spec)):
                return True
    return False


def recipe_required_artifacts(recipe, preset_loader) -> List[RecipeArtifact]:
    artifacts: Dict[str, RecipeArtifact] = {a.id: a for a in recipe.artifacts if a.required}
    for step in recipe.steps:
        if step.kind != "modes.plan" or preset_loader is None:
            continue
        preset = preset_loader.load_preset_by_id(step.params.get("preset_id"))
        if preset is None:
            continue
        wanted = list(dict.fromkeys(list(step.params.get("selected") or []) + list(step.params.get("locked") or [])))
        offered = step.params.get("modes", "all")
        plan = derive_mode_slots(preset, None if offered == "all" else [m for m in offered if m in (preset.modes or {})])
        for mode in (plan.modes[m] for m in wanted if m in plan.modes):
            for slot_id in mode.slot_ids:
                slot = plan.slots[slot_id]
                if slot.artifact is not None:
                    artifacts.setdefault(f"{step.key}:{slot_id}", slot.artifact)
    return list(artifacts.values())
