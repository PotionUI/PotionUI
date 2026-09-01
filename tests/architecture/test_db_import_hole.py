"""Permanent guard for the module-level `db` import hole.

Incident: a module that binds ``db`` at its own top level binds that name to
whatever ``Database`` singleton exists at the moment the module is *first*
imported - collection alone is enough. `tests/conftest.py`'s ``mock_db``
fixture (and `tests/fixtures/persistence_base.py`) redirect the canonical
``src.platform.database.database.db``; any module that bound its own ``db``
at import time keeps talking to whatever ``db`` was at that first import -
the live database, in a plain ``pytest tests/`` run - for the rest of the
process, invisible to those fixtures.

The fix is to defer the import to call time, inside whichever function uses
``db`` - see any method in ``src/features/downloads/repository.py`` or
``src/platform/settings/repository.py`` for the pattern.

Every spelling of the import freezes the same object, so all of them are
forbidden here:

- ``src.platform.database.database`` - the defining submodule.
- ``src.platform.database`` - the package-level re-export.
- ``src.plugin_api`` / ``src.plugin_api.storage`` - the plugin-facing
  re-exports.

The two package ``__init__`` modules resolve ``db`` through a PEP 562
``__getattr__`` rather than binding it, which is what makes a *call-time*
``from src.plugin_api import db`` re-resolve on every call. That alone does
not close the hole: ``from ... import db`` still copies the result into the
importer's namespace, so a module-level import freezes it just the same.
Call-time resolution per use site is the real fix; ``__getattr__`` is
defence-in-depth for out-of-tree plugins this guard cannot see.

Modules that only *re-export* the name for others to import (like
``src/plugin_api/storage.py``) use a module ``__getattr__`` instead, so the
name is resolved fresh on each access rather than snapshotted at re-export
time.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {"__pycache__", "node_modules", "venv", ".git", "dist"}

# Every module whose `db` is the same frozen singleton handle.
TARGET_MODULES = {
    "src.platform.database.database",
    "src.platform.database",
    "src.plugin_api",
    "src.plugin_api.storage",
}

# Trees this guard walks. `content/plugins/local/` is a separate, gitignored
# repository that is simply absent from a clean checkout - rglob yields
# nothing there and the guard passes, while a developer who does have it
# checked out still gets told when one of those plugins reopens the hole.
SCAN_ROOTS = ("src", "content/plugins")

# path (relative to repo root) -> why this module-level `db` import is safe.
ALLOWLIST: dict[str, str] = {
    "src/platform/database/migration_runner.py": (
        "one of the two names tests/conftest.py's mock_db fixture patches "
        "directly (`migration_runner.db`) - the canonical patched pair "
        "along with database.database.db itself; other modules should defer "
        "their own import to call time rather than ask to be added here."
    ),
}


def _is_exempt(path: Path) -> bool:
    """Migration modules and test modules are not part of the hazard.

    A migration is loaded fresh per run via
    ``importlib.util.spec_from_file_location``
    (``src/platform/database/migration_runner.py``) and never cached in
    ``sys.modules``, so its module-level import always executes after the
    current db redirection is in place and cannot freeze a stale reference.
    The same holds for a plugin's own ``migrations/`` directory.

    Test modules are excluded because they are the code performing the
    redirection - a test that deliberately reaches for the process-wide
    singleton (to mutate its ``db_path``, say) is doing so on purpose.
    """
    parts = set(path.parts)
    return "migrations" in parts or "tests" in parts


def _py_files(base: Path):
    if not base.exists():
        return
    for f in base.rglob("*.py"):
        if any(part in SKIP_DIRS for part in f.parts):
            continue
        if _is_exempt(f):
            continue
        yield f


def _scanned_files():
    for root in SCAN_ROOTS:
        yield from _py_files(ROOT / root)


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
    """Line numbers of top-level `from <forbidden module> import db` (or
    `... as db`) statements - `tree.body` only, so anything nested inside a
    function or class (already call-time) is not a violation."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []

    hits = []
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        if _resolved_module(path, node) not in TARGET_MODULES:
            continue
        for alias in node.names:
            bound_name = alias.asname or alias.name
            if bound_name == "db":
                hits.append(node.lineno)
    return hits


