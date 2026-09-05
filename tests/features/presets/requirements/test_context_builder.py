"""`context_builder`: resolving a preset's engine backend(s) into
`RequirementBackendInfo`/`RequirementContext` - in particular, that
`execution_device` is read off the actual resolved backend instance (never
inferred from its driver name) and is the only thing that gates whether a
local VRAM reading applies (see `vram_min_gb`,
`src.features.backends.base_backend.ExecutionDevice`).
"""

import pytest

from src.features.presets.requirements.context_builder import (
    backend_infos_for_engine,
    build_requirement_context,
    build_requirement_context_for_backend,
)
from src.features.presets.requirements.contracts import RequirementBackendInfo
from src.features.presets.templates import PresetTemplate


def _preset(engine="native"):
    return PresetTemplate(id="p1", name="Preset", version="1.0.0", path="/tmp/preset", modes={}, engine=engine)


class _FakeConfig:
    def __init__(self, id, name, engine, driver=None):
        self.id = id
        self.name = name
        self.engine = engine
        self.driver = driver or engine


class _FakeBackend:
    def __init__(self, config, execution_device="unestablished"):
        self.config = config
        self.backend_id = config.id
        self.name = config.name
        self.engine = config.engine
        # Mirrors the real `BaseBackend.execution_device` class attribute -
        # set explicitly per fixture, never derived from `driver`.
        self.execution_device = execution_device


class _FakeConfigStore:
    def __init__(self, configs_by_id, default_id=None):
        self._configs_by_id = configs_by_id
        self.default_id = default_id

    def get_default_backend(self, engine):
        if self.default_id is None:
            return None
        config = self._configs_by_id.get(self.default_id)
        return config if config and config.engine == engine else None


class _FakeRegistry:
    def __init__(self, backends, default_id=None):
        self._backends = backends
        self.backend_config_store = _FakeConfigStore(
            {b.config.id: b.config for b in backends}, default_id=default_id
        )

    def get_backends_for_engine(self, engine):
        return [b for b in self._backends if b.engine == engine]

    def get_backend(self, backend_id):
        return next((b for b in self._backends if b.backend_id == backend_id), None)


class _FakeGpuMonitor:
    def __init__(self, total_vram_mb, available=True):
        self.available = available
        self._total_vram_mb = total_vram_mb

    def get_total_vram(self):
        return self._total_vram_mb


class TestBackendInfosCarryExecutionDevice:
    def test_backend_infos_for_engine_copies_execution_device(self):
        registry = _FakeRegistry([
            _FakeBackend(_FakeConfig("a", "A", "native"), execution_device="this_host_gpu"),
            _FakeBackend(_FakeConfig("b", "B", "native"), execution_device="unestablished"),
        ])

        infos = backend_infos_for_engine(registry, "native")

        by_id = {i.id: i for i in infos}
        assert by_id["a"].execution_device == "this_host_gpu"
        assert by_id["b"].execution_device == "unestablished"

    def test_backend_infos_for_engine_defaults_when_backend_declares_nothing(self):
        """A plain object with no `execution_device` attribute at all (a
        backend class that never overrides `BaseBackend`'s default) must
        read as "unestablished" via the `getattr` fallback, not raise."""

        class _BareBackend:
            def __init__(self, config):
                self.config = config
                self.backend_id = config.id
                self.name = config.name
                self.engine = config.engine

        registry = _FakeRegistry([_BareBackend(_FakeConfig("bare", "Bare", "comfyui"))])

        infos = backend_infos_for_engine(registry, "comfyui")

        assert infos[0].execution_device == "unestablished"

    def test_resolve_backend_copies_the_default_backends_execution_device(self):
        registry = _FakeRegistry(
            [_FakeBackend(_FakeConfig("native-local", "Local", "native"), execution_device="this_host_gpu")],
            default_id="native-local",
        )

        ctx = build_requirement_context(_preset(engine="native"), None, _FakeGpuMonitor(8 * 1024), registry)

        assert ctx.backend.execution_device == "this_host_gpu"
        assert ctx.gpu_total_vram_gb == pytest.approx(8.0)

    def test_resolve_backend_reads_unestablished_when_get_backend_returns_none(self):
        """A config the registry hasn't instantiated yet (or can't) must not
        crash - and must not read as local."""

        class _RegistryWithNoInstance(_FakeRegistry):
            def get_backend(self, backend_id):
                return None

        registry = _RegistryWithNoInstance(
            [_FakeBackend(_FakeConfig("native-local", "Local", "native"), execution_device="this_host_gpu")],
            default_id="native-local",
        )

        ctx = build_requirement_context(_preset(engine="native"), None, _FakeGpuMonitor(8 * 1024), registry)

        assert ctx.backend.execution_device == "unestablished"
        assert ctx.gpu_total_vram_gb is None


class TestBuildRequirementContextForBackendExecutionDeviceGate:
    def test_this_host_gpu_reads_local_vram(self):
        info = RequirementBackendInfo(id="a", engine="native", driver="native", execution_device="this_host_gpu")

        ctx = build_requirement_context_for_backend(_preset(), None, _FakeGpuMonitor(24 * 1024), info)

        assert ctx.gpu_total_vram_gb == pytest.approx(24.0)

    def test_remote_never_reads_local_vram_even_with_a_gpu_available(self):
        info = RequirementBackendInfo(id="a", engine="native", driver="native.remote", execution_device="remote")

        ctx = build_requirement_context_for_backend(_preset(), None, _FakeGpuMonitor(24 * 1024), info)

        assert ctx.gpu_total_vram_gb is None

    def test_unestablished_never_reads_local_vram_regardless_of_driver_name(self):
        """The exact regression this guards: a driver name that doesn't
        contain "remote" (e.g. a plugin's own engine name, like "comfyui")
        used to be read as local by a substring check - only an explicit
        `execution_device` may now grant that."""
        info = RequirementBackendInfo(id="a", engine="comfyui", driver="comfyui", execution_device="unestablished")

        ctx = build_requirement_context_for_backend(_preset(), None, _FakeGpuMonitor(24 * 1024), info)

        assert ctx.gpu_total_vram_gb is None

    def test_no_backend_never_reads_local_vram(self):
        ctx = build_requirement_context_for_backend(_preset(), None, _FakeGpuMonitor(24 * 1024), None)

        assert ctx.gpu_total_vram_gb is None
