DESCRIPTION = (
    'Convert `modes/<mode>/editor.yml` files into `editor_modes/<name>/editor_mode.yml`.\n'
    '\n'
    'Usage:\n'
    '    python scripts/editor_modes_from_editor_yml.py <preset dir or root> [<more> ...]\n'
    '    python scripts/editor_modes_from_editor_yml.py <preset dir or root> --write\n'
    '\n'
    'Without `--write` the script only prints what it would write. With `--write` it\n'
    'creates the new files and never deletes or changes an `editor.yml`. A target that\n'
    'already exists is reported and left alone. Every `editor.yml` found under the given\n'
    'paths is converted, one editor mode per task: it is named after its generation\n'
    'mode, or `<mode>-<task>` when the file declares several tasks.\n'
    '\n'
    'The result starts from the old declaration and nothing else: `image` becomes\n'
    '`inputs.source`, `set` becomes `fixed`, `seed_field` and `quantity_field` move\n'
    'under `inputs`, and every `owned` field that the new file does not already claim\n'
    '(as an input, a fixed value or the size field) goes to `fields.hide`. Review the\n'
    'output and add `change`, `add` and `fixed` entries by hand where the editor\n'
    'should differ from the generation mode.\n'
)


import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DECLARATION_FILE = "editor.yml"
TARGET_DIR = "editor_modes"
TARGET_FILE = "editor_mode.yml"


@dataclass
class Conversion:
    name: str
    source: Path
    target: Path
    data: Dict[str, Any]

    @property
    def text(self) -> str:
        return yaml.safe_dump(self.data, sort_keys=False, allow_unicode=True, default_flow_style=False)

    @property
    def exists(self) -> bool:
        return self.target.exists()


def input_fields(inputs: Dict[str, Any]) -> List[str]:
    image = inputs.get("image")
    names = [image["field"] if isinstance(image, dict) else image]
    names.extend(inputs.get(key) for key in ("mask",))
    return [name for name in names if name]


def convert_task(mode: str, task: Dict[str, Any]) -> Dict[str, Any]:
    old_inputs = dict(task.get("inputs") or {})
    inputs: Dict[str, Any] = {"source": old_inputs.get("image")}
    if old_inputs.get("mask"):
        inputs["mask"] = old_inputs["mask"]
    if task.get("quantity_field"):
        inputs["quantity"] = task["quantity_field"]
    if task.get("seed_field"):
        inputs["seed"] = task["seed_field"]

    document: Dict[str, Any] = {"schema_version": 1, "task": task["task"], "from": mode}
    if task.get("label"):
        document["label"] = task["label"]
    document["region"] = task.get("region")
    document["inputs"] = inputs
    size = task.get("size")
    if size:
        document["size"] = dict(size)
    if "context" in task:
        document["context"] = task["context"]
    fixed = dict(task.get("set") or {})
    if fixed:
        document["fixed"] = fixed

    claimed = {*input_fields(old_inputs), *fixed, *(inputs.get(key) for key in ("quantity", "seed") if inputs.get(key))}
    owned = [*(task.get("owned") or []), *([size["field"]] if size and size.get("field") else [])]
    hide = []
    for name in owned:
        if name not in claimed and name not in hide:
            hide.append(name)
    if hide:
        document["fields"] = {"hide": hide}
    return document


def mode_name(mode: str, task: Dict[str, Any], count: int) -> str:
    if count == 1:
        return mode
    return f"{mode}-{str(task['task']).replace('.', '-').replace('_', '-')}"


def convert_file(path: Path) -> List[Conversion]:
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    tasks = document.get("tasks") or []
    mode = path.parent.name
    preset_root = path.parent.parent.parent
    conversions = []
    for task in tasks:
        name = mode_name(mode, task, len(tasks))
        conversions.append(
            Conversion(
                name=name,
                source=path,
                target=preset_root / TARGET_DIR / name / TARGET_FILE,
                data=convert_task(mode, task),
            )
        )
    return conversions


def find_declarations(paths: List[Path]) -> List[Path]:
    found: List[Path] = []
    for path in paths:
        if path.is_file() and path.name == DECLARATION_FILE:
            found.append(path)
            continue
        found.extend(
            sorted(p for p in path.rglob(DECLARATION_FILE) if p.parent.parent.name == "modes")
        )
    return found


def run(paths: List[Path], write: bool, out=print) -> int:
    declarations = find_declarations(paths)
    if not declarations:
        out("No modes/<mode>/editor.yml found.")
        return 0
    for declaration in declarations:
        for conversion in convert_file(declaration):
            if conversion.exists:
                out(f"SKIP {conversion.target}: already exists")
                continue
            out(f"{'WRITE' if write else 'WOULD WRITE'} {conversion.target} (from {declaration})")
            if write:
                conversion.target.parent.mkdir(parents=True, exist_ok=True)
                conversion.target.write_text(conversion.text, encoding="utf-8")
            else:
                for line in conversion.text.splitlines():
                    out(f"    {line}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", type=Path, help="A preset directory, or a root holding presets")
    parser.add_argument("--write", action="store_true", help="Write the files; the default only prints them")
    args = parser.parse_args(argv)
    return run([p.resolve() for p in args.paths], args.write)


if __name__ == "__main__":
    sys.exit(main())
