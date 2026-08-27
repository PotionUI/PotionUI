"""The `AssetFetcher` port and its error type."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Protocol, Sequence, runtime_checkable


def asset_subdir(category: str, repo_id: str) -> str:
    """The depot-relative directory a repo's mirror belongs in.

    `<category>/<slug>`, slugified the same way the tagger and embedder
    destinations are (`src/features/media_index/tagger.py`), so every caller
    that needs to name the same asset - a loader, a presence check, an admin
    status endpoint - derives one path rather than three.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", repo_id.strip().lower()).strip("-")
    return f"{category}/{slug}"


class AssetFetchError(RuntimeError):
    """An asset could not be made available locally.

    Declared here rather than in the downloads feature so a pipe can catch a
    failed fetch: `src/pipelines/` cannot import `src.features.downloads`'s
    exception module, so an implementation's own exception types are
    untouchable from a pipe. Implementations translate their internal failures
    into this.
    """


@runtime_checkable
class AssetFetcher(Protocol):
    """Makes a remote model asset available on local disk, synchronously.

    Implemented by `src.features.downloads.DownloadQueue`, which a pipe
    receives as the injected `ASSETS` built-in service (see
    `GenerationEngine._inject_built_in_services`). Both methods are:

    - **synchronous and blocking** - they return only once the asset is on
      disk. Callers are pipes: ordinary synchronous code, often running under
      `asyncio.to_thread`, sometimes on a thread with no event loop at all.
    - **idempotent** - an asset already present is returned without touching
      the network, so a hot path may call them unconditionally.
    - **depot-relative** - `subdir` names a directory *inside* the configured
      model depot. It is never a path in its own right: an implementation must
      resolve it against the depot root and reject any escape (traversal,
      absolute path, or symlink hop), so a pipe cannot write outside the
      depot even by accident.

    Every fetch is expected to land in the same download history, with the
    same progress reporting and concurrency control, as a fetch queued from
    the admin UI.
    """

    def ensure_asset_file(
        self,
        url: str,
        *,
        subdir: str,
        filename: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Path:
        """Ensure the single file at `url` exists in the depot, and return it.

        Args:
            url: Direct download URL.
            subdir: Depot-relative destination directory.
            filename: Name to save as; derived from `url` when omitted.
            timeout: Overall bound in seconds on waiting for the fetch.

        Returns:
            The absolute path of the local file.

        Raises:
            AssetFetchError: The fetch could not be started or did not finish.
        """
        ...

    def ensure_asset_repo(
        self,
        repo_id: str,
        *,
        subdir: str,
        files: Optional[Sequence[str]] = None,
        revision: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Path:
        """Ensure a Hugging Face repo is mirrored into the depot, and return its dir.

        The path to hand a library's `from_pretrained` instead of a repo id -
        the library then loads from disk and never fetches anything itself.

        Args:
            repo_id: e.g. "org/model-name".
            subdir: Depot-relative destination directory.
            files: Repo-relative filenames this caller needs. They double as
                the download filter (nothing else is fetched) and as the
                presence test (the asset counts as present only once all of
                them exist). Omit to mirror the whole repo, in which case
                presence degrades to "the directory exists and is not empty".
            revision: Optional git revision to pin.
            timeout: Overall bound in seconds on waiting for the fetch.

        Returns:
            The absolute path of the local directory holding the repo files.

        Raises:
            AssetFetchError: The fetch could not be started or did not finish.
        """
        ...
