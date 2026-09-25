from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from src.features.recipes.executors._artifact_lookup import find_artifact_model
from src.features.recipes.schema import RecipeArtifact
from src.features.recipes.variants import (
    VariantChoice,
    choose_variant,
    describe_single_file,
    describe_variant,
)
from src.platform.runtime.gpu_profile import GpuProfile


def found_as(model: Any, expected_filename: str) -> Optional[str]:
    path = getattr(model, "file_path", None) if model is not None else None
    if path and getattr(model, "filename", None) != expected_filename:
        return path
    return None


def describe_slot(
    model_repository: Any, artifact: RecipeArtifact, gpu: GpuProfile
) -> Tuple[Dict[str, Any], VariantChoice, Dict[str, Any]]:
    installed: Dict[str, Any] = {}
    if artifact.variants:
        for variant in artifact.variants:
            model = find_artifact_model(model_repository, artifact.resolve(variant.id))
            if model is not None:
                installed[variant.id] = model
        choice = choose_variant(artifact, gpu, installed.keys())
        variants = [
            describe_variant(
                variant,
                gpu,
                installed=variant.id in installed,
                found_as=found_as(installed.get(variant.id), variant.filename),
            )
            for variant in artifact.variants
        ]
    else:
        model = find_artifact_model(model_repository, artifact)
        if model is not None:
            installed[artifact.id] = model
        choice = VariantChoice(
            artifact.id,
            "Already installed" if model is not None else "The only file this recipe offers here",
        )
        variants = [
            describe_single_file(
                artifact,
                installed=model is not None,
                found_as=found_as(model, artifact.filename),
            )
        ]
    slot = {
        "id": artifact.id,
        "label": artifact.display_name or artifact.filename,
        "kind": artifact.kind,
        "model_type": artifact.model_type,
        "required": artifact.required,
        "variants": variants,
        "recommended_variant_id": choice.variant_id,
        "reason": choice.reason,
    }
    return slot, choice, installed
