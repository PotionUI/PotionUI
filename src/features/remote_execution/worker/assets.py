"""Input-asset staging: verifying and storing an uploaded input asset before
an execution that references it may run.

Staged against ``ExecutionPackageV1.input_assets``
(``src.platform.worker_protocol.input_asset.InputAssetManifestV1``/
``InputAssetV1``): a package without a manifest has nothing to stage, and
``WorkerCoordinator._wait_for_assets`` treats an empty expected set as
already satisfied.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Optional, Set

from src.platform.worker_protocol import ExecutionPackageV1, InputAssetManifestV1, InputAssetV1


class AssetStagingError(Exception):
    def __init__(self, logical_id: str, reason: str):
        self.logical_id = logical_id
        self.reason = reason
        super().__init__(f"asset '{logical_id}': {reason}")


@dataclass
class AssetStager:
    """One instance per execution. ``execution_dir`` is scoped per-execution so
    two executions can never collide on a logical id."""

    execution_dir: Path
    _staged: Dict[str, Path] = field(default_factory=dict)

    def manifest_for(self, package: ExecutionPackageV1) -> Optional[InputAssetManifestV1]:
        return package.input_assets

    def expected_logical_ids(self, package: ExecutionPackageV1) -> Set[str]:
        manifest = self.manifest_for(package)
        if manifest is None:
            return set()
        return {entry.logical_id for entry in manifest.assets}

    def entry_for(self, package: ExecutionPackageV1, logical_id: str) -> Optional[InputAssetV1]:
        manifest = self.manifest_for(package)
        if manifest is None:
            return None
        return manifest.asset(logical_id)

    def stage(self, entry: InputAssetV1, chunks: Iterable[bytes]) -> Path:
        """Write ``chunks`` to ``entry.relative_path`` under a temp name,
        verifying size and digest as they arrive, then atomically publish.

        A re-upload of an already-staged asset is safe to call again: the
        destination is content-addressed by ``entry``, so identical bytes
        overwrite themselves and mismatched bytes still fail the same check.
        """
        dest = self.execution_dir / entry.relative_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")

        hasher = hashlib.new(entry.digest.algorithm)
        size = 0
        try:
            with tmp.open("wb") as handle:
                for chunk in chunks:
                    size += len(chunk)
                    if size > entry.size_bytes:
                        raise AssetStagingError(
                            entry.logical_id,
                            f"size mismatch: expected {entry.size_bytes} bytes, got more",
                        )
                    hasher.update(chunk)
                    handle.write(chunk)

            if size != entry.size_bytes:
                raise AssetStagingError(
                    entry.logical_id, f"size mismatch: expected {entry.size_bytes}, got {size}"
                )

            digest = hasher.hexdigest()
            if digest != entry.digest.hex:
                raise AssetStagingError(
                    entry.logical_id,
                    f"digest mismatch: expected {entry.digest.hex}, got {digest}",
                )
        except AssetStagingError:
            tmp.unlink(missing_ok=True)
            raise

        tmp.replace(dest)
        self._staged[entry.logical_id] = dest
        return dest

    def all_staged(self, package: ExecutionPackageV1) -> bool:
        return self.expected_logical_ids(package).issubset(self._staged.keys())

    def resolve(self, logical_id: str) -> Path:
        path = self._staged.get(logical_id)
        if path is None:
            raise KeyError(logical_id)
        return path
