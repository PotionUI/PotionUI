import sys
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.features.model_layouts.catalog import plugin_model_layout_roots, scan_layout_root
from src.features.model_layouts.schema import SOURCE_LOCAL, SOURCE_MARKETPLACE, SOURCE_PLUGIN, ModelLayout
from src.platform.plugins.loader import PluginLoader


def lint_roots(roots: List[Tuple[Path, str, str]]) -> Tuple[List[str], Dict[str, List[str]]]:
    layouts: Dict[str, ModelLayout] = {}
    errors: Dict[str, List[str]] = {}
    scanned: List[str] = []
    for path, source, plugin_id in roots:
        if path.is_dir():
            scanned.extend(scan_layout_root(path, source, layouts, errors, plugin_id or None))
    return scanned, errors


def main(argv: List[str]) -> int:
    if argv:
        roots = [(Path(p), SOURCE_LOCAL, "") for p in argv]
    else:
        manifests = PluginLoader().discover_plugins()
        roots = [
            (ROOT / "content/model-layouts/marketplace", SOURCE_MARKETPLACE, ""),
            (ROOT / "content/model-layouts/local", SOURCE_LOCAL, ""),
        ] + [(root.path, SOURCE_PLUGIN, root.plugin_id) for root in plugin_model_layout_roots(manifests)]

    scanned, errors = lint_roots(roots)

    if not scanned:
        print("No model layout files found.")
        return 0

    total = 0
    for path in scanned:
        issues = errors.get(path)
        if issues:
            total += len(issues)
            print(f"{path}:")
            for issue in issues:
                print(f"  - {issue}")
        else:
            print(f"{path}: OK")

    if total:
        print(f"\n{total} issue(s) found.")
        return 1

    print("\nNo issues found.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
