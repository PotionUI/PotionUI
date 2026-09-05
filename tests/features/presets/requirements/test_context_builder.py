"""`context_builder`: resolving a preset's engine backend(s) into
`RequirementBackendInfo`/`RequirementContext` - in particular, that
`execution_device` carries a backend INSTANCE's own `ExecutionDeviceEvidence`
(never inferred from its driver name, and never just "a GPU exists on this
host") and is the only thing that gates whether a local VRAM reading
applies. A `NativeBackend` can be configured for any `cuda:N`, and this
process's one `GpuMonitor` is bound to exactly one index - a reading must
never be attributed to a backend resolved to a DIFFERENT index (see
`src.features.backends.base_backend.ExecutionDeviceEvidence`).
"""

import pytest

from src.features.backends.backend_config import NativeBackendConfig
from src.features.backends.base_backend import ExecutionDeviceEvidence
from src.features.backends.native_backend import NativeBackend
from src.features.presets.requirements.context_builder import (
    backend_infos_for_engine,
    build_requirement_context,
    build_requirement_context_for_backend,
)
from src.features.presets.requirements.contracts import RequirementBackendInfo
from src.features.presets.templates import PresetTemplate


def _preset(engine="native"):
    return PresetTemplate(id="p1", name="Preset", version="1.0.0", path="/tmp/preset", modes={}, engine=engine)


def _native_backend(id, device):
    """A REAL `NativeBackend` over a REAL `NativeBackendConfig` - every
    field that would otherwise probe this host's hardware
    (`detect_native_hardware_defaults`) is pinned explicitly so the fixture
    is deterministic and never touches torch/CUDA."""
    config = NativeBackendConfig(id=id, name=id, device=device, dtype="float16", gpu_max_vram=0)
    return NativeBackend(backend_config=config)


class _FakeConfig:
    def __init__(self, id, name, engine, driver=None):
        self.id = id
        self.name = name
        self.engine = engine
        self.driver = driver or engine


class _FakeBackend:
    def __init__(self, config, execution_device=None):
        self.config = config
        self.backend_id = config.id
        self.name = config.name
        self.engine = config.engine
        self._execution_device = execution_device or ExecutionDeviceEvidence(kind="unestablished")

    def resolve_execution_device(self) -> ExecutionDeviceEvidence:
        # Mirrors the real `BaseBackend.resolve_execution_device()` - set
        # explicitly per fixture, never derived from `driver`.
        return self._execution_device


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
    def __init__(self, total_vram_mb, available=True, device_index=0):
        self.available = available
        self._total_vram_mb = total_vram_mb
        self.device_index = device_index

    def get_total_vram(self):
        return self._total_vram_mb