def _module_level_db_aliases(path: Path) -> list[int]:
    """Line numbers of top-level `<name> = <something>.db` assignments - the
    `import src.platform.database as X` / `X.db` spelling of the same freeze,
    which no `from ... import` check would see."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []

    hits = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            value = node.value
        else:
            continue
        if isinstance(value, ast.Attribute) and value.attr == "db":
            hits.append(node.lineno)
    return hits


def test_no_module_level_db_import_outside_allowlist():
    violations: list[str] = []
    for f in _scanned_files():
        rel = str(f.relative_to(ROOT))
        if rel in ALLOWLIST:
            continue
        for lineno in _module_level_db_imports(f):
            violations.append(f"{rel}:{lineno}")

    assert not violations, (
        "module-level `import db` freezes `db` at this module's first import, "
        "escaping the db redirection in tests/conftest.py and "
        "tests/fixtures/persistence_base.py - defer the import to call time "
        "instead (see src/features/downloads/repository.py):\n"
        + "\n".join(violations)
    )


def test_no_module_level_db_attribute_alias():
    violations: list[str] = []
    for f in _scanned_files():
        for lineno in _module_level_db_aliases(f):
            violations.append(f"{str(f.relative_to(ROOT))}:{lineno}")

    assert not violations, (
        "a module-level `x = <module>.db` alias freezes the handle exactly "
        "like a module-level import of it - read `db` inside the function "
        "that uses it instead:\n" + "\n".join(violations)
    )


def _redirectable_modules() -> set[str]:
    """The only modules whose `db` attribute a test may assign to: the two
    that still bind the name, and so are the ones a redirection has to go
    through."""
    return {
        "src.platform.database.database",
        "src.platform.database.migration_runner",
    }


def _module_aliases(tree: ast.Module) -> dict[str, str]:
    """`import a.b.c as x` / `import a.b.c` -> the dotted module each
    module-level name refers to."""
    aliases = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Import):
            continue
        for alias in node.names:
            aliases[alias.asname or alias.name.split(".")[0]] = alias.name
    return aliases


def _dotted(node: ast.AST) -> str | None:
    """Dotted source text of a pure Name/Attribute chain, else None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _silent_db_redirections(path: Path) -> list[str]:
    """`<module>.db = <x>` assignments onto a module that no longer binds
    `db` - the assignment succeeds, the repository never reads it, and the
    test quietly runs against whatever database was already in place."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []

    aliases = _module_aliases(tree)
    allowed = _redirectable_modules()
    hits = []
    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else []
        for target in targets:
            if not (isinstance(target, ast.Attribute) and target.attr == "db"):
                continue
            base = _dotted(target.value)
            if base is None:
                continue
            # Either a dotted import path written out in full, or a name
            # bound to one by `import ... as ...`.
            module_name = base if base.startswith("src.") else aliases.get(base)
            if module_name is None or not module_name.startswith("src."):
                continue
            if module_name in allowed:
                continue
            hits.append(f"{path.relative_to(ROOT)}:{node.lineno} ({module_name}.db)")
    return hits


def test_no_test_redirects_db_on_a_module_that_no_longer_binds_it():
    """The failure mode this catches is silent: the assignment lands on a
    module attribute nothing reads, so the test keeps passing while running
    against the real database instead of its scratch one."""
    violations: list[str] = []
    for root in ("tests", "content/plugins"):
        for f in (ROOT / root).rglob("*.py"):
            if any(part in SKIP_DIRS for part in f.parts):
                continue
            violations.extend(_silent_db_redirections(f))

    assert not violations, (
        "these assignments no longer reach anything - the module resolves "
        "`db` at call time, so redirect "
        "`src.platform.database.database.db` instead:\n" + "\n".join(violations)
    )


def test_allowlist_entries_still_exist_and_are_needed():
    for rel in ALLOWLIST:
        path = ROOT / rel
        assert path.is_file(), f"allowlisted {rel} no longer exists - remove its entry"
        assert _module_level_db_imports(path), (
            f"{rel} no longer has a module-level db import - remove its allowlist entry"
        )


@pytest.mark.parametrize("module_name", sorted(TARGET_MODULES))
def test_every_forbidden_module_actually_exposes_db(module_name):
    """The guard is only worth anything while each name it forbids really
    does resolve to the singleton - a typo'd or moved module would silently
    forbid nothing."""
    import importlib

    from src.platform.database.database import db as canonical

    module = importlib.import_module(module_name)
    assert getattr(module, "db", None) is canonical, (
        f"{module_name}.db no longer resolves to the canonical Database "
        "singleton - this guard's TARGET_MODULES is out of date"
    )
