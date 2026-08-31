"""Turning a processed pipeline's model references into a real model bundle.

``RemoteNativeBackend`` dispatches a pipeline it built for THIS host's own
model depot - every ``{"file_path": ..., "name": ...}`` dict a model-picker
field wrote into a pipe's config is a path on this host's filesystem
(``package_assembly``'s "model paths stay verbatim" note). Before this
module, nothing turned that set of paths into the
:class:`~src.platform.worker_protocol.ModelBundleManifestV1` an execution
package carries - ``RemoteNativeBackend`` sent an always-empty one.

A model with no recorded digest gets hashed here, once, and the digest is
persisted so later dispatches skip straight to the recorded value. This
function does blocking file I/O - the caller runs it off the event loop.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional, Sequence, Tuple

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
_HASH_CHUNK_BYTES = 1024 * 1024


class ModelBundleResolutionError(RuntimeError):
    """A model a pipeline references cannot be turned into a bundle entry."""


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

    return build_bundle_manifest(entries.values())


def build_bundle_manifest(entries: Iterable[ModelBundleEntryV1]) -> ModelBundleManifestV1:
    """A deterministic manifest for an already-resolved set of entries -
    the tail half of `build_model_bundle`, exposed for callers (the admin
    model-push op) that resolve entries by model id rather than by walking a
    pipeline."""
    ordered = tuple(sorted(entries, key=lambda e: e.logical_id))
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
    return resolve_bundle_entry(model, repo)


def resolve_bundle_entry(model: Model, repo: ModelRepository) -> ModelBundleEntryV1:
    """The bundle entry for an already-resolved model row, hashing it on
    demand if it has never been hashed. Shared by pipeline bundling
    (`_entry_for`) and the admin model-push op, which resolves a model by id
    rather than by the file path a pipe config carries."""
    if model.is_directory:
        raise ModelBundleResolutionError(
            f"Model {model.filename!r} ({model.file_path}) is an HF-layout directory model - "
            "remote bundling of directory models is not implemented; it needs per-shard "
            "entries, not a single-file digest."
        )
    if not model.sha256 or model.file_size is None:
        model.sha256, model.file_size = _hash_and_persist(model, model.file_path, repo)

    role = model.model_type or "unknown"
    directory = MODEL_TYPE_TO_DIRECTORY.get(role, role)
    return ModelBundleEntryV1(
        logical_id=f"{role}/{model.filename}",
        role=role,
        relative_path=f"{directory}/{model.filename}",
        digest=ContentDigest(algorithm=_DIGEST_ALGORITHM, hex=model.sha256),
        size_bytes=model.file_size,
    )


def _hash_and_persist(model: Model, file_path: str, repo: ModelRepository) -> Tuple[str, int]:
    path = Path(file_path)
    if not path.is_file():
        raise ModelBundleResolutionError(
            f"Model {model.filename!r} ({file_path}) is missing on disk - cannot dispatch it "
            "to a remote worker."
        )
    sha256 = _hash_file(path)
    file_size = path.stat().st_size
    repo.update_digest(model.id, sha256=sha256, file_size=file_size)
    return sha256, file_size


def _hash_file(path: Path) -> str:
    hasher = hashlib.new(_DIGEST_ALGORITHM)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _bundle_digest(entries: Tuple[ModelBundleEntryV1, ...]) -> ContentDigest:
    canonical = json.dumps(
        [entry.model_dump(mode="json") for entry in entries],
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return ContentDigest(algorithm=_DIGEST_ALGORITHM, hex=hashlib.sha256(canonical).hexdigest())
