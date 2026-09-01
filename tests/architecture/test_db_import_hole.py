"""Permanent guard for the module-level `db` import hole.

Incident: a repository that does ``from src.platform.database.database import
db`` at module top level binds its own ``db`` name to whatever ``Database``
singleton exists at the moment that module is *first* imported - collection
alone is enough. `tests/conftest.py`'s ``mock_db`` fixture only patches two
names (``src.platform.database.database.db`` and
``src.platform.database.migration_runner.db``); any other module that bound
its own ``db`` this way keeps talking to whatever ``db`` was at that first
import - the live database, in a plain ``pytest tests/`` run - for the rest
of the process, invisible to `mock_db`.

The fix is to defer the import to call time, inside whichever function uses
``db`` - see any method in ``src/features/downloads/repository.py`` or
``src/platform/settings/repository.py`` for the pattern (module re-exporting
the name for callers to import elsewhere, like ``src/plugin_api/storage.py``,
use a module ``__getattr__`` instead, so the name is still resolved fresh on
each access rather than snapshotted at re-export time).

This check is intentionally narrow: it only recognizes imports of the
``database`` *submodule* (``src.platform.database.database``, absolute or
relative). ``from src.platform.database import db`` - the package-level
re-export in ``src/platform/database/__init__.py`` - resolves the exact same
frozen-at-import-time object and carries the identical risk, and is not yet
covered by any guard; a large number of existing repositories use that form
(see the follow-up noted where this test was introduced). Extending this
guard to that form is a separate, much larger change and is deliberately not
attempted here.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {"__pycache__", "node_modules", "venv", ".git"}

TARGET_MODULE = "src.platform.database.database"

_MIGRATIONS_RATIONALE = (
    "loaded fresh per run via importlib.util.spec_from_file_location "
    "(src/platform/database/migration_runner.py), never cached in "
    "sys.modules - a module-level import here always executes after any "
    "test's db patch is already in place, so it cannot freeze a stale "
    "reference."
)

# path (relative to repo root) -> why this module-level `db` import is safe.
ALLOWLIST: dict[str, str] = {
    "src/platform/database/migration_runner.py": (
        "one of the two names tests/conftest.py's mock_db fixture patches "
        "directly (`migration_runner.db`) - the canonical patched pair "
        "along with database.database.db itself; other modules should defer "
        "their own import to call time rather than ask to be added here."
    ),
    "src/platform/database/migrations/001_baseline.py": _MIGRATIONS_RATIONALE,
    "src/platform/database/migrations/002_rename_clip_to_text_encoder.py": _MIGRATIONS_RATIONALE,
    "src/platform/database/migrations/003_remove_quick_search_keybinding.py": _MIGRATIONS_RATIONALE,
    "src/platform/database/migrations/004_provisioned_compute.py": _MIGRATIONS_RATIONALE,
    "src/platform/database/migrations/005_download_destination_backend.py": _MIGRATIONS_RATIONALE,
}


def _py_files(base: Path):
    for f in base.rglob("*.py"):
        if any(part in SKIP_DIRS for part in f.parts):
            continue
        yield f


def _package_of(path: Path) -> str:
    """Dotted package a module file belongs to (its containing directory)."""
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts[:-1]) if rel.name != "__init__" else list(rel.parts)
    return ".".join(parts)


def _resolved_module(path: Path, node: ast.ImportFrom) -> str:
    """Absolute dotted module a (possibly relative) ImportFrom names."""
    if node.level == 0:
        return node.module or ""
    package_parts = _package_of(path).split(".") if _package_of(path) else []
    # level=1 means "this package"; each extra level climbs one more.
    base = package_parts[: len(package_parts) - (node.level - 1)] if node.level > 1 else package_parts
    if node.level > len(package_parts) + 1:
        base = []
    absolute = ".".join(base)
    if node.module:
        absolute = f"{absolute}.{node.module}" if absolute else node.module
    return absolute


def _module_level_db_imports(path: Path) -> list[int]:
    """Line numbers of top-level `from <database submodule> import db`
    (or `... as db`) statements - `tree.body` only, so anything nested inside
    a function or class (already call-time) is not a violation."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    hits = []
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        if _resolved_module(path, node) != TARGET_MODULE:
            continue
        for alias in node.names:
            bound_name = alias.asname or alias.name
            if bound_name == "db":
                hits.append(node.lineno)
    return hits


def test_no_module_level_db_import_outside_allowlist():
    violations: list[str] = []
    for f in _py_files(ROOT / "src"):
        rel = str(f.relative_to(ROOT))
        if rel in ALLOWLIST:
            continue
        for lineno in _module_level_db_imports(f):
            violations.append(f"{rel}:{lineno}")

    assert not violations, (
        "module-level `from ... database import db` freezes `db` at this "
        "module's first import, escaping tests/conftest.py's mock_db patch "
        "- defer the import to call time instead (see "
        "src/features/downloads/repository.py):\n" + "\n".join(violations)
    )


def test_allowlist_entries_still_exist_and_are_needed():
    for rel in ALLOWLIST:
        path = ROOT / rel
        assert path.is_file(), f"allowlisted {rel} no longer exists - remove its entry"
        assert _module_level_db_imports(path), (
            f"{rel} no longer has a module-level db import - remove its allowlist entry"
        )
