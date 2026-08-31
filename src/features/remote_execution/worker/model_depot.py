"""The worker's persistent model depot: presence/digest checking and chunked
staging of a ``ModelBundleManifestV1``'s entries onto local disk.

Unlike ``AssetStager`` (one instance per execution, staged into a per-execution
directory), a staged model's bytes are depot-persistent and shared across
every execution that references it - addressed by ``entry.relative_path``
alone, never scoped by execution or bundle id. A registered manifest is kept
in memory only (mirrors ``WorkerCoordinator._packages``): it exists so a later
staging upload can look an entry back up by ``(bundle_id, logical_id)``
without the caller re-sending the entry's digest/size on every chunk.

**Digest sidecars.** Re-hashing a multi-gigabyte checkpoint on every inventory
call would make the inventory endpoint itself the slow path. A depot file's
digest is only ever computed once - at successful `stage()`, or the first time
`inventory()` finds a file with no trustworthy sidecar - and cached as a
`<file>.digest` JSON sidecar recording the digest and the file size it was
computed against. A later call trusts the sidecar as long as the file's
current size still matches what the sidecar was written for; a size change
(re-download, truncation, manual replacement) invalidates it and forces one
more real hash.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import httpx

from src.features.remote_execution.worker.transfers import Transfer, TransferRegistry
from src.platform.worker_protocol import ModelBundleEntryV1, ModelBundleManifestV1
from src.platform.worker_protocol.model_fetch import ModelFetchRequestV1
from src.platform.worker_protocol.model_inventory import (
    ModelInventoryEntryV1,
    ModelInventoryResponseV1,
)

_SIDECAR_SUFFIX = ".digest"
_HASH_CHUNK_BYTES = 1024 * 1024
_PART_SUFFIX = ".part"
_FETCH_CHUNK_BYTES = 8 * 1024 * 1024


class ModelStagingError(Exception):
    def __init__(self, logical_id: str, reason: str):
        self.logical_id = logical_id
        self.reason = reason
        super().__init__(f"model '{logical_id}': {reason}")


@dataclass
class ModelDepot:
    """One instance per worker process."""

    depot_dir: Path
    #: Overridable so a test can point a fetch at a fake upstream
    #: (httpx.MockTransport / httpx.ASGITransport) instead of a real socket -
    #: never set in production, where the default (a real connection) is
    #: wanted.
    http_transport: Optional[httpx.AsyncBaseTransport] = None
    transfers: TransferRegistry = field(default_factory=TransferRegistry)
    _manifests: Dict[str, ModelBundleManifestV1] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.depot_dir.mkdir(parents=True, exist_ok=True)

    def entry_for(self, bundle_id: str, logical_id: str) -> Optional[ModelBundleEntryV1]:
        manifest = self._manifests.get(bundle_id)
        if manifest is None:
            return None
        for entry in manifest.entries:
            if entry.logical_id == logical_id:
                return entry
        return None

    def inventory(self, manifest: ModelBundleManifestV1) -> ModelInventoryResponseV1:
        """Register *manifest* (so a later staging upload can find its
        entries by logical_id) and report each entry's depot status."""
        self._manifests[manifest.bundle_id] = manifest
        entries = tuple(
            ModelInventoryEntryV1(logical_id=entry.logical_id, status=self._status(entry))
            for entry in manifest.entries
        )
        return ModelInventoryResponseV1(bundle_id=manifest.bundle_id, entries=entries)

    def stage(self, entry: ModelBundleEntryV1, chunks: Iterable[bytes]) -> Path:
        """Write *chunks* to the depot under a temp name, verifying size and
        digest as they arrive, then atomically publish.

        Mirrors ``AssetStager.stage``: a re-upload of already-correct bytes is
        a safe no-op overwrite, since the destination is derived from
        ``entry.relative_path`` alone.
        """
        dest = self._destination(entry)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")

        hasher = hashlib.new(entry.digest.algorithm)
        size = 0
        try:
            with tmp.open("wb") as handle:
                for chunk in chunks:
                    size += len(chunk)
                    if size > entry.size_bytes:
                        raise ModelStagingError(
                            entry.logical_id,
                            f"size mismatch: expected {entry.size_bytes} bytes, got more",
                        )
                    hasher.update(chunk)
                    handle.write(chunk)

            if size != entry.size_bytes:
                raise ModelStagingError(
                    entry.logical_id, f"size mismatch: expected {entry.size_bytes}, got {size}"
                )

            digest = hasher.hexdigest()
            if digest != entry.digest.hex:
                raise ModelStagingError(
                    entry.logical_id,
                    f"digest mismatch: expected {entry.digest.hex}, got {digest}",
                )
        except ModelStagingError:
            tmp.unlink(missing_ok=True)
            raise

        tmp.replace(dest)
        self._write_sidecar(dest, digest)
        return dest

    def _destination(self, entry: ModelBundleEntryV1) -> Path:
        return self._destination_for(entry.relative_path, entry.logical_id)

    def _destination_for(self, relative_path: str, error_id: str) -> Path:
        # relative_path is already structurally validated (no `..`, not
        # absolute - see validate_contained_relative_path); this is a second,
        # resolved-path containment check, the same defense-in-depth pattern
        # the artifact-serving route uses.
        dest = (self.depot_dir / relative_path).resolve()
        root = self.depot_dir.resolve()
        if root != dest and root not in dest.parents:
            raise ModelStagingError(error_id, "relative_path escapes the model depot")
        return dest

    # -- depot listing ----------------------------------------------------

    def list_entries(self) -> List[dict]:
        """Every published file in the depot - never a ``.part`` (in-flight
        staging) or ``.digest`` sidecar. Digest is reported only when a
        trusted sidecar already exists; this never hashes a file just to list
        it, or the listing endpoint becomes the slow path on a multi-terabyte
        depot."""
        root = self.depot_dir.resolve()
        entries: List[dict] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.name.endswith(_PART_SUFFIX) or path.name.endswith(_SIDECAR_SUFFIX):
                continue
            entries.append({
                "relative_path": path.relative_to(root).as_posix(),
                "size_bytes": path.stat().st_size,
                "digest": self._sidecar_digest(path),
            })
        return entries

    # -- fetch (core -> worker URL pull) -----------------------------------

    def start_fetch(self, request: ModelFetchRequestV1) -> Transfer:
        """Validate destination containment and register the transfer.
        Raises ``ModelStagingError`` before anything is registered so a
        rejected fetch never appears in the transfer list."""
        self._destination_for(request.relative_path, request.relative_path)
        return self.transfers.start("fetch", request.relative_path, request.expected_size)

    async def run_fetch(self, transfer_id: str, request: ModelFetchRequestV1) -> None:
        """Stream *request.url* into the depot, verifying size and digest as
        bytes arrive, then atomically publish - the fetch counterpart to
        ``stage()``. Always terminates the transfer (completed or failed),
        never leaves it ``running``."""
        dest = self._destination_for(request.relative_path, request.relative_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + _PART_SUFFIX)

        hasher = hashlib.new(request.expected_digest.algorithm)
        size = 0
        try:
            async with httpx.AsyncClient(transport=self.http_transport, timeout=None) as client:
                async with client.stream(
                    "GET", request.url, headers=dict(request.headers or {}), follow_redirects=True,
                ) as response:
                    if response.status_code != 200:
                        raise ModelStagingError(
                            request.relative_path, f"fetch failed: HTTP {response.status_code}",
                        )
                    with tmp.open("wb") as handle:
                        async for chunk in response.aiter_bytes(_FETCH_CHUNK_BYTES):
                            size += len(chunk)
                            if size > request.expected_size:
                                raise ModelStagingError(
                                    request.relative_path,
                                    f"size mismatch: expected {request.expected_size} bytes, got more",
                                )
                            hasher.update(chunk)
                            handle.write(chunk)
                            self.transfers.progress(transfer_id, size)

            if size != request.expected_size:
                raise ModelStagingError(
                    request.relative_path,
                    f"size mismatch: expected {request.expected_size}, got {size}",
                )

            digest = hasher.hexdigest()
            if digest != request.expected_digest.hex:
                raise ModelStagingError(
                    request.relative_path,
                    f"digest mismatch: expected {request.expected_digest.hex}, got {digest}",
                )
        except (ModelStagingError, httpx.HTTPError) as exc:
            tmp.unlink(missing_ok=True)
            reason = exc.reason if isinstance(exc, ModelStagingError) else str(exc)
            self.transfers.fail(transfer_id, reason)
            return

        tmp.replace(dest)
        self._write_sidecar(dest, digest)
        self.transfers.complete(transfer_id)

    def _status(self, entry: ModelBundleEntryV1) -> str:
        dest = self._destination(entry)
        if not dest.exists():
            return "missing"
        if dest.stat().st_size != entry.size_bytes:
            return "mismatched"

        trusted = self._sidecar_digest(dest)
        if trusted is not None and trusted == entry.digest.hex:
            return "present"

        digest = _hash_file(dest)
        if digest != entry.digest.hex:
            return "mismatched"
        self._write_sidecar(dest, digest)
        return "present"

    @staticmethod
    def _sidecar_path(dest: Path) -> Path:
        return dest.with_name(dest.name + _SIDECAR_SUFFIX)

    def _sidecar_digest(self, dest: Path) -> Optional[str]:
        sidecar = self._sidecar_path(dest)
        if not sidecar.exists():
            return None
        try:
            data = json.loads(sidecar.read_text())
        except (ValueError, OSError):
            return None
        if not isinstance(data, dict) or data.get("size") != dest.stat().st_size:
            return None
        digest = data.get("digest")
        return digest if isinstance(digest, str) else None

    def _write_sidecar(self, dest: Path, digest: str) -> None:
        self._sidecar_path(dest).write_text(
            json.dumps({"digest": digest, "size": dest.stat().st_size})
        )


def _hash_file(path: Path, *, algorithm: str = "sha256", chunk_size: int = _HASH_CHUNK_BYTES) -> str:
    hasher = hashlib.new(algorithm)
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()
