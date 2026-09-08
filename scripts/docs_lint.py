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
from scripts.pipes_reference import check as check_pipes_reference  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Lint typed documentation (Docs 2.0).")
    ap.add_argument("--docs-root", default="docs", help="Docs directory (default: docs).")
    args = ap.parse_args(argv)

    report = lint_docs(args.docs_root)
    for w in report.warnings:
        print(f"WARN  {w.path}: {w.message}")
    for e in report.errors:
        print(f"ERROR {e.path}: {e.message}")

    # docs/pipes.md and docs/preset-context.md are generated from the pipe
    # catalog and the templating/preset surface (scripts/pipes_reference.py);
    # a drift here means the committed file no longer matches the code. An
    # environment that can't import some pipes can't verify their sections
    # either - that surfaces as a warning, not a silent pass.
    reference_report = check_pipes_reference()
    for w in reference_report.warnings:
        print(f"WARN  {w}")
    for e in reference_report.errors:
        print(f"ERROR {e}")

    total_errors = len(report.errors) + len(reference_report.errors)
    total_warnings = len(report.warnings) + len(reference_report.warnings)
    print(f"\n{total_errors} error(s), {total_warnings} warning(s).")
    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
