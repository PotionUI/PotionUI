"""
Enumerate the models the native engine can load: a walk of this host's models directory.

The `ref` a native pipe expects is the path it will open, so `ref` is the file path exactly
as stored — `models/loras/x.safetensors`. That keeps the native picker value the same as
the preset-declared reference, so no preset-side translation is needed on the native side.

Every file gets a digest, but hashing a multi-GB checkpoint on every index would make
the action unusable, so this goes through `model_hash_cache` first: a hit keyed on
(path, size, mtime_ns) means the file hasn't moved since it was last hashed - by this
scan, or by the depot-wide model indexer - and the cached digest is reused without
touching the file. Only a cache miss (new file, or one whose size/mtime changed since
it was last seen) actually reads bytes. See migration 110_model_availability_digest.py
and `src.features.models.hash_cache_repository`.
"""

import hashlib
from datetime import timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.features.backends.model_listing import BackendModel
from src.features.models.hash_cache_repository import model_hash_cache_repo
from src.platform.filesystem.model_types import DIRECTORY_TO_MODEL_TYPE, SUPPORTED_MODEL_EXTENSIONS
from src.platform.observability.logger import logger


# Amortizes the read() call overhead on multi-GB checkpoints without holding much
# extra memory - same idea as ModelScanner.calculate_sha256's chunked read, just a
# larger chunk since this always reads whole-file (no interleaving with other I/O).
_HASH_CHUNK_SIZE = 1024 * 1024


SUPPORTED_EXTENSIONS = SUPPORTED_MODEL_EXTENSIONS

# First-level directory under models_dir -> PotionUI model type. `llm` is
# excluded: `_walk` below is a flat file-by-file walk with no directory-per-
# model handling (unlike `ModelScanner.DIRECTORY_MODEL_TYPES`), so mapping it
# would report every shard inside an LLM's directory as its own model.
DIRECTORY_TYPE_MAPPING = {
    directory: model_type
    for directory, model_type in DIRECTORY_TO_MODEL_TYPE.items()
    if model_type != 'llm'
}


def _indexed_at_epoch(indexed_at) -> Optional[float]:
    """`models.indexed_at` as an epoch second, comparable with `st_mtime`.

    Two conversions the naive datetime does not carry on its own. The column is
    written by SQLite's CURRENT_TIMESTAMP, so it is UTC: reading it as local time
    would move it by the host's UTC offset, and west of UTC that shift is
    *forward*, which would make a file modified after indexing look untouched.
    CURRENT_TIMESTAMP also truncates to the second, so the second it dropped is
    added back - without it, a file written and indexed within the same second
    reads as "modified after indexing" and is rehashed on every scan forever.
    """
    if not indexed_at:
        return None
    if indexed_at.tzinfo is None:
        indexed_at = indexed_at.replace(tzinfo=timezone.utc)
    return indexed_at.timestamp() + 1.0


def _known_hashes() -> Dict[str, Tuple[str, Optional[int], Optional[float]]]:
    """file_path -> (sha256, file_size, indexed_at epoch) for models already indexed here.

    `file_size` and `indexed_at` ride along so `_digest_for` can decide whether that
    digest still describes the bytes currently at that path: a different size, or a
    file modified after the row was written, means it does not.
    """
    try:
        from src.features.models.repository import model_repo

        known = {}
        for m in model_repo.get_all(include_providers=False, include_tags=False):
            if not m.file_path or not m.sha256 or getattr(m, "is_directory", False):
                continue
            known[m.file_path] = (
                m.sha256,
                m.file_size,
                _indexed_at_epoch(getattr(m, "indexed_at", None)),
            )
        return known
    except Exception as e:
        logger.debug(f"[NATIVE_SCAN] Could not read known hashes: {e}")
        return {}


def _hash_file(path: Path, chunk_size: int = _HASH_CHUNK_SIZE) -> Optional[str]:
    try:
        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(chunk_size), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except OSError as e:
        logger.warning(f"[NATIVE_SCAN] Cannot hash {path}: {e}")
        return None


