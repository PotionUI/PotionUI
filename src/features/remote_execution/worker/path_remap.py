"""Remapping a dispatched pipeline's model file paths from the dispatching
host's verbatim absolute paths onto this worker's own model depot.

``build_model_bundle`` (``src.features.remote_execution.model_bundle_builder``)
walks a pipe's ``config`` for the ``{"file_path": ..., "name": ...}`` shape
every model-picker field writes to build the manifest a package carries; this
module walks the same shape again, in the worker process, to rewrite it - the
two walks run in different processes with no shared state to thread through,
so re-walking rather than reusing a precomputed map is the simplest thing
that is still correct.

A ``file_path`` is matched to its manifest entry by filename alone: the
worker has no PotionUI database, so it cannot repeat the model-identity
lookup (``model_type``/``sha256``) the dispatching side used to build
``logical_id`` in the first place. This is why a filename that collides
across two different manifest entries is treated as unresolvable rather than
guessed at.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List

from src.platform.worker_protocol import ModelBundleManifestV1, ProcessedPipelineV1


class ModelRemapError(Exception):
    def __init__(self, file_path: str, reason: str):
        self.file_path = file_path
        self.reason = reason
        super().__init__(f"{file_path}: {reason}")


def remap_model_paths(
    pipeline: ProcessedPipelineV1, model_bundle: ModelBundleManifestV1, depot_dir: Path
) -> ProcessedPipelineV1:
    """Return *pipeline* with every referenced model ``file_path`` rewritten to
    its depot location.

    Raises ``ModelRemapError`` naming the first file that has no manifest
    entry, matches more than one, or isn't actually staged - never runs a
    pipe against the dispatching host's original (unreachable) path.
    """
    by_filename: Dict[str, List] = {}
    for entry in model_bundle.entries:
        by_filename.setdefault(Path(entry.relative_path).name, []).append(entry)

    def resolve(file_path: str) -> str:
        matches = by_filename.get(Path(file_path).name, [])
        if not matches:
            raise ModelRemapError(file_path, "no matching entry in this execution's model bundle")
        if len(matches) > 1:
            raise ModelRemapError(
                file_path, "matches more than one model bundle entry by filename - ambiguous"
            )
        dest = depot_dir / matches[0].relative_path
        if not dest.is_file():
            raise ModelRemapError(file_path, f"not staged at {dest}")
        return str(dest)

    remapped_pipes = tuple(
        pipe.model_copy(update={"config": _remap_config(pipe.config, resolve)})
        for pipe in pipeline.pipes
    )
    return pipeline.model_copy(update={"pipes": remapped_pipes})


def _remap_config(value: Any, resolve: Callable[[str], str]) -> Any:
    if isinstance(value, dict):
        file_path = value.get("file_path")
        if isinstance(file_path, str) and file_path.strip():
            if _is_disabled(value):
                return dict(value)
            return {**value, "file_path": resolve(file_path)}
        return {key: _remap_config(item, resolve) for key, item in value.items()}
    if isinstance(value, list):
        return [_remap_config(item, resolve) for item in value]
    return value


def _is_disabled(entry: Dict[str, Any]) -> bool:
    if "weight" not in entry and "strength" not in entry:
        return False
    try:
        return float(entry.get("weight", entry.get("strength"))) == 0.0
    except (TypeError, ValueError):
        return False
