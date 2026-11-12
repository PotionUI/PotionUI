from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SESSIONS = ROOT / "src" / "features" / "sessions"
ENTRY = ("generate.py", "request.py", "request_segments.py", "request_variables.py")
WRITERS = {"create", "update", "delete", "set_pinned", "create_if_changed", "save_session", "update_session", "delete_session"}


def imported_modules(path: Path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
            for alias in node.names:
                yield f"{node.module}.{alias.name}"


def module_file(name: str):
    parts = name.split(".")
    candidate = ROOT.joinpath(*parts)
    if candidate.with_suffix(".py").is_file():
        return candidate.with_suffix(".py")
    if (candidate / "__init__.py").is_file():
        return candidate / "__init__.py"
    return None


def session_closure():
    seen = {}
    pending = [SESSIONS / name for name in ENTRY]
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen[path] = sorted(set(imported_modules(path)))
        for name in seen[path]:
            if name.startswith("src.features.sessions"):
                target = module_file(name)
                if target is not None and target not in seen:
                    pending.append(target)
    return seen


def test_the_generate_path_never_imports_the_session_write_operations():
    offenders = {
        path.name: [name for name in names if name.startswith("src.features.sessions.operations")]
        for path, names in session_closure().items()
    }
    assert not any(offenders.values()), offenders


def test_the_generate_path_never_imports_the_version_writer_or_the_live_announcer():
    forbidden = ("src.features.sessions.version_repository", "src.features.sessions.live")
    offenders = {
        path.name: [name for name in names if name.startswith(forbidden)] for path, names in session_closure().items()
    }
    assert not any(offenders.values()), offenders


def test_the_generate_path_calls_no_writing_method():
    offenders = []
    for name in ENTRY:
        tree = ast.parse((SESSIONS / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in WRITERS:
                owner = ast.unparse(node.func.value)
                if "session" in owner.lower() or "version" in owner.lower():
                    offenders.append(f"{name}:{node.lineno} {owner}.{node.func.attr}")
    assert not offenders, offenders
