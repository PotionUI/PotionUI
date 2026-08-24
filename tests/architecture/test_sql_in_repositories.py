"""Permanent guard: every database query lives behind a repository.

No `src/` code outside the sanctioned locations may issue SQL directly. The
sanctioned locations are:

  * ``src/platform/database/`` — the substrate itself (the `Database`
    wrapper, the migration runner, the migrations).
  * any module named ``repository.py`` or ``*_repository.py``, or living
    under a ``repository/``/``repositories/`` directory — that IS the
    repository layer, by naming convention across the codebase (see
    ``src/features/*/repository.py``, ``src/features/generation/
    file_repository.py``, ``src/features/model_library/repository/*.py``).

The check is AST-based (stdlib only, like ``test_layering.py``): it flags a
call to ``.execute(...)``/``.executemany(...)`` whose first argument is a
string literal (or f-string) that starts with a SQL keyword. Grepping for the
keyword alone would trip on docstrings, comments and prose ("Update settings
for the user...") — anchoring on the call site's literal argument avoids that
without ever needing an import of ``src``.

What this does NOT catch (accepted, documented limitation, matching the
`.execute()`-literal contract above): a query built as a variable earlier and
passed to `.execute(query)` by name rather than by literal. Every current
repository keeps the literal (or an f-string) inline at the call site, so this
has zero false negatives on the codebase as it stands; a future violation that
routes around it would need to go out of its way to.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {"__pycache__", "node_modules", "venv", ".git"}

EXECUTE_METHODS = {"execute", "executemany"}

# Matches at the start of the (stripped) SQL string - real queries open with
# the verb; prose that merely mentions one ("Update settings for the user")
# does not open with it.
SQL_KEYWORD_RE = re.compile(
    r"^\s*(SELECT\s|INSERT\s+(OR\s+\w+\s+)?INTO\s|UPDATE\s+\w|DELETE\s+FROM\s|"
    r"CREATE\s+(TABLE|INDEX|VIEW)\s|DROP\s+(TABLE|INDEX)\s)",
    re.IGNORECASE,
)

# file (relative to repo root) -> reason. Every entry here is a judgement call
# the maintainer should periodically re-examine, not a permanent exemption.
ALLOWLIST: dict[str, str] = {}


def _is_sanctioned(rel_path: Path) -> bool:
    parts = rel_path.parts
    if parts[:3] == ("src", "platform", "database"):
        return True
    if rel_path.name == "repository.py" or rel_path.name.endswith("_repository.py"):
        return True
    if "repository" in parts or "repositories" in parts:
        return True
    if str(rel_path) in ALLOWLIST:
        return True
    return False


def _literal_prefix(node: ast.expr) -> str | None:
    """The string this argument would evaluate to, for the literal shapes a
    hand-written SQL statement actually appears as. `None` for anything else
    (a variable, a function call, ...) - those are out of this guard's reach,
    see the module docstring."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        # f"SELECT ... {x}" - the leading literal chunk is what matters; a SQL
        # statement never opens with an interpolated placeholder.
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                if value.value.strip():
                    return value.value
                continue
            return None
        return None
    return None


def _py_files(base: Path):
    for f in base.rglob("*.py"):
        if not any(part in SKIP_DIRS for part in f.parts):
            yield f


def _sql_violations(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in EXECUTE_METHODS):
            continue
        if not node.args:
            continue
        text = _literal_prefix(node.args[0])
        if text is None:
            continue
        if SQL_KEYWORD_RE.match(text):
            hits.append(f"{path.relative_to(ROOT)}:{node.lineno} .{func.attr}(...)")
    return hits


def test_no_sql_execute_outside_repositories():
    src = ROOT / "src"
    assert src.is_dir()

    violations: list[str] = []
    for f in _py_files(src):
        rel = f.relative_to(ROOT)
        if _is_sanctioned(rel):
            continue
        violations.extend(_sql_violations(f))

    assert not violations, (
        "raw SQL execute() outside a repository - move the query into the "
        "owning feature's repository.py:\n" + "\n".join(violations)
    )


@pytest.mark.parametrize("rel_path,reason", sorted(ALLOWLIST.items()))
def test_allowlist_entries_still_exist_and_are_still_violations(rel_path: str, reason: str):
    """An allowlist entry that no longer matches anything (file moved, query
    fixed) is dead weight the maintainer should prune, not carry forever."""
    path = ROOT / rel_path
    assert path.is_file(), f"allowlisted {rel_path} no longer exists - remove it ({reason})"
    assert _sql_violations(path), f"allowlisted {rel_path} has no SQL violation left - remove it ({reason})"
