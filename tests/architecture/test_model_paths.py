from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Iterable, List, Tuple

import pytest

from src.platform.filesystem.model_types import MODEL_DIRECTORY_NAMES

ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {"__pycache__", "node_modules", "venv", ".git"}

_USER_PLUGIN_ROOT = Path("content/plugins/local")

_SCAN_ROOTS = (Path("src"), Path("content/plugins"))

_DIRECTORY_MAP_ALLOWLIST = (
    "src/platform/filesystem/model_types.py",
    "src/platform/filesystem/model_roots.py",
    "src/platform/filesystem/__init__.py",
    "src/features/models/root_detection.py",
    "src/features/models/roots.py",
    "src/features/models/symlink_adoption.py",
    "src/features/models/indexer.py",
    "src/features/models/catalog.py",
    "src/platform/templating/dict_utils.py",
    "src/features/remote_execution/model_bundle_builder.py",
    "src/features/remote_execution/ops.py",
    "src/features/remote_execution/worker/",
    "src/features/backends/native_remote_backend.py",
    "src/features/automation/nodes/actions.py",
    "src/platform/database/migrations/",
)

_MODELS_DIR_ALLOWLIST = (
    "src/platform/settings/settings.py",
    "src/platform/filesystem/model_roots.py",
    "src/platform/filesystem/model_roots_repository.py",
    "src/features/models/roots.py",
    "src/bootstrap/app.py",
    "src/bootstrap/container.py",
    "src/features/backup/paths.py",
    "src/platform/database/migrations/",
)

_TYPE_DIR_LITERAL_ALLOWLIST = (
    "src/platform/templating/dict_utils.py",
    "src/features/developer/template_functions_documenter.py",
    "src/platform/database/migrations/",
    "src/pipelines/pipes/tiled_refiner/main.py",
    "src/pipelines/pipes/_shared/detection/hand_detector.py",
    "src/pipelines/pipes/_shared/detection/teeth_detector.py",
    "src/pipelines/pipes/_shared/detection/detailer_helper.py",
    "src/pipelines/pipes/_shared/detection/eye_detector.py",
    "src/pipelines/pipes/_shared/detection/face_detector.py",
    "src/pipelines/pipes/detailer/video_ltx/detection.py",
    "src/pipelines/pipes/detailer/video_ltx/main.py",
    "src/pipelines/pipes/detailer/sdxl/main.py",
    "src/features/automation/nodes/triggers.py",
    "src/features/automation/nodes/actions.py",
)

_DIRECTORY_MAP_NAMES = ("MODEL_TYPE_TO_DIRECTORY", "DIRECTORY_TO_MODEL_TYPE", "MODEL_DIRECTORY_NAMES")

_TYPE_DIR_LITERAL_RE = re.compile(
    r"models/(?:" + "|".join(re.escape(n) for n in MODEL_DIRECTORY_NAMES) + r")/"
)


def _is_user_plugin_path(rel: Path) -> bool:
    return rel.parts[: len(_USER_PLUGIN_ROOT.parts)] == _USER_PLUGIN_ROOT.parts


def _py_files() -> Iterable[Path]:
    for scan_root in _SCAN_ROOTS:
        base = ROOT / scan_root
        if not base.is_dir():
            continue
        for f in base.rglob("*.py"):
            if any(part in SKIP_DIRS for part in f.parts):
                continue
            rel = f.relative_to(ROOT)
            if _is_user_plugin_path(rel):
                continue
            if "tests" in rel.parts:
                continue
            yield f


def _allowed(rel_posix: str, allowlist: Tuple[str, ...]) -> bool:
    return any(rel_posix == entry or rel_posix.startswith(entry) for entry in allowlist)


def _directory_map_imports(tree: ast.AST) -> List[str]:
    found: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in _DIRECTORY_MAP_NAMES:
                    found.append(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[-1] in _DIRECTORY_MAP_NAMES:
                    found.append(alias.name)
    return found


def _models_dir_literal_hits(text: str) -> bool:
    return "'models_dir'" in text or '"models_dir"' in text or "get_models_dir(" in text


_DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _docstring_node_ids(tree: ast.AST) -> set:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, _DOCSTRING_OWNERS):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def _type_dir_literal_hits(tree: ast.AST) -> List[str]:
    hits: List[str] = []
    docstring_ids = _docstring_node_ids(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstring_ids:
            if _TYPE_DIR_LITERAL_RE.search(node.value):
                hits.append(node.value)
    return hits


def test_directory_map_import_is_allowlisted():
    violations: List[str] = []
    for f in _py_files():
        rel_posix = f.relative_to(ROOT).as_posix()
        if _allowed(rel_posix, _DIRECTORY_MAP_ALLOWLIST):
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        hits = _directory_map_imports(tree)
        if hits:
            violations.append(f"{rel_posix} imports {', '.join(sorted(set(hits)))}")

    assert not violations, "MODEL_TYPE_TO_DIRECTORY/DIRECTORY_TO_MODEL_TYPE/MODEL_DIRECTORY_NAMES used outside the allowlist:\n" + "\n".join(violations)


def test_models_dir_literal_is_allowlisted():
    violations: List[str] = []
    for f in _py_files():
        rel_posix = f.relative_to(ROOT).as_posix()
        if _allowed(rel_posix, _MODELS_DIR_ALLOWLIST):
            continue
        text = f.read_text(encoding="utf-8")
        if _models_dir_literal_hits(text):
            violations.append(rel_posix)

    assert not violations, "'models_dir' / get_models_dir( used outside the allowlist:\n" + "\n".join(violations)


def test_type_dir_path_literal_is_allowlisted():
    violations: List[str] = []
    for f in _py_files():
        rel_posix = f.relative_to(ROOT).as_posix()
        if _allowed(rel_posix, _TYPE_DIR_LITERAL_ALLOWLIST):
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        hits = _type_dir_literal_hits(tree)
        if hits:
            violations.append(f"{rel_posix}: {hits[0]!r}")

    assert not violations, "a 'models/<type dir>/' string literal was found outside the allowlist:\n" + "\n".join(violations)


def test_bite_check_directory_map_import_is_actually_caught(tmp_path):
    bad = tmp_path / "src" / "features" / "not_allowlisted.py"
    bad.parent.mkdir(parents=True)
    bad.write_text("from src.platform.filesystem.model_types import MODEL_TYPE_TO_DIRECTORY\n", encoding="utf-8")

    tree = ast.parse(bad.read_text(encoding="utf-8"))
    hits = _directory_map_imports(tree)
    rel_posix = "src/features/not_allowlisted.py"

    assert hits
    assert not _allowed(rel_posix, _DIRECTORY_MAP_ALLOWLIST)


def test_bite_check_models_dir_literal_is_actually_caught():
    text = "root = settings.get_setting('models_dir')\n"
    assert _models_dir_literal_hits(text)
    assert not _allowed("src/features/not_allowlisted.py", _MODELS_DIR_ALLOWLIST)


def test_bite_check_type_dir_literal_is_actually_caught():
    tree = ast.parse("path = 'models/loras/' + filename\n")
    hits = _type_dir_literal_hits(tree)
    assert hits
    assert not _allowed("src/features/not_allowlisted.py", _TYPE_DIR_LITERAL_ALLOWLIST)


def test_bite_check_join_with_neither_half_available_cannot_be_written():
    with pytest.raises(NameError):
        eval("models_root / type_dir")
