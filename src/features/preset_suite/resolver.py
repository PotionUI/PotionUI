"""Resolve a test case's ``ModelRef`` (a sha256, optionally with an HF ref) to a
concrete file path on this host.

Resolution order (first hit wins):
  0. **live sha-index** — an in-memory ``sha256 -> file_path`` snapshot of the live
     models table, captured read-only before the suite switches to its ephemeral DB.
     This is what keeps the suite fast AND isolated: the run's own (empty) DB can't
     answer model lookups, and without this snapshot every resolution would fall to
     a hash-walk over a models tree that can be hundreds of GB (symlinked model
     stores). Empty when not provided.
  1. **models table** — the indexed models DB stores sha256 → file_path; a read-only
     lookup, no hashing. (In an ephemeral-DB run this misses; the index above covers it.)
  2. **hash-walk** — walk the configured models directory hashing files, with a
     cache keyed by ``(path, size, mtime)`` so a big tree is only fully hashed once
     per machine (``storage/model_hash_cache.json`` by default).
  3. **download** — only when ``allow_download`` and the ref carries an HF pointer:
     fetch it into the models dir (a ``model_type`` subdir when known, else a
     ``tests-downloads`` subdir) and index it.

A ref that can't be resolved without downloading (no permission, or no HF ref) is
reported as a SKIP with a clear reason — never a hard failure. Everything the
runner needs to keep going is on the returned :class:`ResolveResult`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_HASH_CHUNK = 1024 * 1024  # 1 MiB read blocks when hashing a file
_HF_BASE_URL = "https://huggingface.co"
_DOWNLOAD_POLL_SECONDS = 2.0
_DOWNLOAD_TIMEOUT_SECONDS = 3600


@dataclass
class ResolveResult:
    """Outcome of resolving one ``ModelRef``.

    ``file_path`` is the resolved absolute path, or ``None`` when the model
    couldn't be resolved (``reason`` then says why — a SKIP, not a failure).
    ``source`` names how it was found (``"db"`` / ``"hash-walk"`` / ``"download"``).
    """

    file_path: Optional[str]
    source: Optional[str] = None
    reason: Optional[str] = None

    @property
    def resolved(self) -> bool:
        return self.file_path is not None


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(_HASH_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


class ModelResolver:
    """Resolves ``ModelRef`` sha256s to file paths for the preset test suite.

    Dependencies are injected so the whole thing is unit-testable without a real
    models DB or a network: ``model_repository`` (anything with
    ``get_by_sha256(sha)`` returning an object with ``.file_path``, or ``None``),
    ``downloader`` (``(repo, file, dest_dir) -> path``), ``download_queue``
    (a ``src.features.downloads.DownloadQueue``, or anything exposing the same
    ``queue_model_download`` / ``get_download`` surface), and ``hasher``
    (``path -> hex sha256``).

    ``downloader`` always wins when given (mainly for tests - see
    ``tests/features/preset_suite/test_resolver.py``). Otherwise, when
    ``download_queue`` is given, a missing model is fetched through it
    (queue-then-poll, like ``src/features/setup/executors/artifacts_fetch.py``)
    so the fetch shows up in the admin download history and honors the
    configured depot the same way every other model fetch does. Only when
    neither is given does resolution fall back to a direct
    ``huggingface_hub.hf_hub_download`` call - a bypass, but a caller-opt-in one:
    nothing in this codebase constructs a `ModelResolver` without a
    ``download_queue`` for a real (non-dry-run) suite run; see
    ``scripts/preset_test_suite.py``.
    """

    def __init__(
        self,
        models_dir: str | os.PathLike,
        *,
        model_repository: Any = None,
        sha_index: Optional[dict] = None,
        cache_path: str | os.PathLike | None = None,
        allow_download: bool = False,
        downloader: Optional[Callable[[str, str, Path], str]] = None,
        download_queue: Any = None,
        hasher: Callable[[Path], str] = _sha256_file,
    ) -> None:
        self.models_dir = Path(models_dir)
        self.repo = model_repository
        # An in-memory ``sha256 -> file_path`` snapshot of the LIVE models table,
        # captured read-only before the suite re-points the DB at its ephemeral
        # copy. It is the fastest resolution path and — crucially — avoids hashing
        # a multi-hundred-GB models tree when the run's own (empty) DB can't answer
        # the lookup. See the class docstring's resolution order.
        self.sha_index = {str(k).strip().lower(): v for k, v in (sha_index or {}).items()}
        self.allow_download = allow_download
        self._downloader = downloader
        self.download_queue = download_queue
        self._hasher = hasher
        self.cache_path = Path(cache_path) if cache_path else Path("storage") / "model_hash_cache.json"
        self._cache: dict[str, dict] = self._load_cache()
        self._cache_dirty = False

    # -- public ------------------------------------------------------------

    def resolve(self, ref: Any, *, model_type: Optional[str] = None) -> ResolveResult:
        """Resolve one ``ModelRef`` to a file path, or a SKIP reason."""
        sha = (getattr(ref, "sha256", None) or "").strip().lower()
        if not sha:
            return ResolveResult(None, reason="ModelRef has no sha256")

        idx = self.sha_index.get(sha)
        if idx and Path(idx).exists():
            logger.info("resolved model sha256=%s… via live models index -> %s", sha[:12], idx)
            return ResolveResult(idx, source="db-index")

        hit = self._from_db(sha)
        if hit is not None:
            logger.info("resolved model sha256=%s… via models DB -> %s", sha[:12], hit)
            return ResolveResult(hit, source="db")

        logger.info(
            "model sha256=%s… not in models DB; falling back to hashing the models "
            "tree at %s (this can take minutes on first run)", sha[:12], self.models_dir,
        )
        hit = self._from_hash_walk(sha)
        if hit is not None:
            self._flush_cache()
            logger.info("resolved model sha256=%s… via hash-walk -> %s", sha[:12], hit)
            return ResolveResult(hit, source="hash-walk")
        self._flush_cache()

        hf = getattr(ref, "hf", None)
        if not self.allow_download:
            hint = f" (available on HF: {hf.get('repo')}/{hf.get('file')})" if hf else ""
            return ResolveResult(
                None,
                reason=f"model sha256={sha[:12]}… not found locally and --allow-download not set{hint}",
            )
        if not hf:
            return ResolveResult(
                None, reason=f"model sha256={sha[:12]}… not found locally and the case has no HF ref to download",
            )
        return self._download(ref, sha, hf, model_type)

    # -- (1) models DB -----------------------------------------------------

    def _from_db(self, sha: str) -> Optional[str]:
        if self.repo is None:
            return None
        try:
            model = self.repo.get_by_sha256(sha)
        except Exception as e:  # pragma: no cover - DB hiccup shouldn't abort resolution
            logger.debug("model DB lookup failed for %s: %s", sha[:12], e)
            return None
        if model is None:
            return None
        path = getattr(model, "file_path", None)
        if path and Path(path).exists():
            return str(path)
        return None

    # -- (2) hash-walk with mtime/size cache -------------------------------

    def _from_hash_walk(self, sha: str) -> Optional[str]:
        if not self.models_dir.is_dir():
            return None
        files = [p for p in sorted(self.models_dir.rglob("*")) if p.is_file()]
        total = len(files)
        # Log progress every ~25 files (and always the first) so a long walk on a
        # large models tree shows it is making progress rather than looking hung.
        for idx, path in enumerate(files):
            if idx == 0 or (idx + 1) % 25 == 0 or idx + 1 == total:
                logger.info("hash-walk %d/%d: %s", idx + 1, total, path.name)
            try:
                digest = self._cached_hash(path)
            except OSError as e:  # pragma: no cover - unreadable file, skip it
                logger.debug("could not hash %s: %s", path, e)
                continue
            if digest == sha:
                return str(path)
        return None

    def _cached_hash(self, path: Path) -> str:
        st = path.stat()
        key = str(path)
        entry = self._cache.get(key)
        if entry and entry.get("size") == st.st_size and entry.get("mtime") == st.st_mtime:
            return entry["sha256"]
        digest = self._hasher(path)
        self._cache[key] = {"size": st.st_size, "mtime": st.st_mtime, "sha256": digest}
        self._cache_dirty = True
        return digest

    def _load_cache(self) -> dict:
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _flush_cache(self) -> None:
        if not self._cache_dirty:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._cache, f)
            os.replace(tmp, self.cache_path)
            self._cache_dirty = False
        except OSError as e:  # pragma: no cover - cache is an optimisation, never fatal
            logger.debug("could not write hash cache %s: %s", self.cache_path, e)

    # -- (3) download ------------------------------------------------------

    def _download(self, ref: Any, sha: str, hf: dict, model_type: Optional[str]) -> ResolveResult:
        subdir = model_type if model_type else "tests-downloads"
        dest_dir = self.models_dir / subdir
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            if self._downloader is not None:
                downloader = self._downloader
            elif self.download_queue is not None:
                downloader = self._download_via_queue
            else:
                downloader = _hf_download
            path = downloader(hf["repo"], hf["file"], dest_dir)
        except Exception as e:  # noqa: BLE001 - a failed download is a SKIP, not a crash
            logger.warning("download of %s/%s failed: %s", hf.get("repo"), hf.get("file"), e)
            return ResolveResult(None, reason=f"download failed: {e}")
        # Verify the download matches the declared sha256 (a mismatch is a SKIP:
        # a wrong file would only produce misleading test results).
        try:
            got = self._hasher(Path(path))
        except OSError as e:  # pragma: no cover
            return ResolveResult(None, reason=f"downloaded file unreadable: {e}")
        if got != sha:
            return ResolveResult(
                None, reason=f"downloaded {hf['repo']}/{hf['file']} sha256={got[:12]}… != expected {sha[:12]}…",
            )
        return ResolveResult(str(path), source="download")

    def _download_via_queue(self, repo: str, file: str, dest_dir: Path) -> str:
        """Queue-then-poll a single HF file through the core download queue.

        Same shape as ``ArtifactsFetchExecutor`` (`src/features/setup/executors/
        artifacts_fetch.py`): `queue_model_download` is async, this call site is
        not, so the queueing call is bridged with `run_sync` and completion is
        polled off the plain-sync `get_download`. `destination_dir` is passed as
        the already-resolved, depot-contained `dest_dir` computed by `_download`
        above; `DownloadQueue.queue_model_download` re-validates it stays
        inside the configured depot regardless.
        """
        from src.features.setup.executors._async_bridge import run_sync

        url = f"{_HF_BASE_URL}/{repo}/resolve/main/{file}"
        download = run_sync(
            self.download_queue.queue_model_download(
                url=url,
                destination_dir=str(dest_dir),
                filename=file,
            )
        )

        deadline = time.monotonic() + _DOWNLOAD_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            current = self.download_queue.get_download(download.id)
            status = getattr(current.status, "value", current.status)
            if status == "completed":
                return current.destination_path
            if status in ("failed", "cancelled"):
                raise RuntimeError(f"{status}: {current.error_message or 'unknown error'}")
            time.sleep(_DOWNLOAD_POLL_SECONDS)
        raise RuntimeError(f"did not finish within {_DOWNLOAD_TIMEOUT_SECONDS}s")


def _hf_download(repo: str, file: str, dest_dir: Path) -> str:
    """Fallback HF downloader used only when a `ModelResolver` is given neither
    a `download_queue` nor an explicit `downloader` - bypasses the download
    queue's history/depot-containment, so callers should prefer wiring a
    `download_queue` (see `scripts/preset_test_suite.py`). Imported lazily so
    ``huggingface_hub`` is only a dependency when this path actually runs."""
    from huggingface_hub import hf_hub_download

    return hf_hub_download(repo_id=repo, filename=file, local_dir=str(dest_dir))
