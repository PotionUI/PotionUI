"""Turning a processed pipeline's model references into a real model bundle.

``RemoteNativeBackend`` dispatches a pipeline it built for THIS host's own
model depot - every ``{"file_path": ..., "name": ...}`` dict a model-picker
field wrote into a pipe's config is a path on this host's filesystem
(``package_assembly``'s "model paths stay verbatim" note). Before this
module, nothing turned that set of paths into the
:class:`~src.platform.worker_protocol.ModelBundleManifestV1` an execution
package carries - ``RemoteNativeBackend`` sent an always-empty one.

**Digests are never computed here.** The models feature already records a
content sha256 per indexed file (``models.sha256``, migration-backed) as
part of ordinary indexing - re-hashing a multi-gigabyte checkpoint on every
dispatch would make remote dispatch itself the slow path, and would compute a
digest this process cannot cross-check against anything. A model with no
recorded digest fails the dispatch with an actionable message rather than
silently hashing on the hot path or silently shipping an unverifiable bundle.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterator, Optional, Sequence, Tuple

from src.features.models.records import Model
from src.features.models.repository import ModelRepository
from src.features.models.repository import model_repo as _default_model_repo
from src.platform.filesystem.model_types import MODEL_TYPE_TO_DIRECTORY
from src.platform.worker_protocol import (
    ContentDigest,
    ModelBundleEntryV1,
    ModelBundleManifestV1,
    ProcessedPipeV1,
)

_DIGEST_ALGORITHM = "sha256"


class ModelBundleResolutionError(RuntimeError):
    """A model a pipeline references cannot be turned into a bundle entry.

    Always actionable: the fix is to (re)index the model's location so its
    digest (and, for a directory-layout model, real per-file digests) are on
    record - never to weaken this check.
    """


def build_model_bundle(
    processed_pipes: Sequence[ProcessedPipeV1],
    *,
    model_repository: Optional[ModelRepository] = None,
) -> ModelBundleManifestV1:
    """The bundle for every model file ``processed_pipes`` actually references.

    Walks each pipe's ``config`` (never ``inputs`` - that's provider wiring,
    not model selection) for the same ``{"file_path": ...}`` leaf shape every
    model-picker field writes, across every native model-loader family and
    the legacy checkpoint/controlnet loaders alike. A list entry that also
    carries a ``weight``/``strength`` key (the LoRA-stack shape) is skipped
    when that value is zero - mirroring ``loader_helpers.active_loras`` - so
    a LoRA slot a user picked and then disabled does not force a digest
    requirement on a file the pipeline will never actually load.

    Deduplicated by the resolved model's identity (``model_type/filename``),
    not by the raw path string, so the same model referenced from two pipes
    becomes one bundle entry. Deterministic: entries are sorted by
    ``logical_id`` before the bundle digest is computed, so the same
    pipeline always produces the same manifest bytes.
    """
    repo = model_repository or _default_model_repo
    entries: Dict[str, ModelBundleEntryV1] = {}
    seen_paths: set = set()

    for pipe in processed_pipes:
        for file_path in _referenced_paths(pipe.config):
            if file_path in seen_paths:
                continue
            seen_paths.add(file_path)
            entry = _entry_for(file_path, repo)
            entries[entry.logical_id] = entry

    ordered = tuple(sorted(entries.values(), key=lambda e: e.logical_id))
    digest = _bundle_digest(ordered)
    return ModelBundleManifestV1(
        bundle_id=f"bundle-{digest.hex[:32]}",
        bundle_digest=digest,
        entries=ordered,
    )


def _referenced_paths(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        file_path = value.get("file_path")
        if isinstance(file_path, str) and file_path.strip():
            if not _is_disabled(value):
                yield file_path
            return
        for item in value.values():
            yield from _referenced_paths(item)
    elif isinstance(value, list):
        for item in value:
            yield from _referenced_paths(item)


def _is_disabled(entry: Dict[str, Any]) -> bool:
    if "weight" not in entry and "strength" not in entry:
        return False
    try:
        return float(entry.get("weight", entry.get("strength"))) == 0.0
    except (TypeError, ValueError):
        return False


def _entry_for(file_path: str, repo: ModelRepository) -> ModelBundleEntryV1:
    model: Optional[Model] = repo.get_by_file_path(file_path, include_providers=False)
    if model is None:
        raise ModelBundleResolutionError(
            f"Pipeline references model file {file_path!r}, which is not indexed. "
            "Re-index its location before dispatching to a remote worker."
        )
    if model.is_directory:
        raise ModelBundleResolutionError(
            f"Model {model.filename!r} ({file_path}) is an HF-layout directory model - "
            "remote bundling of directory models is not implemented; it needs per-shard "
            "entries, not a single-file digest."
        )
    if not model.sha256 or model.file_size is None:
        raise ModelBundleResolutionError(
            f"Model {model.filename!r} ({file_path}) has no recorded content digest. "
            "Re-index its location so a digest is on record - remote dispatch does not "
            "hash model files on the fly."
        )

    role = model.model_type or "unknown"
    directory = MODEL_TYPE_TO_DIRECTORY.get(role, role)
    return ModelBundleEntryV1(
        logical_id=f"{role}/{model.filename}",
        role=role,
        relative_path=f"{directory}/{model.filename}",
        digest=ContentDigest(algorithm=_DIGEST_ALGORITHM, hex=model.sha256),
        size_bytes=model.file_size,
    )


def _bundle_digest(entries: Tuple[ModelBundleEntryV1, ...]) -> ContentDigest:
    canonical = json.dumps(
        [entry.model_dump(mode="json") for entry in entries],
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return ContentDigest(algorithm=_DIGEST_ALGORITHM, hex=hashlib.sha256(canonical).hexdigest())