def _digest_for(
    path: Path, size: int, known: Dict[str, Tuple[str, Optional[int], Optional[float]]]
) -> Optional[str]:
    """The content digest for `path`, hashing only when nothing cheaper proves it.

    Three tiers, cheapest first:
    1. `model_hash_cache` hit at the file's *current* (size, mtime_ns) - the file
       hasn't moved since it (or an equivalent depot-indexer pass) was last hashed.
    2. A `models` row that hashed this exact path, at this size, before the file was
       last written - a pre-cache row for a file nobody has touched since. Trusted
       once and seeded into the cache, so later scans go through tier 1.
    3. Neither: hash the file now and cache the result.

    Tier 2 is deliberately unreachable once the path has ANY cache row. A cache row
    that failed tier 1 is positive evidence that the bytes moved since something
    hashed them, and `models.sha256` is not refreshed by this scan - trusting it
    there would answer a same-size in-place swap with the pre-swap digest and then
    write that digest back under the new mtime, so every later scan hit tier 1 and
    reported the stale digest forever. Both `size` and `indexed_at` are checked for
    the same reason: neither alone rules out a replacement.
    """
    ref = str(path)
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError as e:
        logger.warning(f"[NATIVE_SCAN] Cannot stat {path} for hashing: {e}")
        return None

    cached = model_hash_cache_repo.get(ref)
    if cached and cached.size == size and cached.mtime_ns == mtime_ns:
        return cached.sha256

    if cached is None:
        known_digest, known_size, known_indexed_at = known.get(ref, (None, None, None))
        unmodified_since_indexing = (
            known_indexed_at is not None and mtime_ns / 1_000_000_000 <= known_indexed_at
        )
        if known_digest and known_size == size and unmodified_since_indexing:
            model_hash_cache_repo.put(ref, size, mtime_ns, known_digest)
            return known_digest

    digest = _hash_file(path)
    if digest:
        model_hash_cache_repo.put(ref, size, mtime_ns, digest)
    return digest


def _walk(directory: Path, visited: set) -> List[Path]:
    """Depth-first walk that follows symlinked subdirectories exactly once.

    `Path.rglob` deliberately does not descend into symlinked directories, and model
    libraries are routinely assembled out of symlinks (`models/loras -> /srv/weights/loras`).
    `visited` holds resolved directories so a symlink loop terminates.
    """
    try:
        real = directory.resolve()
    except OSError as e:
        logger.warning(f"[NATIVE_SCAN] Cannot resolve {directory}: {e}")
        return []

    if real in visited:
        return []
    visited.add(real)

    files: List[Path] = []
    try:
        entries = sorted(directory.iterdir())
    except OSError as e:
        logger.warning(f"[NATIVE_SCAN] Cannot read {directory}: {e}")
        return []

    for entry in entries:
        if entry.is_dir():
            files.extend(_walk(entry, visited))
        elif entry.is_file() and entry.suffix.lower() in SUPPORTED_EXTENSIONS:
            files.append(entry)
    return files


def scan_native_models(models_dir: str) -> List[BackendModel]:
    """Walk `models_dir` and report every model file the native engine could load."""
    root = Path(models_dir)
    if not root.exists():
        logger.warning(f"[NATIVE_SCAN] Models directory does not exist: {root}")
        return []

    hashes = _known_hashes()
    found: List[BackendModel] = []

    for type_dir in sorted(root.iterdir()):
        if not type_dir.is_dir():
            continue

        model_type = DIRECTORY_TYPE_MAPPING.get(type_dir.name)
        if model_type is None:
            logger.debug(f"[NATIVE_SCAN] Skipping unmapped directory '{type_dir.name}'")
            continue

        for path in _walk(type_dir, visited=set()):
            try:
                size = path.stat().st_size
            except OSError as e:
                logger.warning(f"[NATIVE_SCAN] Cannot stat {path}: {e}")
                continue

            found.append(BackendModel(
                model_type=model_type,
                filename=path.name,
                ref=str(path),
                size=size,
                sha256=_digest_for(path, size, hashes),
            ))

    logger.info(f"[NATIVE_SCAN] Found {len(found)} model files under {root}")
    return found