class TestBackendInfosCarryExecutionDevice:
    def test_backend_infos_for_engine_copies_execution_device(self):
        registry = _FakeRegistry([
            _FakeBackend(_FakeConfig("a", "A", "native"), ExecutionDeviceEvidence(kind="this_host_gpu", gpu_index=0)),
            _FakeBackend(_FakeConfig("b", "B", "native"), ExecutionDeviceEvidence(kind="unestablished")),
        ])

        infos = backend_infos_for_engine(registry, "native")

        by_id = {i.id: i for i in infos}
        assert by_id["a"].execution_device == ExecutionDeviceEvidence(kind="this_host_gpu", gpu_index=0)
        assert by_id["b"].execution_device == ExecutionDeviceEvidence(kind="unestablished")

    def test_backend_infos_for_engine_defaults_when_backend_declares_nothing(self):
        """A plain object with no `resolve_execution_device` method at all
        (a backend class that never overrides `BaseBackend`'s default) must
        read as "unestablished" via the `getattr` fallback, not raise."""

        class _BareBackend:
            def __init__(self, config):
                self.config = config
                self.backend_id = config.id
                self.name = config.name
                self.engine = config.engine

        registry = _FakeRegistry([_BareBackend(_FakeConfig("bare", "Bare", "comfyui"))])

        infos = backend_infos_for_engine(registry, "comfyui")

        assert infos[0].execution_device == ExecutionDeviceEvidence(kind="unestablished")

    def test_resolve_backend_copies_the_default_backends_execution_device(self):
        registry = _FakeRegistry(
            [_FakeBackend(
                _FakeConfig("native-local", "Local", "native"),
                ExecutionDeviceEvidence(kind="this_host_gpu", gpu_index=0),
            )],
            default_id="native-local",
        )

        ctx = build_requirement_context(_preset(engine="native"), None, _FakeGpuMonitor(8 * 1024), registry)

        assert ctx.backend.execution_device == ExecutionDeviceEvidence(kind="this_host_gpu", gpu_index=0)
        assert ctx.gpu_total_vram_gb == pytest.approx(8.0)

    def test_resolve_backend_reads_unestablished_when_get_backend_returns_none(self):
        """A config the registry hasn't instantiated yet (or can't) must not
        crash - and must not read as local."""

        class _RegistryWithNoInstance(_FakeRegistry):
            def get_backend(self, backend_id):
                return None

        registry = _RegistryWithNoInstance(
            [_FakeBackend(
                _FakeConfig("native-local", "Local", "native"),
                ExecutionDeviceEvidence(kind="this_host_gpu", gpu_index=0),
            )],
            default_id="native-local",
        )

        ctx = build_requirement_context(_preset(engine="native"), None, _FakeGpuMonitor(8 * 1024), registry)

        assert ctx.backend.execution_device == ExecutionDeviceEvidence(kind="unestablished")
        assert ctx.gpu_total_vram_gb is None


class TestBuildRequirementContextForBackendExecutionDeviceGate:
    def test_this_host_gpu_reads_local_vram_when_index_matches_the_monitor(self):
        info = RequirementBackendInfo(
            id="a", engine="native", driver="native",
            execution_device=ExecutionDeviceEvidence(kind="this_host_gpu", gpu_index=0),
        )

        ctx = build_requirement_context_for_backend(_preset(), None, _FakeGpuMonitor(24 * 1024, device_index=0), info)

        assert ctx.gpu_total_vram_gb == pytest.approx(24.0)

    def test_this_host_gpu_reads_unknown_when_the_monitors_index_differs(self):
        """The device-identity gap this rework closes: a backend resolved
        to GPU 1 must never read a monitor bound to GPU 0's total, even
        though both are genuinely "this host's GPU"."""
        info = RequirementBackendInfo(
            id="a", engine="native", driver="native",
            execution_device=ExecutionDeviceEvidence(kind="this_host_gpu", gpu_index=1),
        )

        ctx = build_requirement_context_for_backend(_preset(), None, _FakeGpuMonitor(24 * 1024, device_index=0), info)

        assert ctx.gpu_total_vram_gb is None

    def test_no_gpu_never_reads_local_vram_even_with_a_gpu_available(self):
        """A NativeBackend explicitly configured with `device="cpu"` (see
        `NativeBackend.resolve_execution_device`) must not borrow this
        host's GPU reading just because one happens to exist."""
        info = RequirementBackendInfo(
            id="a", engine="native", driver="native", execution_device=ExecutionDeviceEvidence(kind="no_gpu"),
        )

        ctx = build_requirement_context_for_backend(_preset(), None, _FakeGpuMonitor(24 * 1024), info)

        assert ctx.gpu_total_vram_gb is None

    def test_remote_never_reads_local_vram_even_with_a_gpu_available(self):
        info = RequirementBackendInfo(
            id="a", engine="native", driver="native.remote", execution_device=ExecutionDeviceEvidence(kind="remote"),
        )

        ctx = build_requirement_context_for_backend(_preset(), None, _FakeGpuMonitor(24 * 1024), info)

        assert ctx.gpu_total_vram_gb is None

    def test_unestablished_never_reads_local_vram_regardless_of_driver_name(self):
        """A driver name that doesn't contain "remote" (e.g. a plugin's own
        engine name, like "comfyui") must never be read as local - only an
        explicit `ExecutionDeviceEvidence` may grant that."""
        info = RequirementBackendInfo(
            id="a", engine="comfyui", driver="comfyui", execution_device=ExecutionDeviceEvidence(kind="unestablished"),
        )

        ctx = build_requirement_context_for_backend(_preset(), None, _FakeGpuMonitor(24 * 1024), info)

        assert ctx.gpu_total_vram_gb is None

    def test_no_backend_never_reads_local_vram(self):
        ctx = build_requirement_context_for_backend(_preset(), None, _FakeGpuMonitor(24 * 1024), None)

        assert ctx.gpu_total_vram_gb is None


