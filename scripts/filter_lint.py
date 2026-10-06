import sys
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.features.filters.lint import default_roots, format_finding, lint_roots
from src.features.filters.plugin_ops import plugin_filter_ops
from src.features.filters.schema import SOURCE_LOCAL
from src.platform.imaging.filters import merged_ops
from src.platform.plugins.loader import PluginLoader


def main(argv: List[str]) -> int:
    manifests = PluginLoader().discover_plugins()
    ops = merged_ops(plugin_filter_ops(manifests))
    if argv:
        roots = [(Path(p), SOURCE_LOCAL, "") for p in argv]
    else:
        roots = default_roots(ROOT / "content" / "filters", manifests)

    report = lint_roots(roots, ops)

    if not report.scanned:
        print("No filter directories found.")
        return 0

    for path in report.scanned:
        items = report.findings.get(path, [])
        if not items:
            print(f"{path}: OK")
            continue
        print(f"{path}:")
        for finding in items:
            print(f"  - {format_finding(finding)}")

    errors = len(report.errors)
    warnings = len(report.warnings)
    print(f"\n{errors} error(s), {warnings} warning(s).")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
