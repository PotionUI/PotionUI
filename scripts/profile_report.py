#!/usr/bin/env python3
"""Render a text report from a per-generation ``profile.jsonl`` file.

Usage:
    python scripts/profile_report.py <path/to/profile.jsonl>

Thin CLI wrapper over ``src.platform.observability.profiling.report`` -- that
module owns the profile row schema and the report layout (the same renderer the
admin profile-viewer route serves to the frontend). This script only handles
argv, file-existence, and printing; it keeps no report logic of its own.

Produces, in plain text: a stage table, the top RSS jumps, a peak/net summary,
the CPU tensor census, and (when a ``generation.log`` sits next to the jsonl)
LOG HIGHLIGHTS. See the module docstring for the section-by-section detail.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.platform.observability.profiling import report as _report  # noqa: E402


def load_rows(path: Path):
    """Load rows, echoing any malformed-line warnings to stderr (kept here so
    the underlying module stays free of stdout/stderr concerns)."""
    malformed: list[tuple[int, str]] = []
    rows = _report.load_rows(path, malformed=malformed)
    for line_num, error in malformed:
        print(f"warning: skipping malformed line {line_num}: {error}", file=sys.stderr)
    return rows


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <profile.jsonl>", file=sys.stderr)
        return 2
    path = Path(argv[1])
    if not path.exists():
        print(f"error: {path} does not exist", file=sys.stderr)
        return 1

    rows = load_rows(path)
    if not rows:
        print("error: no valid rows found", file=sys.stderr)
        return 1

    sys.stdout.write(_report.render_report(rows, path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
