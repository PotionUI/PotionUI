"""Admin operations for syncing the host's model files onto a `native.remote`
worker's depot - Admin -> Backends -> <name> -> Models.

Model sync is admin configuration, never a silent side effect of a user's
generation: `RemoteNativeBackend._dispatch` only ever *checks* worker
inventory (`model_bundle_staging.find_unstaged_entries`) and fails fast when
something is missing; pushing or fetching bytes onto a worker's depot happens
only through the operations here, admin-triggered.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Optional, Set, Tuple

from src.features.backends.backend_config import NATIVE_REMOTE_DRIVER, NativeRemoteBackendConfig
from src.features.backends.backend_registry import BackendRegistry
from src.features.models.records import Model
from src.features.models.repository import ModelRepository
from src.features.providers.base_provider import ProviderCapability
from src.features.providers.registry import ProviderRegistry
from src.features.providers.remote_download import (
    RemoteDownloadResolutionError,
    resolve_model_remote_download,
)
from src.features.remote_execution.model_bundle_builder import (
    ModelBundleResolutionError,
    build_bundle_manifest,
    resolve_bundle_entry,
)
from src.features.remote_execution.transport import WorkerTransport, WorkerTransportError
from src.platform.filesystem.model_types import MODEL_TYPE_TO_DIRECTORY
from src.platform.worker_protocol import ModelBundleEntryV1
from src.platform.worker_protocol.model_fetch import ModelFetchRequestV1

#: Bound on how long push_models waits to see a just-started upload register
#: in the worker's own transfer list before reporting no transfer id for it -
#: the upload itself keeps running regardless of whether this finds it.
_TRANSFER_REGISTRATION_ATTEMPTS = 50
_TRANSFER_REGISTRATION_DELAY_SECONDS = 0.02


class RemoteModelsBackendError(Exception):
    """*backend_id* is not usable for a model-sync operation."""


def resolve_remote_backend_config(
    backend_registry: BackendRegistry, backend_id: str,
) -> NativeRemoteBackendConfig:
    config = backend_registry.backend_config_store.get_backend(backend_id)
    if config is None:
        raise RemoteModelsBackendError(f"No backend configured with id '{backend_id}'")
    if config.driver != NATIVE_REMOTE_DRIVER:
        raise RemoteModelsBackendError(f"Backend '{backend_id}' is not a native.remote backend")
    return config


def transport_for(config: NativeRemoteBackendConfig) -> WorkerTransport:
    return WorkerTransport(
        config.base_url, config.worker_token,
        connect_timeout=config.connect_timeout_seconds,
        request_timeout=config.request_timeout_seconds,
    )


def _relative_path(model: Model) -> str:
    role = model.model_type or "unknown"
    directory = MODEL_TYPE_TO_DIRECTORY.get(role, role)
    return f"{directory}/{model.filename}"


def _can_fetch(model: Model, provider_registry: ProviderRegistry) -> bool:
    """Cheap capability + link check only - never resolves a URL."""
    for link in model.providers:
        if not link.provider_model_id:
            continue
        provider = provider_registry.get_provider(link.provider)
        if provider and provider.get_metadata().has_capability(ProviderCapability.REMOTE_DOWNLOAD):
            return True
    return False


def _status(model: Model, worker_entry: Optional[dict]) -> str:
    if worker_entry is None:
        return "missing"
    if model.file_size is not None and worker_entry["size_bytes"] != model.file_size:
        return "digest_mismatch"
    if model.sha256 and worker_entry.get("digest") and worker_entry["digest"] != model.sha256:
        return "digest_mismatch"
    return "on_worker"


async def sync_view(
    model_repository: ModelRepository, provider_registry: ProviderRegistry, transport: WorkerTransport,
) -> List[dict]:
    """One row per host single-file model, joined against the worker's depot
    listing. Never hashes - `_status` only compares what's already recorded."""
    worker_entries = {e["relative_path"]: e for e in await transport.list_models()}

    rows = []
    for model in model_repository.get_all(limit=None, include_providers=True, include_tags=False):
        if model.is_directory or not model.file_path:
            continue
        worker_entry = worker_entries.get(_relative_path(model))
        rows.append({
            "model_id": model.id,
            "filename": model.filename,
            "model_type": model.model_type,
            "size_bytes": model.file_size,
            "status": _status(model, worker_entry),
            "providers_can_fetch": _can_fetch(model, provider_registry),
        })
    return rows


