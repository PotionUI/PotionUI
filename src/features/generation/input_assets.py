"""Rewriting real storage paths in processed pipe configs into asset tokens.

A processed pipeline's ``config``/``inputs`` JSON can carry a user-media path
minted by the local host - an absolute path, or one relative to the user's
storage root (see ``src/pipelines/pipes/media_loader/main.py``'s two
conventions and ``src/features/forms/binding.py``'s containment check, which
is validate-only and leaves the submitted value exactly as given). A remote
worker has no access to that filesystem, so before a package that carries
such a path leaves the host, :func:`collect_input_assets` walks every pipe's
config and inputs, finds every string that actually resolves to a real file
under the storage root, and replaces it with an ``asset://<logical_id>``
token - the same token for the same file no matter how many times or where
it appears, keyed by the file's content digest.

Deliberately conservative: a string only becomes a token when it resolves to
an existing file under ``storage_dir``. Nothing here guesses that some other
string "looks like a path" - that would risk rewriting a value a pipe
actually depends on reading verbatim (a model repo id, a prompt, ...).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.platform.util.path_resolution import resolve_within
from src.platform.worker_protocol import (
    ContentDigest,
    InputAssetManifestV1,
    InputAssetV1,
    ProcessedPipeV1,
)

#: Sibling provenance keys (`<field>__origin`) reference host-DB rows and
#: never travel to a worker - stripped wherever the walk finds them, at any
#: depth.
_ORIGIN_SUFFIX = "__origin"

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_MAX_SLUG_LENGTH = 64
_READ_CHUNK_BYTES = 1 << 20


def collect_input_assets(
    processed_pipes: Sequence[ProcessedPipeV1],
    storage_dir: Path,
) -> Tuple[Tuple[ProcessedPipeV1, ...], Optional[InputAssetManifestV1], Dict[str, Path]]:
    """Rewrite every real storage path in ``processed_pipes`` into a token.

    Returns the rewritten pipes (in the same order), the manifest of every
    distinct file that was tokenized (``None`` when none was found), and a
    ``logical_id -> resolved source path`` map for every entry in that
    manifest (empty when the manifest is ``None``) - the transport layer
    reads this to know which local file to upload for each logical id the
    worker's staging manifest names. Deterministic: the same input always
    produces the same manifest bytes, which is required since this runs
    before the package's request digest is computed.
    """
    storage_root = storage_dir.resolve()
    collector = _Collector(storage_root)

    rewritten = tuple(
        ProcessedPipeV1(
            pipe_id=pipe.pipe_id,
            pipe_type=pipe.pipe_type,
            enabled=pipe.enabled,
            config=collector.rewrite(pipe.config),
            inputs=collector.rewrite(pipe.inputs),
        )
        for pipe in processed_pipes
    )

    if not collector.assets:
        return rewritten, None, {}

    manifest = InputAssetManifestV1(
        assets=tuple(sorted(collector.assets.values(), key=lambda a: a.logical_id))
    )
    return rewritten, manifest, collector.sources


class _Collector:
    def __init__(self, storage_root: Path):
        self._storage_root = storage_root
        self.assets: Dict[str, InputAssetV1] = {}
        self.sources: Dict[str, Path] = {}

    def rewrite(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._rewrite_string(value)
        if isinstance(value, dict):
            return {
                key: self.rewrite(item)
                for key, item in value.items()
                if not key.endswith(_ORIGIN_SUFFIX)
            }
        if isinstance(value, list):
            return [self.rewrite(item) for item in value]
        return value

    def _rewrite_string(self, value: str) -> str:
        resolved = self._resolve_under_storage(value)
        if resolved is None:
            return value
        return f"asset://{self._logical_id_for(resolved)}"

    def _resolve_under_storage(self, value: str) -> Optional[Path]:
        if not value:
            return None
        try:
            resolved = resolve_within(self._storage_root, value)
        except OSError:
            return None
        if resolved is None or not resolved.is_file():
            return None
        return resolved

    def _logical_id_for(self, resolved: Path) -> str:
        digest_hex, size_bytes = _digest_file(resolved)
        logical_id = f"{digest_hex[:16]}-{_slugify(resolved.name)}"

        existing = self.assets.get(logical_id)
        if existing is not None:
            return logical_id

        self.assets[logical_id] = InputAssetV1(
            logical_id=logical_id,
            media_type=None,
            relative_path=f"inputs/{logical_id}/{resolved.name}",
            digest=ContentDigest(algorithm="sha256", hex=digest_hex),
            size_bytes=size_bytes,
        )
        self.sources[logical_id] = resolved
        return logical_id


def _digest_file(path: Path) -> Tuple[str, int]:
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_READ_CHUNK_BYTES), b""):
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size


def _slugify(name: str) -> str:
    base = Path(name).stem.lower()
    slug = _SLUG_RE.sub("-", base).strip("-")
    return (slug or "asset")[:_MAX_SLUG_LENGTH]
