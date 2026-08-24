#!/usr/bin/env python3
"""Lint the typed documentation (Docs 2.0) — thin CLI over ``src.features.docs.lint``.

    python scripts/docs_lint.py            # lint docs/
    python scripts/docs_lint.py --docs-root some/dir

Exits nonzero when any ERROR is found (warnings don't fail the lint). The lint
rules live in ``src/features/docs/lint.py`` so the CLI and the developer API endpoint
(``GET /api/developer/docs/lint``) share one implementation.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.docs.lint import lint_docs  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Lint typed documentation (Docs 2.0).")
    ap.add_argument("--docs-root", default="docs", help="Docs directory (default: docs).")
    args = ap.parse_args(argv)

    report = lint_docs(args.docs_root)
    for w in report.warnings:
        print(f"WARN  {w.path}: {w.message}")
    for e in report.errors:
        print(f"ERROR {e.path}: {e.message}")
    print(f"\n{len(report.errors)} error(s), {len(report.warnings)} warning(s).")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
