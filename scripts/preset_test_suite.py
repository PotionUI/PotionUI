#!/usr/bin/env python3
"""Preset E2E test-suite runner CLI.

Iterates ``presets/**/tests.yml`` and runs each declared case as a REAL
generation through the same orchestrator a UI request uses, then writes the
outputs + a static HTML gallery for eyeballing.

    python scripts/preset_test_suite.py --list
    python scripts/preset_test_suite.py --preset native/SDXL/realistic --tag fast
    python scripts/preset_test_suite.py --allow-download --output-dir test-runs

Exit code is nonzero when any case FAILED (skips do not fail the run).

NOTE: a real run loads models and generates on the GPU. During development this
is never invoked automatically — the whole runner/checks/report stack is
unit-tested with a mocked generation layer; the first real run is the user's.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

# Make `src` importable when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.preset_suite.models import FAIL, PASS, SKIP  # noqa: E402
from src.features.preset_suite.runner import PresetSuiteRunner  # noqa: E402


def _build_resolver(allow_download: bool):
    """Build the model resolver from the LIVE database, read-only, BEFORE the
    client re-points the DB at its ephemeral copy. We snapshot the whole models
    table into an in-memory ``sha256 -> file_path`` index here so that during the
    (ephemeral-DB) run, model lookups are instant and never fall back to hashing
    the models tree — which is symlinked to a multi-hundred-GB store on this host.
    This is the one live-DB read the ephemeral design permits: read-only, purely to
    LOCATE model files."""
    from src.features.preset_suite.resolver import ModelResolver
    from src.platform.settings.settings import SettingsManager
    from src.features.models.repository import ModelRepository
    from src.platform.settings.repository import SettingRepository

    settings_manager = SettingsManager(SettingRepository())
    models_dir = settings_manager.get_models_dir()
    repo = ModelRepository()

    sha_index: dict = {}
    try:
        for m in repo.get_all(include_providers=False, include_tags=False):
            sha = (getattr(m, "sha256", None) or "").strip().lower()
            file_path = getattr(m, "file_path", None)
            if sha and file_path:
                sha_index[sha] = file_path
    except Exception as e:  # noqa: BLE001 - a snapshot miss just falls back to hash-walk
        print(f"warning: could not snapshot the live models index ({e}); "
              "model resolution will fall back to hashing the models tree.")

    # A missing model is fetched through the real download queue (same manager
    # the admin UI uses) rather than hitting HuggingFace directly, so the fetch
    # shows up in the admin download history and honors the configured depot -
    # only constructed when a case might actually need to download something.
    download_manager = None
    if allow_download:
        from src.features.downloads.manager import DownloadManager
        from src.features.downloads.repository import DownloadRepository
        from src.platform.plugins import PluginRegistry
        from src.platform.websocket.download_connection_manager import DownloadConnectionManager

        download_manager = DownloadManager(
            download_repository=DownloadRepository(),
            plugin_registry=PluginRegistry(),
            settings_manager=settings_manager,
            connection_manager=DownloadConnectionManager(),
        )

    return ModelResolver(
        models_dir,
        model_repository=repo,
        sha_index=sha_index,
        cache_path=Path("storage") / "model_hash_cache.json",
        allow_download=allow_download,
        download_manager=download_manager,
    )


def _list(runner: PresetSuiteRunner, preset: str | None, tag: str | None) -> int:
    n = 0
    current = None
    for _dir, preset_id, case in runner.iter_cases(preset, tag):
        if preset_id != current:
            print(f"\n{preset_id}")
            current = preset_id
        tags = ",".join(getattr(case, "tags", None) or [])
        print(f"  - {getattr(case, 'name', 'unnamed')}  [{tags}]  mode={getattr(case, 'mode', 'txt2img')}")
        n += 1
    print(f"\n{n} case(s) discovered.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the preset E2E test suite.")
    ap.add_argument("--preset", help="Only this preset id (e.g. native/SDXL/realistic).")
    ap.add_argument("--tag", help="Only cases carrying this tag (e.g. fast).")
    ap.add_argument("--allow-download", action="store_true",
                    help="Download missing models from HuggingFace when a case declares an hf ref.")
    ap.add_argument("--list", action="store_true", help="List discovered cases + tags and exit (no generation).")
    ap.add_argument("--output-dir", default="test-runs", help="Where to write the run output (default: test-runs).")
    ap.add_argument("--keep-db", action="store_true",
                    help="Keep the ephemeral per-run DB + storage after the run "
                         "(default: delete on success; ALWAYS kept when a case fails).")
    args = ap.parse_args(argv)

    if args.list:
        # Discovery only — no injector/DB/GPU boot.
        runner = PresetSuiteRunner(client=None, resolver=None)  # type: ignore[arg-type]
        return _list(runner, args.preset, args.tag)

    from src.features.preset_suite import ephemeral
    from src.features.preset_suite.report import write_run
    from src.features.preset_suite.runner import HeadlessGenerationClient

    # Create + MARK the run dir up front: the ephemeral DB (<run_dir>/suite.db) and
    # isolated image storage (<run_dir>/storage) live inside it, and the marker is
    # what authorises cleanup to remove them (never touches the repo's storage/).
    run_dir = Path(args.output_dir) / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    ephemeral.mark(run_dir)

    # The resolver reads the REAL models_dir (read-only) to LOCATE model files on
    # disk; this is the one live-DB read the design permits (see task notes). The
    # client then re-points the DB singleton at the ephemeral DB for the whole run.
    resolver = _build_resolver(args.allow_download)
    from src.bootstrap.container import build_container

    client = HeadlessGenerationClient(build_container, run_dir=run_dir)
    runner = PresetSuiteRunner(client=client, resolver=resolver)

    results: list = []
    try:
        results = runner.run(args.preset, args.tag)
    finally:
        # The report/gallery MUST always be written — even on crash/timeout so the
        # user always has something to inspect.
        index = write_run(run_dir, results)
        print(f"\nGallery: {index}")

    n_pass = sum(1 for r in results if r.verdict == PASS)
    n_fail = sum(1 for r in results if r.verdict == FAIL)
    n_skip = sum(1 for r in results if r.verdict == SKIP)
    print(f"\n{len(results)} case(s): {n_pass} PASS, {n_fail} FAIL, {n_skip} SKIP")
    for r in results:
        if r.verdict != PASS:
            print(f"  {r.verdict}  {r.preset_id}/{r.case_name}: {r.reason}")

    # Tear down the ephemeral DB + storage on success; retain on any failure (for
    # debugging) or when --keep-db. Only ever removes suite-created, marked paths.
    ephemeral.cleanup(
        run_dir,
        [run_dir / "suite.db", run_dir / "suite.db-wal", run_dir / "suite.db-shm",
         run_dir / "storage"],
        keep=args.keep_db, failed=(n_fail > 0),
    )
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
