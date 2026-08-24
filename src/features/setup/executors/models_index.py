"""`models.index` - rescan the models directory so files that are already on
disk (e.g. dropped in manually, or left over from a previous install) become
visible before `preset.ensure`/`pipeline.render` look for them.

Uses the same filesystem `ModelScanner` singleton the manual "reindex" action
elsewhere in the app uses (`src.features.models.indexer.model_scanner`) -
this is a full directory scan, independent of any particular backend/engine,
so it needs no async backend round-trip (contrast with the per-backend
`BackendModelIndexer` in `src.features.models.backend_indexer`, which asks a
specific backend what it can load).

A large models folder makes `index_models()` (it hashes every new file's full
contents - see `ModelScanner.index_single_model`) take much longer than a
request can wait on - this step is only safe to run because
`SetupRunManager.drive_async` drives it off the request thread (see
`run_manager.py`). This executor wires the scanner's
`set_progress_callback` seam to `StepContext.report_progress` so the
in-flight attempt row shows "N of M" files while the scan runs, throttled to
roughly once a second (matching `artifacts_fetch.py`'s cadence) so a huge
library doesn't turn into one DB write per file.
"""

from __future__ import annotations

import time

from src.features.setup.executors.base import StepContext, StepResult

# Once-a-second throttle for progress writes - `index_single_model` reports
# per completed file, which for a huge library would otherwise be one DB
# write per file (see module docstring).
_PROGRESS_MIN_INTERVAL_SECONDS = 1.0


class ModelsIndexExecutor:
    def __init__(self, model_scanner=None):
        if model_scanner is None:
            from src.features.models.indexer import model_scanner as _default_scanner

            model_scanner = _default_scanner
        self.model_scanner = model_scanner

    def execute(self, context: StepContext) -> StepResult:
        set_progress_callback = getattr(self.model_scanner, "set_progress_callback", None)
        if callable(set_progress_callback):
            set_progress_callback(self._progress_reporter(context))

        try:
            result = self.model_scanner.index_models()
        except Exception as exc:
            return StepResult.fail(
                "MODEL_INDEX_FAILED",
                f"Scanning for model files failed: {exc}",
                suggested_repair="Check that the models directory exists and is readable, then retry.",
            )
        finally:
            if callable(set_progress_callback):
                set_progress_callback(None)

        return StepResult.ok(
            {
                "indexed": result.get("indexed", 0),
                "skipped": result.get("skipped", 0),
                "failed": result.get("failed", 0),
                "total": result.get("total", 0),
            }
        )

    @staticmethod
    def _progress_reporter(context: StepContext):
        """A `ModelScanner.set_progress_callback` callback bound to this
        step's `report_progress`, throttled so it isn't a DB write per file.
        The very first tick (`current == 0`, the "Scanning..." message before
        the file count is even known - see `ModelScanner.index_models`) and
        the terminal tick (`current == total`) always get through, so the row
        never gets stuck showing a stale mid-run count."""
        last_reported_at = {"t": 0.0}

        def _report(current: int, total: int, message: str) -> None:
            now = time.monotonic()
            is_edge = current == 0 or (total and current >= total)
            if not is_edge and (now - last_reported_at["t"]) < _PROGRESS_MIN_INTERVAL_SECONDS:
                return
            last_reported_at["t"] = now
            context.report_progress(
                progress_current=current,
                progress_total=total or None,
                progress_unit="files",
            )

        return _report
