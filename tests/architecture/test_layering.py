"""Permanent guard for the feature-first package layering.

The OSS-prep restructure split ``src`` into five packages with a strict import
direction. These tests enforce that direction so contributors cannot quietly
reintroduce a cycle or resurrect one of the dissolved packages. They are the
pytest reimplementation of what the (now removed) ``scripts/refactor``
migration tooling checked stage by stage.

Contracts:

1. ``src/features`` must not import ``src.bootstrap`` — a feature may not reach
   up into the composition root.
2. ``src/platform`` must not import ``src.features`` — platform is the shared
   substrate; it may not depend on the domains built on top of it.
3. ``src/pipelines`` must not import ``src.features`` or ``src.bootstrap`` — a
   pipeline is engine machinery, downstream of no feature and of no wiring.
4. Nothing may write ``from src import platform`` — ``src.platform`` shadows the
   stdlib ``platform`` module, so it must always be imported by its full path.
5. The packages the restructure dissolved — ``src.core``, ``src.api``,
   ``src.persistence``, ``src.services`` — stay dissolved: no importer anywhere,
   and no leftover directory.
6. ``content/plugins/`` must not import ``src.features``, ``src.platform``,
   ``src.pipelines`` or ``src.bootstrap`` directly — ``src.plugin_api`` is the
   only sanctioned surface (the krea2-edit exception to this rule is closed,
   recorded at commit 855a93da). Audited clean at the time this rule was
   added: every plugin already imported exclusively from ``src.plugin_api``.

The check walks source files with the standard library only (``ast`` + ``re``),
exactly like the original script, so this suite carries no third-party
dependency and runs anywhere the repo is checked out.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {"__pycache__", "node_modules", "venv", ".git"}

# `from src import platform` — the stdlib-shadow guard.
FROM_SRC_PLATFORM_RE = re.compile(
    r"^\s*from\s+src\s+import\s+.*\bplatform\b", re.MULTILINE
)
# Any `src.*` import, dotted-path form, for the dissolved-package scan.
IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+(src(?:\.[\w.]+)?)", re.MULTILINE)

FORBIDDEN = {
    "src/features": ("src.bootstrap",),
    "src/platform": ("src.features",),
    "src/pipelines": ("src.features", "src.bootstrap"),
    # vendor/ ports third-party (GPL) source with zero PotionUI dependencies —
    # a vendor module importing src would mean vendored code can't be reasoned
    # about (or relicensed/replaced) independent of the rest of the codebase.
    "vendor": ("src",),
    # content/plugins/ may only import src.plugin_api - everything else is
    # internal and moves without notice (see src/plugin_api/__init__.py's own
    # docstring). `_matches` is prefix-based, so this doesn't catch
    # `src.plugin_api` itself.
    "content/plugins": ("src.features", "src.platform", "src.pipelines", "src.bootstrap"),
}
DISSOLVED_PREFIXES = ("src.core", "src.api", "src.persistence", "src.services")


def _is_type_checking_test(test: ast.expr) -> bool:
    """True for ``TYPE_CHECKING`` or ``<mod>.TYPE_CHECKING`` if-guards."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def _runtime_src_imports(text: str) -> list[str]:
    """Return the ``src.*`` modules imported at *runtime*.

    Imports guarded by ``if TYPE_CHECKING:`` are excluded — a type-only import
    creates no runtime dependency and so cannot violate a layering contract.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return IMPORT_RE.findall(text)

    modules: list[str] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.If) and _is_type_checking_test(child.test):
                for orelse in child.orelse:  # the else-branch still runs
                    visit(orelse)
                continue
            if isinstance(child, ast.ImportFrom):
                if child.level == 0 and child.module and child.module.startswith("src"):
                    modules.append(child.module)
            elif isinstance(child, ast.Import):
                for alias in child.names:
                    if alias.name.startswith("src"):
                        modules.append(alias.name)
            visit(child)

    visit(tree)
    return modules


# content/plugins/local is user-owned (often a nested checkout carrying its own
# test trees) - not shipped code, not this repo's to police.
_USER_PLUGIN_ROOT = Path("content/plugins/local")


def _py_files(base: Path):
    for f in base.rglob("*.py"):
        if any(part in SKIP_DIRS for part in f.parts):
            continue
        try:
            if f.relative_to(ROOT).parts[: len(_USER_PLUGIN_ROOT.parts)] == _USER_PLUGIN_ROOT.parts:
                continue
        except ValueError:
            pass
        yield f


def _matches(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


@pytest.mark.parametrize("scope,banned", sorted(FORBIDDEN.items()))
def test_layer_does_not_import_forbidden(scope: str, banned: tuple[str, ...]):
    base = ROOT / scope
    assert base.is_dir(), f"expected package directory {scope} to exist"

    violations: list[str] = []
    for f in _py_files(base):
        for mod in _runtime_src_imports(f.read_text(encoding="utf-8")):
            if any(_matches(mod, b) for b in banned):
                violations.append(f"{f.relative_to(ROOT)} imports {mod}")

    assert not violations, "forbidden cross-layer imports:\n" + "\n".join(violations)


def test_no_from_src_import_platform():
    """`src.platform` shadows the stdlib; it must be imported by full path."""
    violations: list[str] = []
    for scope in ("src", "tests", "content/plugins", "scripts"):
        base = ROOT / scope
        if not base.is_dir():
            continue
        for f in _py_files(base):
            if FROM_SRC_PLATFORM_RE.search(f.read_text(encoding="utf-8")):
                violations.append(str(f.relative_to(ROOT)))

    assert not violations, "`from src import platform` (stdlib shadow risk):\n" + "\n".join(
        violations
    )


def test_dissolved_packages_have_no_importers():
    violations: list[str] = []
    for scope in ("src", "tests", "content/plugins", "scripts"):
        base = ROOT / scope
        if not base.is_dir():
            continue
        for f in _py_files(base):
            for mod in IMPORT_RE.findall(f.read_text(encoding="utf-8")):
                if any(_matches(mod, p) for p in DISSOLVED_PREFIXES):
                    violations.append(f"{f.relative_to(ROOT)} imports {mod}")
                    break

    assert not violations, "imports of dissolved packages:\n" + "\n".join(violations)


@pytest.mark.parametrize("prefix", DISSOLVED_PREFIXES)
def test_dissolved_package_directory_is_gone(prefix: str):
    pkg = ROOT / prefix.replace(".", "/")
    assert not pkg.exists(), f"{prefix.replace('.', '/')}/ should not exist (dissolved package)"
