"""`models.index_backend` against fake BackendRegistry/BackendModelIndexer
surfaces."""

from types import SimpleNamespace

from src.features.setup.executors.base import StepContext
from src.features.setup.executors.models_index_backend import ModelsIndexBackendExecutor
from src.features.setup.recipe_schema import Recipe, RecipeStep
from src.features.setup.records import SetupRun, SetupRunStatus


class FakeBackendRegistry:
    def __init__(self, backends_by_engine=None):
        self.backends_by_engine = backends_by_engine or {}

    def get_backends_for_engine(self, engine):
        return self.backends_by_engine.get(engine, [])


class FakeIndexResult:
    def to_dict(self):
        return {"backend_id": "be-1", "listed": 3, "created": 2, "matched": 1, "removed": 0}


class FakeBackendModelIndexer:
    def __init__(self, result=None, raises=None):
        self.result = result or FakeIndexResult()
        self.raises = raises
        self.calls = []

    async def index_backend(self, backend):
        self.calls.append(backend)
        if self.raises:
            raise self.raises
        return self.result


def _context(engine="comfyui"):
    run = SetupRun(id="r1", recipe_id="x", recipe_version=1, scope="instance", status=SetupRunStatus.RUNNING)
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="comfyui")
    step = RecipeStep(key="models.index_backend", kind="models.index_backend", title="Index", params={"engine": engine})
    return StepContext(run=run, recipe=recipe, step=step)


def test_indexes_the_backend_and_reports_counts():
    backend = SimpleNamespace(backend_id="be-1", name="ComfyUI")
    registry = FakeBackendRegistry({"comfyui": [backend]})
    indexer = FakeBackendModelIndexer()
    executor = ModelsIndexBackendExecutor(registry, indexer)

    result = executor.execute(_context())

    assert result.success is True
    assert result.safe_output["listed"] == 3
    assert indexer.calls == [backend]


def test_no_backend_for_engine_fails_with_repair_hint():
    registry = FakeBackendRegistry({})
    executor = ModelsIndexBackendExecutor(registry, FakeBackendModelIndexer())

    result = executor.execute(_context())

    assert result.success is False
    assert result.error_code == "NO_BACKEND_FOR_ENGINE"


def test_indexer_failure_reported_cleanly():
    backend = SimpleNamespace(backend_id="be-1", name="ComfyUI")
    registry = FakeBackendRegistry({"comfyui": [backend]})
    indexer = FakeBackendModelIndexer(raises=RuntimeError("connection reset"))
    executor = ModelsIndexBackendExecutor(registry, indexer)

    result = executor.execute(_context())

    assert result.success is False
    assert result.error_code == "MODEL_INDEX_FAILED"
    assert "connection reset" in result.safe_error_detail
