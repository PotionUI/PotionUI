from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OOM_TYPES = {"torch.cuda.OutOfMemoryError", "torch.OutOfMemoryError"}
UNFILTERED_FAST_PATHS = {
    "src/platform/runtime/native/kernels/fp8_linear.py",
    "src/platform/runtime/native/kernels/nvfp4_linear.py",
}


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _caught(handler: ast.ExceptHandler) -> set[str]:
    if handler.type is None:
        return set()
    nodes = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return {_dotted(node) for node in nodes}


def _calls(handler: ast.ExceptHandler) -> set[str]:
    return {_dotted(node.func).rsplit(".", 1)[-1] for node in ast.walk(handler) if isinstance(node, ast.Call)}


def _sources() -> list[Path]:
    return sorted((REPO_ROOT / "src").rglob("*.py"))


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _handlers(path: Path) -> list[ast.ExceptHandler]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)]


def test_no_fallback_catches_only_the_torch_out_of_memory_error():
    offenders = [
        f"{_relative(path)}:{handler.lineno}"
        for path in _sources()
        if _relative(path) not in UNFILTERED_FAST_PATHS
        for handler in _handlers(path)
        if _caught(handler) & OOM_TYPES
    ]
    assert offenders == [], "catch RuntimeError and filter it through retry_after_cuda_oom"


def test_every_fallback_that_reads_the_policy_records_the_damage_too():
    offenders = [
        f"{_relative(path)}:{handler.lineno}"
        for path in _sources()
        for handler in _handlers(path)
        if "is_cuda_oom" in _calls(handler) and "retry_after_cuda_oom" not in _calls(handler)
    ]
    assert offenders == [], "a fallback asks retry_after_cuda_oom, the predicate alone records nothing"
