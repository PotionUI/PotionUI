#!/usr/bin/env python3
"""Run each marketplace plugin's own test suite.

Each plugin under content/plugins/marketplace/<plugin>/tests is collected in
its own pytest subprocess, never pooled into one invocation with another
plugin's tests: plugins that ship a top-level `backend` package (the `api:`
module convention, see docs/plugin-api.md) claim that name in sys.modules at
conftest collection time, and a second plugin's conftest doing the same
would silently point the first plugin's `backend.*` imports at the wrong
plugin's package for the rest of the session.

Usage: run_suites.py [pytest-args...]
Any arguments are forwarded verbatim to each per-plugin pytest invocation.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MARKETPLACE_ROOT = REPO_ROOT / "content" / "plugins" / "marketplace"


def main() -> int:
    pytest_args = sys.argv[1:]
    test_dirs = sorted(p for p in MARKETPLACE_ROOT.glob("*/tests") if p.is_dir())

    if not test_dirs:
        print("No marketplace plugin test directories found; nothing to run.")
        return 0

    failed = []
    for test_dir in test_dirs:
        plugin_name = test_dir.parent.name
        print(f"\n=== {plugin_name} ({test_dir.relative_to(REPO_ROOT)}) ===")
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_dir), *pytest_args],
            cwd=REPO_ROOT,
        )
        if result.returncode != 0:
            failed.append(plugin_name)

    if failed:
        print(f"\nFAILED plugin suites: {', '.join(failed)}")
        return 1

    print(f"\nAll {len(test_dirs)} plugin suite(s) passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
