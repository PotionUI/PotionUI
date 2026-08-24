"""`backend.ensure` executor against a fake BackendRegistry surface."""

from types import SimpleNamespace

from src.features.setup.executors.backend_ensure import BackendEnsureExecutor
from src.features.setup.executors.base import StepContext
from src.features.setup.recipe_schema import Recipe, RecipeStep
from src.features.setup.records import SetupRun, SetupRunStatus


class FakeBackendRegistry:
    def __init__(self, backends_by_engine=None):
        self.backends_by_engine = backends_by_engine or {}

    def get_backends_for_engine(self, engine):
        return self.backends_by_engine.get(engine, [])


def _context(engine="native"):
    run = SetupRun(id="r1", recipe_id="x", recipe_version=1, scope="instance", status=SetupRunStatus.RUNNING)
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="native")
    step = RecipeStep(key="backend.ensure", kind="backend.ensure", title="Prepare the backend", params={"engine": engine})
    return StepContext(run=run, recipe=recipe, step=step)


def test_existing_backend_succeeds():
    backend = SimpleNamespace(backend_id="native", name="Local Generation")
    registry = FakeBackendRegistry({"native": [backend]})
    executor = BackendEnsureExecutor(registry)

    result = executor.execute(_context("native"))

    assert result.success is True
    assert result.safe_output == {"engine": "native", "backend_id": "native", "backend_name": "Local Generation"}


def test_no_backend_for_engine_fails_with_repair_hint():
    registry = FakeBackendRegistry({})
    executor = BackendEnsureExecutor(registry)

    result = executor.execute(_context("comfyui"))

    assert result.success is False
    assert result.error_code == "NO_BACKEND_FOR_ENGINE"
    assert "comfyui" in result.safe_error_detail
    assert "Administration" in result.suggested_repair


def test_falls_back_to_recipe_engine_when_step_omits_it():
    backend = SimpleNamespace(backend_id="native", name="Local Generation")
    registry = FakeBackendRegistry({"native": [backend]})
    executor = BackendEnsureExecutor(registry)

    run = SetupRun(id="r1", recipe_id="x", recipe_version=1, scope="instance", status=SetupRunStatus.RUNNING)
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="native")
    step = RecipeStep(key="backend.ensure", kind="backend.ensure", title="Prepare the backend", params={})
    result = executor.execute(StepContext(run=run, recipe=recipe, step=step))

    assert result.success is True
