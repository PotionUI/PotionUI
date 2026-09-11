"""Resolve a recipe artifact to the index row that backs it."""

from typing import Any, Optional


def find_artifact_model(model_repository: Any, artifact: Any) -> Optional[Any]:
    """The indexed model backing `artifact`, or None when it must be fetched.

    Identity first - the `(model_type, filename)` pair `models.index` keys
    on. Failing that, the artifact's sha256: the same bytes may already sit
    under another name, folder or type (a copy downloaded under the preset's
    older filename, or a misfiled download indexed as `unknown`). A hash
    match carrying a different `model_type` is adopted - retyped to the
    artifact's - so the preset's model field resolves to it in the smoke
    step instead of the recipe asking to download it again.
    """
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
    if by_hash.model_type != artifact.model_type:
        by_hash.model_type = artifact.model_type
        model_repository.update(by_hash)
    return by_hash
