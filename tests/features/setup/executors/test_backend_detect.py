"""`backend.detect` against a fake `BackendRegistry` - never a real network
probe. Exercises: engine plugin not enabled, unreachable server, and a
reachable server being registered as a backend (create and update paths)."""

from src.features.setup.executors.backend_detect import BackendDetectExecutor
from src.features.setup.executors.base import StepContext
from src.features.setup.recipe_schema import Recipe, RecipeStep
from src.features.setup.records import SetupRun, SetupRunStatus


class FakeConfig:
    """Stands in for a pydantic `BaseBackendConfig` subclass without pydantic:
    constructible from kwargs, dumps back to a plain dict, and exposes the
    `host`/`port` defaults `_target()` falls back to."""

    model_fields = {"host": type("F", (), {"default": "127.0.0.1"})(), "port": type("F", (), {"default": 8188})()}

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def model_dump(self):
        return dict(self.__dict__)


class FakeBackendInstance:
    def __init__(self, config, health):
        self.config = config
        self._health = health

    async def health_check(self):
        return self._health


class FakeBackendConfigStore:
    def __init__(self, existing=None):
        self.existing = existing  # list of FakeConfig for the engine
        self.added = []
        self.updated = []

    def get_backends_for_engine(self, engine):
        return self.existing or []

    def validate_backend_config(self, data):
        return FakeConfig(**data)

    def add_backend(self, config):
        self.added.append(config)

    def update_backend(self, backend_id, config):
        self.updated.append((backend_id, config))


class FakeBackendRegistry:
    def __init__(self, engine="comfyui", registered=True, health=None, existing=None):
        self._registered = registered
        self._health = health or {"status": "available"}
        self.backend_config_store = FakeBackendConfigStore(existing=existing)
        self.refreshed = False
        self.engine = engine

    def get_registered_config_types(self):
        return {self.engine: FakeConfig} if self._registered else {}

    def get_registered_backend_types(self):
        return {self.engine: FakeBackendInstance} if self._registered else {}

    def _create_backend_instance(self, config):
        return FakeBackendInstance(config, self._health)

    async def refresh_backends(self):
        self.refreshed = True


def _context(base_url=None):
    run = SetupRun(id="r1", recipe_id="x", recipe_version=1, scope="instance", status=SetupRunStatus.RUNNING)
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="comfyui")
    params = {"engine": "comfyui"}
    if base_url:
        params["base_url"] = base_url
    step = RecipeStep(key="backend.detect", kind="backend.detect", title="Detect", params=params)
    return StepContext(run=run, recipe=recipe, step=step)


def test_engine_plugin_not_enabled_fails_clearly():
    registry = FakeBackendRegistry(registered=False)
    executor = BackendDetectExecutor(registry)

    result = executor.execute(_context())

    assert result.success is False
    assert result.error_code == "ENGINE_PLUGIN_NOT_ENABLED"


def test_unreachable_server_fails_with_url_in_message():
    registry = FakeBackendRegistry(health={"status": "offline", "error": "connection refused"})
    executor = BackendDetectExecutor(registry)

    result = executor.execute(_context())

    assert result.success is False
    assert result.error_code == "BACKEND_UNREACHABLE"
    assert "127.0.0.1:8188" in result.safe_error_detail


def test_reachable_server_creates_a_new_backend():
    registry = FakeBackendRegistry(health={"status": "available"}, existing=None)
    executor = BackendDetectExecutor(registry)

    result = executor.execute(_context())

    assert result.success is True
    assert result.safe_output["created"] is True
    assert len(registry.backend_config_store.added) == 1
    assert registry.refreshed is True


def test_reachable_server_updates_an_existing_backend():
    existing = FakeConfig(id="be-1", name="ComfyUI", engine="comfyui", host="127.0.0.1", port=8188, enabled=True)
    registry = FakeBackendRegistry(health={"status": "available"}, existing=[existing])
    executor = BackendDetectExecutor(registry)

    result = executor.execute(_context())

    assert result.success is True
    assert result.safe_output["created"] is False
    assert len(registry.backend_config_store.updated) == 1
    assert registry.backend_config_store.updated[0][0] == "be-1"


def test_explicit_base_url_overrides_default():
    registry = FakeBackendRegistry(health={"status": "available"})
    executor = BackendDetectExecutor(registry)

    result = executor.execute(_context(base_url="http://192.168.1.50:8189"))

    assert result.success is True
    assert result.safe_output["host"] == "192.168.1.50"
    assert result.safe_output["port"] == 8189