async def push_models(
    model_ids: List[str], *, model_repository: ModelRepository, transport: WorkerTransport,
) -> List[dict]:
    """Ensure a digest for each requested model (hashing on demand) and push
    it onto the worker's depot as a fire-and-forget upload - the worker's own
    transfer registry (`GET .../transfers`), not this call, is the source of
    truth for progress. The transfer_id returned per model is best-effort,
    captured by watching that registry for the upload's own registration
    right after it starts; `None` if it never showed up in time (the upload
    is still running regardless)."""
    results: List[dict] = []
    to_upload: List[Tuple[str, ModelBundleEntryV1, Path]] = []

    for model_id in model_ids:
        model = model_repository.get_by_id(model_id, include_providers=False)
        if model is None:
            results.append({"model_id": model_id, "transfer_id": None, "error": "model not found"})
            continue
        try:
            entry = await asyncio.to_thread(resolve_bundle_entry, model, model_repository)
        except ModelBundleResolutionError as exc:
            results.append({"model_id": model_id, "transfer_id": None, "error": str(exc)})
            continue
        to_upload.append((model_id, entry, Path(model.file_path)))

    if not to_upload:
        return results

    manifest = build_bundle_manifest(entry for _, entry, _ in to_upload)
    await transport.model_inventory(manifest)

    before_ids: Set[str] = {t["id"] for t in await transport.list_transfers()}
    for _, entry, source_path in to_upload:
        asyncio.create_task(transport.upload_model(manifest.bundle_id, entry, source_path))

    for model_id, entry, _source_path in to_upload:
        transfer_id = await _await_transfer_registration(transport, entry.relative_path, before_ids)
        results.append({"model_id": model_id, "transfer_id": transfer_id})
    return results


async def _await_transfer_registration(
    transport: WorkerTransport, relative_path: str, before_ids: Set[str],
) -> Optional[str]:
    for _ in range(_TRANSFER_REGISTRATION_ATTEMPTS):
        for transfer in await transport.list_transfers():
            if transfer["relative_path"] == relative_path and transfer["id"] not in before_ids:
                before_ids.add(transfer["id"])
                return transfer["id"]
        await asyncio.sleep(_TRANSFER_REGISTRATION_DELAY_SECONDS)
    return None


async def fetch_models(
    model_ids: List[str], *,
    model_repository: ModelRepository, provider_registry: ProviderRegistry, transport: WorkerTransport,
) -> List[dict]:
    """Resolve each model's linked provider to a credential-free URL and hand
    it to the worker to pull directly - per-model failures (unlinked model,
    a provider lacking REMOTE_DOWNLOAD, a failed resolution) are reported in
    the response rather than failing the whole batch."""
    results: List[dict] = []
    for model_id in model_ids:
        model = model_repository.get_by_id(model_id, include_providers=True)
        if model is None:
            results.append({"model_id": model_id, "transfer_id": None, "error": "model not found"})
            continue
        try:
            entry = await asyncio.to_thread(resolve_bundle_entry, model, model_repository)
        except ModelBundleResolutionError as exc:
            results.append({"model_id": model_id, "transfer_id": None, "error": str(exc)})
            continue
        try:
            ref = await resolve_model_remote_download(model_repository, provider_registry, model_id)
        except RemoteDownloadResolutionError as exc:
            results.append({"model_id": model_id, "transfer_id": None, "error": str(exc)})
            continue

        request = ModelFetchRequestV1(
            relative_path=entry.relative_path, expected_digest=entry.digest,
            expected_size=entry.size_bytes, url=ref.url, headers=ref.headers or None,
        )
        try:
            transfer_id = await transport.fetch_model(request)
        except WorkerTransportError as exc:
            results.append({"model_id": model_id, "transfer_id": None, "error": str(exc)})
            continue
        results.append({"model_id": model_id, "transfer_id": transfer_id})
    return results


async def list_transfers(transport: WorkerTransport) -> List[dict]:
    return await transport.list_transfers()
