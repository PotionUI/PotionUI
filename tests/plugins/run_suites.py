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
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MARKETPLACE_ROOT = REPO_ROOT / "content" / "plugins" / "marketplace"


def _run_one(test_dir: Path, pytest_args: List[str]) -> Tuple[str, int, str]:
    plugin_name = test_dir.parent.name
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_dir), *pytest_args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return plugin_name, result.returncode, result.stdout + result.stderr


def main() -> int:
    pytest_args = sys.argv[1:]
    test_dirs = sorted(p for p in MARKETPLACE_ROOT.glob("*/tests") if p.is_dir())

    if not test_dirs:
        print("No marketplace plugin test directories found; nothing to run.")
        return 0

    failed = []
    with ThreadPoolExecutor(max_workers=len(test_dirs)) as pool:
        futures = {
            pool.submit(_run_one, test_dir, pytest_args): test_dir
            for test_dir in test_dirs
        }
        for future in as_completed(futures):
            test_dir = futures[future]
            plugin_name, returncode, output = future.result()
            print(f"\n=== {plugin_name} ({test_dir.relative_to(REPO_ROOT)}) ===")
            print(output)
            if returncode != 0:
                failed.append(plugin_name)

    if failed:
        print(f"\nFAILED plugin suites: {', '.join(sorted(failed))}")
        return 1

    print(f"\nAll {len(test_dirs)} plugin suite(s) passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
