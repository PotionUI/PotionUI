"""Permanent guard against async test methods on `unittest.TestCase` subclasses.

A past audit found 49 `async def test_*` methods across 5 `unittest.TestCase`
classes that had never actually run. pytest's unittest integration calls a `TestCase` method,
gets back a coroutine (because it's `async def`), never awaits it, and reports
the test as **passed** - the body never executes. `pytest-asyncio` only
instruments plain classes and module-level test functions; it does not apply
to `unittest.TestCase` subclasses at all. `unittest.IsolatedAsyncioTestCase` is
the one exception - it drives its own event loop and genuinely awaits async
test methods - so it is exempt from this guard.

The fix for each affected class was to drop `unittest.TestCase` and become a
plain class (this repo's `pytest.ini` sets `asyncio_mode = auto`, so no
`@pytest.mark.asyncio` is even required, though the existing convention adds
it anyway) or, where xUnit-style `setUp`/`tearDown` mattered, to keep the
`setUp`/`tearDown` names as `setup_method`/`teardown_method` and convert
`self.assertX(...)` calls to plain `assert`.

Like `test_layering.py`, this walks source with the standard library only
(`ast`), so it carries no third-party dependency.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parents[2]
SCOPES = ("tests", "content/plugins")
SKIP_DIRS = {"__pycache__", "node_modules", "venv", ".git", ".svelte-kit", "dist"}

# unittest.IsolatedAsyncioTestCase genuinely awaits async test methods (it runs
# its own event loop per test) - a class rooted here is exempt, even though it
# is itself a unittest.TestCase subclass.
SAFE_BASE = "IsolatedAsyncioTestCase"
UNSAFE_BASE = "TestCase"


def _py_files():
    for scope in SCOPES:
        base = ROOT / scope
        if not base.is_dir():
            continue
        for f in base.rglob("*.py"):
            if not any(part in SKIP_DIRS for part in f.parts):
                yield f


def _base_name(node: ast.expr):
    """Last dotted component of a base-class expression: `unittest.TestCase`
    and a bare `TestCase` (via `from unittest import TestCase`) both -> "TestCase"."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _collect():
    """Scan every file once, returning:

    - `global_bases`: class name -> the set of its immediate base names, so
      transitive inheritance through a local base class (in this file or any
      other under `tests/`/`plugins/`) can be resolved by name.
    - `async_tests`: (file, class name) -> [(method name, lineno), ...] for
      every `async def test*` method defined directly on that class.
    """
    global_bases: Dict[str, Set[str]] = {}
    async_tests: Dict[Tuple[Path, str], List[Tuple[str, int]]] = {}

    for f in _py_files():
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {b for b in (_base_name(base) for base in node.bases) if b}
            global_bases.setdefault(node.name, set()).update(bases)
            for item in node.body:
                if isinstance(item, ast.AsyncFunctionDef) and item.name.startswith("test"):
                    async_tests.setdefault((f, node.name), []).append((item.name, item.lineno))

    return global_bases, async_tests


def _is_unaited_testcase(name: str, global_bases: Dict[str, Set[str]], seen=None) -> bool:
    """True if `name` is a (possibly transitive) `unittest.TestCase` subclass
    whose async test methods pytest never awaits - i.e. `TestCase` is on its
    base chain but `IsolatedAsyncioTestCase` is not."""
    if seen is None:
        seen = set()
    if name in seen:
        return False
    seen.add(name)
    if name == SAFE_BASE:
        return False
    if name == UNSAFE_BASE:
        return True
    return any(_is_unaited_testcase(b, global_bases, seen) for b in global_bases.get(name, ()))


def test_no_async_def_test_on_unittest_testcase():
    global_bases, async_tests = _collect()

    violations = []
    for (path, cls_name), methods in sorted(async_tests.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
        if _is_unaited_testcase(cls_name, global_bases):
            for method, lineno in methods:
                violations.append(f"{path.relative_to(ROOT)}:{lineno} {cls_name}.{method}")

    assert not violations, (
        "`async def test_*` methods on a `unittest.TestCase` subclass are never "
        "awaited by pytest and always report passed without running - convert "
        "the class to a plain class (pytest-asyncio picks it up; asyncio_mode "
        "is 'auto' in pytest.ini) or to unittest.IsolatedAsyncioTestCase:\n"
        + "\n".join(violations)
    )
