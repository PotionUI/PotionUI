"""Ephemeral, per-run isolation for the preset test suite.

A real suite run must NOT touch the user's live database or image storage. Each
run gets its own throwaway sqlite DB and file-storage dir inside the run
directory (``test-runs/<timestamp>/``), migrated fresh and torn down afterwards.

This module owns the DELETION-SAFETY half of that contract. The user was explicit:
removal may only ever delete what the suite itself created. So the client drops a
:data:`MARKER_NAME` file into every directory it creates, and :func:`cleanup`
refuses to delete any path that isn't inside a marked run directory, is a symlink,
or escapes the run root. It never touches ``storage/`` at the repo root.

The DB-path and settings wiring lives in ``runner.HeadlessGenerationClient`` (it
needs the app modules); this module is pure filesystem bookkeeping so it stays
trivially unit-testable.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Iterable, List

logger = logging.getLogger(__name__)

#: Sentinel dropped into every directory the suite creates. Its presence is the
#: ONLY thing that authorises :func:`cleanup` to remove a path.
MARKER_NAME = ".preset-suite-ephemeral"


def mark(directory: Path) -> Path:
    """Create ``directory`` (parents included) and drop the marker file in it.
    Returns the directory. Idempotent."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / MARKER_NAME).write_text(
        "Created by the preset test suite. Safe to delete this whole directory.\n",
        encoding="utf-8",
    )
    return directory


def is_marked(directory: Path) -> bool:
    """Whether ``directory`` is a real (non-symlink) dir carrying the marker."""
    directory = Path(directory)
    return (
        directory.is_dir()
        and not directory.is_symlink()
        and (directory / MARKER_NAME).is_file()
    )


def cleanup(run_dir: Path, ephemeral_paths: Iterable[Path], *, keep: bool, failed: bool) -> List[Path]:
    """Remove the suite-created ephemeral paths under ``run_dir``.

    Safety rules (all must hold for a path to be deleted):
      * ``run_dir`` itself must carry :data:`MARKER_NAME` — otherwise nothing is
        touched at all (guards against a mis-pointed run_dir);
      * each path must resolve to somewhere INSIDE ``run_dir`` (no escaping);
      * a symlink is never followed or deleted.

    ``keep`` (``--keep-db``) or ``failed`` (any case failed/errored) retains
    everything for debugging. Returns the list of paths actually removed.
    """
    run_dir = Path(run_dir)
    if keep or failed:
        logger.info(
            "preset-suite: retaining ephemeral run dir %s (%s)",
            run_dir, "keep-db requested" if keep else "a case failed — kept for debugging",
        )
        return []

    if not is_marked(run_dir):
        # Never delete a directory we didn't demonstrably create.
        logger.warning(
            "preset-suite: refusing to clean up %s — no %s marker (not suite-created)",
            run_dir, MARKER_NAME,
        )
        return []

    run_root = run_dir.resolve()
    removed: List[Path] = []
    for path in ephemeral_paths:
        path = Path(path)
        if not path.exists() and not path.is_symlink():
            continue
        if path.is_symlink():
            logger.warning("preset-suite: refusing to delete symlink %s", path)
            continue
        try:
            resolved = path.resolve()
            resolved.relative_to(run_root)  # raises if outside run_dir
        except ValueError:
            logger.warning("preset-suite: refusing to delete %s (outside run dir %s)", path, run_dir)
            continue
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed.append(path)
        except OSError as e:  # pragma: no cover - best-effort cleanup
            logger.warning("preset-suite: could not remove %s: %s", path, e)
    logger.info("preset-suite: cleaned up %d ephemeral path(s) under %s", len(removed), run_dir)
    return removed
