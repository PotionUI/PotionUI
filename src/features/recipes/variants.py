from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional

from src.features.recipes.schema import RecipeArtifact, RecipeArtifactVariant, RecipeVariantRule
from src.platform.runtime.gpu_profile import GENERATION_LABELS, GpuProfile

_UNSUPPORTED_NOTES = {"nvfp4": "nvfp4 needs an RTX 50-series (Blackwell) GPU"}


@dataclass(frozen=True)
class VariantAssessment:
    variant_id: str
    supported: bool
    fast: bool
    recommended: bool
    note: Optional[str] = None


@dataclass(frozen=True)
class VariantChoice:
    variant_id: str
    reason: str


def _rule_matches(rule: RecipeVariantRule, gpu: GpuProfile) -> bool:
    if rule.min_vram_gb is not None and gpu.vram_class_gb < rule.min_vram_gb:
        return False
    if rule.generations and gpu.generation not in rule.generations:
        return False
    return True


def _rules_match(variant: RecipeArtifactVariant, gpu: GpuProfile) -> bool:
    if not variant.recommended_for:
        return True
    return any(_rule_matches(rule, gpu) for rule in variant.recommended_for)


def _generation_names(generations: Iterable[str]) -> str:
    return ", ".join(GENERATION_LABELS.get(g, g) for g in generations)


def _rule_miss_note(variant: RecipeArtifactVariant, gpu: GpuProfile) -> str:
    vram_mins = [
        rule.min_vram_gb
        for rule in variant.recommended_for
        if rule.min_vram_gb is not None and (not rule.generations or gpu.generation in rule.generations)
    ]
    if vram_mins:
        return f"Best with {_format_gb(min(vram_mins))} of VRAM or more"
    generations: List[str] = []
    for rule in variant.recommended_for:
        for generation in rule.generations:
            if generation not in generations:
                generations.append(generation)
    return f"Recommended for {_generation_names(generations)}"


def _format_gb(value: float) -> str:
    return f"{int(value)} GB" if float(value).is_integer() else f"{value:g} GB"


def assess_variant(variant: RecipeArtifactVariant, gpu: GpuProfile) -> VariantAssessment:
    if not gpu.has_gpu:
        return VariantAssessment(variant.id, supported=False, fast=False, recommended=False)
    supported = gpu.supports(variant.precision)
    fast = gpu.is_fast(variant.precision)
    recommended = supported and _rules_match(variant, gpu)
    note: Optional[str] = None
    if not supported:
        note = _UNSUPPORTED_NOTES.get(variant.precision, f"{variant.precision} doesn't run on {gpu.generation_label}")
    elif not recommended:
        note = _rule_miss_note(variant, gpu)
    elif not fast:
        note = f"{variant.precision} isn't hardware-accelerated on {gpu.generation_label}"
    return VariantAssessment(variant.id, supported=supported, fast=fast, recommended=recommended, note=note)


def _size_key(variant: RecipeArtifactVariant) -> float:
    return variant.size_bytes if variant.size_bytes is not None else math.inf


def _pick_for_gpu(artifact: RecipeArtifact, gpu: GpuProfile) -> VariantChoice:
    default = artifact.default_variant
    if not gpu.has_gpu:
        return VariantChoice(default.id, "No GPU detected, so this uses the recipe's default")
    assessments = {v.id: assess_variant(v, gpu) for v in artifact.variants}
    vram = _format_gb(gpu.vram_class_gb)
    for variant in artifact.variants:
        if assessments[variant.id].recommended:
            return VariantChoice(variant.id, f"Fits your {vram}")
    supported = [v for v in artifact.variants if assessments[v.id].supported]
    fast = [v for v in supported if assessments[v.id].fast]
    pool = fast or supported
    if not pool:
        return VariantChoice(default.id, f"Nothing here runs natively on {gpu.generation_label}, so this uses the recipe's default")
    smallest = min(pool, key=_size_key)
    return VariantChoice(smallest.id, f"Smallest option for your {vram}; part of it will offload to system RAM")


def choose_variant(
    artifact: RecipeArtifact,
    gpu: GpuProfile,
    installed_ids: Iterable[str] = (),
) -> VariantChoice:
    installed = set(installed_ids)
    ideal = _pick_for_gpu(artifact, gpu)
    if not installed:
        return ideal
    if ideal.variant_id in installed:
        return VariantChoice(ideal.variant_id, "Already installed")
    for variant in artifact.variants:
        if variant.id in installed:
            return VariantChoice(variant.id, "Already installed, so nothing to download")
    return ideal


def describe_variant(
    variant: RecipeArtifactVariant,
    gpu: GpuProfile,
    *,
    installed: bool,
    found_as: Optional[str] = None,
) -> Dict[str, Any]:
    assessment = assess_variant(variant, gpu)
    entry: Dict[str, Any] = {
        "id": variant.id,
        "label": variant.label,
        "precision": variant.precision,
        "filename": variant.filename,
        "size_bytes": variant.size_bytes,
        "installed": installed,
        "gated": variant.gated,
        "license_url": variant.license_url,
        "uploader": variant.uploader or None,
        "source": (variant.provider_hint or {}).get("source"),
        "repo_id": (variant.provider_hint or {}).get("model_id"),
        "source_url": variant.source_url,
        "is_recipe_default": variant.default,
        "fast": assessment.fast,
        "recommended": assessment.recommended,
        "note": assessment.note,
    }
    if found_as:
        entry["found_as"] = found_as
    return entry


def describe_single_file(artifact: RecipeArtifact, *, installed: bool, found_as: Optional[str] = None) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "id": artifact.id,
        "label": artifact.display_name or artifact.filename,
        "precision": None,
        "filename": artifact.filename,
        "size_bytes": artifact.size_bytes,
        "installed": installed,
        "gated": artifact.gated,
        "license_url": artifact.license_url,
        "uploader": None,
        "source": (artifact.provider_hint or {}).get("source"),
        "repo_id": (artifact.provider_hint or {}).get("model_id"),
        "source_url": None,
        "is_recipe_default": True,
        "fast": None,
        "recommended": True,
        "note": None,
    }
    if found_as:
        entry["found_as"] = found_as
    return entry


def resolve_selection(artifact: RecipeArtifact, variant_id: Optional[str]) -> RecipeArtifact:
    if not artifact.variants:
        return artifact
    if artifact.get_variant(variant_id) is None:
        variant_id = artifact.default_variant.id
    return artifact.resolve(variant_id)


def validate_selections(
    selections: Mapping[str, Any],
    slots: Iterable[Mapping[str, Any]],
) -> List[str]:
    known = {slot.get("id"): {v.get("id") for v in slot.get("variants") or []} for slot in slots}
    issues: List[str] = []
    for slot_id, variant_id in selections.items():
        if slot_id not in known:
            issues.append(f"'{slot_id}' is not one of the model slots this step asked about.")
        elif not isinstance(variant_id, str) or variant_id not in known[slot_id]:
            issues.append(f"'{variant_id}' is not a variant of '{slot_id}'.")
    return issues
