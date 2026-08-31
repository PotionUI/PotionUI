"""Pushing a dispatched pipeline's model bundle onto a worker's depot before
submit.

``build_model_bundle`` (``model_bundle_builder.py``) turns a processed
pipeline's model references into a ``ModelBundleManifestV1``; this module is
what actually gets those bytes onto the worker, so the worker's own
``model_depot`` (staged once, shared across every execution that references
the same file) has something to remap paths against before the pipeline runs.

**Resume is inventory-driven, not tracked here.** A worker's
``ModelDepot.inventory()`` answer already reflects everything it has staged,
whether that came from an earlier dispatch of this same pipeline or an
unrelated one that happened to reference the same file - re-dispatching after
a partial upload simply asks again and only pushes what inventory still
reports missing/mismatched. Nothing here remembers what a previous attempt
already sent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from src.features.models.repository import ModelRepository
from src.features.models.repository import model_repo as _default_model_repo
from src.features.remote_execution.transport import WorkerTransport
from src.pipelines.outputs import GenerationOutput, Progress, ProgressGenerationOutput
from src.platform.worker_protocol import ModelBundleEntryV1, ModelBundleManifestV1

_BYTES_PER_GB = 1_000_000_000


class ModelStagingSourceError(RuntimeError):
    """A bundle entry's local source file can no longer be resolved.

    The bundle was built from the models index moments earlier; this only
    fires if the index changed out from under the dispatch (the model was
    deleted or re-typed) between building the bundle and pushing it.
    """


async def stage_model_bundle(
    model_bundle: ModelBundleManifestV1,
    transport: WorkerTransport,
    emit: Callable[[Optional[GenerationOutput]], None],
    *,
    model_repository: Optional[ModelRepository] = None,
) -> None:
    """Ensure every entry in *model_bundle* is present on the worker's depot.

    Queries the worker's inventory first and uploads only what it reports
    missing or mismatched - an entry it already has (from this or an earlier
    dispatch) is never re-sent. Emits ``ProgressGenerationOutput`` events as
    bytes are pushed so a caller wired into the generation-status pipeline
    shows staging progress instead of appearing hung. A no-op, including no
    inventory call, when the bundle carries no entries.
    """
    if not model_bundle.entries:
        return

    repo = model_repository or _default_model_repo
    inventory = await transport.model_inventory(model_bundle)
    status_by_id = {entry.logical_id: entry.status for entry in inventory.entries}
    to_push = [entry for entry in model_bundle.entries if status_by_id.get(entry.logical_id) != "present"]
    if not to_push:
        return

    total_bytes = sum(entry.size_bytes for entry in to_push)
    pushed_bytes = 0
    _emit_staging_progress(emit, pushed_bytes, total_bytes)

    for entry in to_push:
        source_path = _source_path(entry, repo)
        await transport.upload_model(model_bundle.bundle_id, entry, source_path)
        pushed_bytes += entry.size_bytes
        _emit_staging_progress(emit, pushed_bytes, total_bytes)


def _emit_staging_progress(
    emit: Callable[[Optional[GenerationOutput]], None], pushed_bytes: int, total_bytes: int,
) -> None:
    percent = int(round((pushed_bytes / total_bytes) * 100)) if total_bytes else 100
    emit(ProgressGenerationOutput(
        state="staging_models",
        title=f"Staging models — {pushed_bytes / _BYTES_PER_GB:.1f} / {total_bytes / _BYTES_PER_GB:.1f} GB",
        progress=Progress(current=percent, max=100),
    ))


def _source_path(entry: ModelBundleEntryV1, repo: ModelRepository) -> Path:
    # Mirrors build_model_bundle's own identity: logical_id/relative_path are
    # both derived from (role, filename) - see model_bundle_builder.py's
    # `_entry_for` - so this is the inverse lookup of the same identity,
    # never a re-hash or a guess.
    model = repo.get_by_identity(entry.role, Path(entry.relative_path).name, include_providers=False)
    if model is None or not model.file_path:
        raise ModelStagingSourceError(
            f"model '{entry.logical_id}' was in the dispatch bundle but no longer resolves to a "
            "local file - it may have been removed or re-indexed since this generation started"
        )
    return Path(model.file_path)
