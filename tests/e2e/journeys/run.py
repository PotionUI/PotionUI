#!/usr/bin/env python3
"""Runner for feature journeys - self-verification checks that exercise a
recently landed backend change end-to-end over HTTP, against one throwaway
PotionUI instance shared by every journey in the run.

Each journey is a module under `tests/e2e/journeys/` exposing
`run(app: ThrowawayApp) -> JourneyResult`. See `README.md` in this directory
for the convention new journeys should follow.

Usage:

    python tests/e2e/journeys/run.py                  # run every journey
    python tests/e2e/journeys/run.py chat_pre_actions_empty
    python tests/e2e/journeys/run.py --models-dir /path/to/depot
    python tests/e2e/journeys/run.py --keep
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
HARNESS_DIR = REPO_ROOT / "tests" / "e2e" / "harness"
if str(HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(HARNESS_DIR))

from e2e_harness import JourneyResult, StageError, ThrowawayApp, log  # noqa: E402

JOURNEYS_DIR = Path(__file__).resolve().parent
_EXCLUDED_MODULES = {"run", "__init__"}


def discover_journeys() -> List[str]:
    return sorted(
        path.stem for path in JOURNEYS_DIR.glob("*.py") if path.stem not in _EXCLUDED_MODULES
    )


def run_journey(name: str, app: ThrowawayApp) -> JourneyResult:
    # Load by file path: importing as `tests.…` can silently resolve to a
    # third-party `tests` package in site-packages (ultralytics ships one).
    spec = importlib.util.spec_from_file_location(f"journey_{name}", JOURNEYS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        result = module.run(app)
    except Exception as exc:  # noqa: BLE001 - a journey crashing is a fail, not a harness bug
        result = JourneyResult.fail(f"unhandled exception: {exc!r}")
    result.name = name
    return result


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run backend feature journeys against a throwaway PotionUI instance.")
    parser.add_argument(
        "journeys", nargs="*", help="Specific journey module names to run (default: every journey discovered)."
    )
    parser.add_argument(
        "--models-dir", default=None, help="Depot to mirror in read-only (default: models/tests)."
    )
    parser.add_argument(
        "--port", type=int, default=None, help="Port for the throwaway backend (default: auto-pick from 8055)."
    )
    parser.add_argument(
        "--keep", action="store_true", help="Leave the instance running and the temp dir on disk after the run."
    )
    args = parser.parse_args(argv)

    names = args.journeys or discover_journeys()
    if not names:
        print("No journeys found under tests/e2e/journeys/", file=sys.stderr)
        return 1

    unknown = [n for n in names if not (JOURNEYS_DIR / f"{n}.py").is_file()]
    if unknown:
        print(f"Unknown journey(s): {unknown}. Available: {discover_journeys()}", file=sys.stderr)
        return 2

    results: List[JourneyResult] = []
    try:
        with ThrowawayApp(models_dir=args.models_dir, port=args.port, keep=args.keep) as app:
            log(f"Throwaway instance up at {app.base_url} (owner={app.username})")
            for name in names:
                log(f"Running journey: {name}")
                result = run_journey(name, app)
                results.append(result)
                for line in result.evidence:
                    log(f"  [{name}] {line}")
                log(f"Journey '{name}' -> {result.status.upper()}")
    except StageError as exc:
        print(f"\nFAILED to boot the throwaway instance at stage [{exc.stage}]: {exc.message}", file=sys.stderr)
        return 1

    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    skipped = sum(1 for r in results if r.status == "skip")
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped ({len(results)} total)")
    for r in results:
        print(f"  [{r.status.upper():4}] {r.name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
