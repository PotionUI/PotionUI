from __future__ import annotations

import time

from src.features.recipes.executors.base import StepContext, StepResult

_PROGRESS_MIN_INTERVAL_SECONDS = 1.0


class ModelsIndexExecutor:
    def __init__(self, indexing_coordinator):
        self.indexing_coordinator = indexing_coordinator

    def execute(self, context: StepContext) -> StepResult:
        try:
            result = self.indexing_coordinator.run_and_wait(on_progress=self._progress_reporter(context))
        except Exception as exc:
            return StepResult.fail(
                "MODEL_INDEX_FAILED",
                "Looking for your model files failed.",
                technical_detail=f"{type(exc).__name__}: {exc}",
                suggested_repair="Check that the models directory exists and is readable, then retry.",
            )

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
