from unittest.mock import MagicMock

from src.features.models.exceptions import ModelIndexingException
from src.features.models.indexing_coordinator import ModelIndexingCoordinator
from src.features.recipes.executors.base import StepContext
from src.features.recipes.executors.models_index import ModelsIndexExecutor
from src.features.recipes.schema import Recipe, RecipeStep
from src.features.recipes.records import RecipeRun, RecipeRunStatus
from tests.features.models.fakes import OpenPlugins, ProgressScanner


class FakeCoordinator:
    def __init__(self, result=None, raises=None, progress_ticks=()):
        self.result = result or {}
        self.raises = raises
        self.calls = 0
        self._progress_ticks = progress_ticks

    def run_and_wait(self, on_progress=None, trigger="recipe", timeout=None):
        self.calls += 1
        if self.raises:
            raise self.raises
        if on_progress is not None:
            for current, total, message in self._progress_ticks:
                on_progress(current, total, message)
        return self.result


def _context(report_progress=None):
    run = RecipeRun(id="r1", recipe_id="x", recipe_version=1, scope="instance", status=RecipeRunStatus.RUNNING)
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="native")
    step = RecipeStep(key="models.index", kind="models.index", title="Index local models", params={"engine": "native"})
    kwargs = {}
    if report_progress is not None:
        kwargs["report_progress"] = report_progress
    return StepContext(run=run, recipe=recipe, step=step, **kwargs)


def test_reports_counts_from_the_pass():
    coordinator = FakeCoordinator(result={"indexed": 2, "skipped": 5, "failed": 0, "total": 7, "models": [{"huge": "blob"}]})
    executor = ModelsIndexExecutor(coordinator)

    result = executor.execute(_context())

    assert result.success is True
    assert result.safe_output == {"indexed": 2, "skipped": 5, "failed": 0, "total": 7}
    assert coordinator.calls == 1


def test_zero_models_found_is_still_success():
    executor = ModelsIndexExecutor(FakeCoordinator(result={"indexed": 0, "skipped": 0, "failed": 0, "total": 0}))

    result = executor.execute(_context())

    assert result.success is True
    assert result.safe_output["total"] == 0


def test_a_failed_pass_is_reported_as_a_clear_failure():
    executor = ModelsIndexExecutor(FakeCoordinator(raises=OSError("permission denied")))

    result = executor.execute(_context())

    assert result.success is False
    assert result.error_code == "MODEL_INDEX_FAILED"
    assert "permission denied" not in result.safe_error_detail
    assert "permission denied" in result.technical_detail
    assert result.suggested_repair is not None


def test_a_plugin_blocked_pass_fails_the_step_with_the_reason():
    executor = ModelsIndexExecutor(FakeCoordinator(raises=ModelIndexingException("Indexing blocked by plugin")))

    result = executor.execute(_context())

    assert result.success is False
    assert result.error_code == "MODEL_INDEX_FAILED"
    assert "Indexing blocked by plugin" not in result.safe_error_detail
    assert "Indexing blocked by plugin" in result.technical_detail


def test_progress_ticks_reach_report_progress_as_file_counts():
    calls = []

    def _report_progress(progress_current=None, progress_total=None, progress_unit=None):
        calls.append((progress_current, progress_total, progress_unit))

    ticks = [(0, 5, "Looking through your models folder..."), (1, 5, "a"), (2, 5, "b"), (3, 5, "c"), (4, 5, "d"), (5, 5, "e")]
    coordinator = FakeCoordinator(result={"indexed": 5, "skipped": 0, "failed": 0, "total": 5}, progress_ticks=ticks)
    executor = ModelsIndexExecutor(coordinator)

    result = executor.execute(_context(report_progress=_report_progress))

    assert result.success is True
    assert calls[0] == (0, 5, "files")
    assert calls[-1] == (5, 5, "files")
    assert 2 <= len(calls) <= len(ticks)


def test_the_step_scans_through_the_real_coordinator_and_leaves_its_state_consistent():
    scanner = ProgressScanner(
        result={"indexed": 1, "skipped": 0, "failed": 0, "total": 1, "found_on_disk": 1, "failed_files": [], "cancelled": False},
        ticks=((1, 1, "model.safetensors"),),
    )
    coordinator = ModelIndexingCoordinator(
        spawn=lambda target: target(),
        model_repository=MagicMock(),
        plugin_registry=OpenPlugins(),
        scanner=scanner,
    )
    calls = []

    result = ModelsIndexExecutor(coordinator).execute(
        _context(report_progress=lambda **kwargs: calls.append(kwargs))
    )

    assert result.success is True
    assert result.safe_output["indexed"] == 1
    assert scanner.calls == 1
    assert scanner.callback is None
    assert coordinator.status()["state"] == "done"
    assert calls[-1]["progress_unit"] == "files"
