"""Resolve a recipe artifact to the index row that backs it."""

from typing import Any, Optional


def find_artifact_model(model_repository: Any, artifact: Any, type_manager: Optional[Any] = None) -> Optional[Any]:
    model = model_repository.get_by_identity(artifact.model_type, artifact.filename)
    if model is not None:
        return model
    checksum = getattr(artifact, "checksum", None)
    sha256 = getattr(checksum, "value", None) if checksum else None
    lookup = getattr(model_repository, "get_by_sha256", None)
    if not sha256 or lookup is None:
        return None
    by_hash = lookup(sha256)
    if by_hash is None or not getattr(by_hash, "is_available", True):
        return None
    if by_hash.model_type != artifact.model_type and type_manager is not None:
        type_manager.assert_type(sha256, artifact.model_type, "recipe")
        by_hash = lookup(sha256) or by_hash
    return by_hash


def find_slot_model(
    model_repository: Any, artifact: Any, variant_id: Optional[str] = None, type_manager: Optional[Any] = None
) -> Optional[Any]:
    variants = getattr(artifact, "variants", None) or ()
    if not variants:
        return find_artifact_model(model_repository, artifact, type_manager)
    ordered = [v.id for v in variants]
    if variant_id in ordered:
        ordered.remove(variant_id)
        ordered.insert(0, variant_id)
    for candidate in ordered:
        model = find_artifact_model(model_repository, artifact.resolve(candidate), type_manager)
        if model is not None:
            return model
    return None