class TestNativeBackendRealInstanceDeviceIdentity:
    """REAL `NativeBackend`/`NativeBackendConfig` instances (not a fake) -
    proves `resolve_execution_device()` reads the instance's own configured
    device, and that the context builder only trusts a GPU reading bound to
    that SAME index."""

    def test_bare_cuda_device_string_resolves_to_index_0(self):
        backend = _native_backend("gpu0-backend", "cuda")

        assert backend.resolve_execution_device() == ExecutionDeviceEvidence(kind="this_host_gpu", gpu_index=0)

    def test_cuda_colon_n_resolves_to_that_index(self):
        backend = _native_backend("gpu1-backend", "cuda:1")

        assert backend.resolve_execution_device() == ExecutionDeviceEvidence(kind="this_host_gpu", gpu_index=1)

    def test_cpu_device_resolves_to_no_gpu(self):
        backend = _native_backend("cpu-backend", "cpu")

        assert backend.resolve_execution_device() == ExecutionDeviceEvidence(kind="no_gpu")

    def test_cuda0_backend_matches_an_index0_monitor(self):
        backend = _native_backend("gpu0-backend", "cuda:0")
        info = RequirementBackendInfo(
            id="gpu0-backend", engine="native", driver="native",
            execution_device=backend.resolve_execution_device(),
        )

        ctx = build_requirement_context_for_backend(_preset(), None, _FakeGpuMonitor(24 * 1024, device_index=0), info)

        assert ctx.gpu_total_vram_gb == pytest.approx(24.0)

    def test_cuda1_backend_is_unknown_against_an_index0_monitor(self):
        """GPU0=24 GiB (this process's one monitor), GPU1=8 GiB, 16 GiB
        requirement: the cuda:1 candidate must read `unknown`, never GPU0's
        total - exactly the false "ok" the reopened bug produced."""
        backend = _native_backend("gpu1-backend", "cuda:1")
        info = RequirementBackendInfo(
            id="gpu1-backend", engine="native", driver="native",
            execution_device=backend.resolve_execution_device(),
        )

        ctx = build_requirement_context_for_backend(_preset(), None, _FakeGpuMonitor(24 * 1024, device_index=0), info)

        assert ctx.gpu_total_vram_gb is None

    def test_cuda1_backend_matches_an_index1_monitor(self):
        backend = _native_backend("gpu1-backend", "cuda:1")
        info = RequirementBackendInfo(
            id="gpu1-backend", engine="native", driver="native",
            execution_device=backend.resolve_execution_device(),
        )

        ctx = build_requirement_context_for_backend(_preset(), None, _FakeGpuMonitor(8 * 1024, device_index=1), info)

        assert ctx.gpu_total_vram_gb == pytest.approx(8.0)

    def test_cpu_configured_backend_never_reads_a_gpu_on_a_gpu_host(self):
        backend = _native_backend("cpu-backend", "cpu")
        info = RequirementBackendInfo(
            id="cpu-backend", engine="native", driver="native",
            execution_device=backend.resolve_execution_device(),
        )

        ctx = build_requirement_context_for_backend(_preset(), None, _FakeGpuMonitor(24 * 1024, device_index=0), info)

        assert ctx.gpu_total_vram_gb is None
