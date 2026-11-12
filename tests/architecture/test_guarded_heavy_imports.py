import ast
from pathlib import Path

from src.platform.runtime.guarded_import import GUARDED_ROOTS

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
HELPER = SRC / "platform" / "runtime" / "guarded_import.py"
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _function_level_heavy_imports(tree: ast.AST) -> list[int]:
    found: list[int] = []

    def walk(node: ast.AST, in_function: bool, type_checking: bool) -> None:
        for child in ast.iter_child_nodes(node):
            inside = in_function or isinstance(child, FUNCTIONS)
            typing_only = type_checking or (isinstance(child, ast.If) and "TYPE_CHECKING" in ast.dump(child.test))
            if inside and not typing_only:
                if isinstance(child, ast.ImportFrom) and child.level == 0 and child.module:
                    if child.module.split(".")[0] in GUARDED_ROOTS:
                        found.append(child.lineno)
                elif isinstance(child, ast.Import):
                    if any(a.name.split(".")[0] in GUARDED_ROOTS for a in child.names):
                        found.append(child.lineno)
            walk(child, inside, typing_only)

    walk(tree, False, False)
    return found


def test_no_function_level_import_of_a_guarded_library_outside_the_helper():
    offenders = []
    for path in SRC.rglob("*.py"):
        if path == HELPER:
            continue
        for lineno in _function_level_heavy_imports(ast.parse(path.read_text(encoding="utf-8"))):
            offenders.append(f"{path.relative_to(ROOT)}:{lineno}")
    assert offenders == [], "lazy imports of " + ", ".join(sorted(GUARDED_ROOTS)) + " must go through guarded_import/guarded_from"


def test_the_scanner_catches_a_function_level_import():
    tree = ast.parse("def f():\n    import transformers\n\ndef g():\n    from huggingface_hub import x\n\nimport diffusers\n")
    assert _function_level_heavy_imports(tree) == [2, 5]
