"""`models.index` executor against a fake ModelScanner (no real filesystem
scan or DB needed)."""

from src.features.setup.executors.base import StepContext
from src.features.setup.executors.models_index import ModelsIndexExecutor
from src.features.setup.recipe_schema import Recipe, RecipeStep
from src.features.setup.records import SetupRun, SetupRunStatus


class FakeScanner:
    def __init__(self, result=None, raises=None, progress_ticks=()):
        self.result = result or {}
        self.raises = raises
        self.calls = 0
        # Ticks a real `ModelScanner.index_models()` would emit via its own
        # `_report_progress` (see src/features/models/indexer.py) - a fake
        # scanner that wants to exercise the executor's progress wiring
        # passes some; one that doesn't (most tests here) just has none, and
        # `set_progress_callback` is simply never invoked.
        self._progress_ticks = progress_ticks
        self._progress_callback = None

    def set_progress_callback(self, callback):
        self._progress_callback = callback

    def index_models(self):
        self.calls += 1
        if self.raises:
            raise self.raises
        if self._progress_callback is not None:
            for current, total, message in self._progress_ticks:
                self._progress_callback(current, total, message)
        return self.result


class ScannerWithoutProgressSupport:
    """No `set_progress_callback` at all - proves the executor degrades
    gracefully against any scanner-like object, not just the real one."""

    def __init__(self, result=None):
        self.result = result or {}

    def index_models(self):
        return self.result


def _context(report_progress=None):
    run = SetupRun(id="r1", recipe_id="x", recipe_version=1, scope="instance", status=SetupRunStatus.RUNNING)
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="native")
    step = RecipeStep(key="models.index", kind="models.index", title="Index local models", params={"engine": "native"})
    kwargs = {}
    if report_progress is not None:
        kwargs["report_progress"] = report_progress
    return StepContext(run=run, recipe=recipe, step=step, **kwargs)


def test_reports_counts_from_scan():
    scanner = FakeScanner(result={"indexed": 2, "skipped": 5, "failed": 0, "total": 7, "models": [{"huge": "blob"}]})
    executor = ModelsIndexExecutor(scanner)

    result = executor.execute(_context())

    assert result.success is True
    assert result.safe_output == {"indexed": 2, "skipped": 5, "failed": 0, "total": 7}
    assert scanner.calls == 1


def test_zero_models_found_is_still_success():
    scanner = FakeScanner(result={"indexed": 0, "skipped": 0, "failed": 0, "total": 0})
    executor = ModelsIndexExecutor(scanner)

    result = executor.execute(_context())

    assert result.success is True
    assert result.safe_output["total"] == 0


def test_scanner_exception_is_reported_as_a_clear_failure():
    scanner = FakeScanner(raises=OSError("permission denied"))
    executor = ModelsIndexExecutor(scanner)

    result = executor.execute(_context())

    assert result.success is False
    assert result.error_code == "MODEL_INDEX_FAILED"
    assert "permission denied" in result.safe_error_detail
    assert result.suggested_repair is not None


# --- files-scanned progress reporting -------------------------------


def test_progress_ticks_reach_report_progress_as_files_counts():
    calls = []

    def _report_progress(progress_current=None, progress_total=None, progress_unit=None):
        calls.append((progress_current, progress_total, progress_unit))

    # Mirrors what the real `ModelScanner.index_models()` emits: a leading
    # "0 of N" tick once the new-file count is known, then one tick per
    # completed file (see src/features/models/indexer.py).
    ticks = [(0, 5, "Looking through your models folder..."), (1, 5, "a"), (2, 5, "b"), (3, 5, "c"), (4, 5, "d"), (5, 5, "e")]
    scanner = FakeScanner(result={"indexed": 5, "skipped": 0, "failed": 0, "total": 5}, progress_ticks=ticks)
    executor = ModelsIndexExecutor(scanner)

    result = executor.execute(_context(report_progress=_report_progress))

    assert result.success is True
    # Throttled to roughly once a second (see `_progress_reporter`) - a fast
    # test fires every tick within the same instant, so only the two edges
    # (the first "0 of N" tick, and the terminal "N of N" tick) are
    # guaranteed through; nothing in between is required to reach the DB.
    assert calls[0] == (0, 5, "files")
    assert calls[-1] == (5, 5, "files")
    assert 2 <= len(calls) <= len(ticks)
    # The callback is unwired again once the step is done, so it can never
    # fire against a step that has already moved on.
    assert scanner._progress_callback is None


def test_scanner_without_progress_support_still_works():
    """Not every scanner-like object implements `set_progress_callback` (a
    test double, or a future alternate scanner) - the executor must degrade
    gracefully rather than assume it's always there."""
    scanner = ScannerWithoutProgressSupport(result={"indexed": 1, "skipped": 0, "failed": 0, "total": 1})
    executor = ModelsIndexExecutor(scanner)

    result = executor.execute(_context())

    assert result.success is True
    assert result.safe_output["indexed"] == 1
