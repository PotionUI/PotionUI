"""`context_builder`: resolving a preset's engine backend(s) into
`RequirementBackendInfo`/`RequirementContext` - in particular, that
`execution_device` carries a backend INSTANCE's own `ExecutionDeviceEvidence`
(never inferred from its driver name, never just "a GPU exists on this
host", and never just "an index matches") and is the only thing that gates
whether a local VRAM reading applies.

An index (an NVML enumeration index, or a CUDA ordinal) is not itself proof
of "the same physical card": NVML's enumeration order need not agree with
CUDA's own (further remappable via `CUDA_VISIBLE_DEVICES`), so the gate is
physical IDENTITY (`src.platform.runtime.gpu.DeviceIdentity`, a stable UUID)
- both sides must report one and they must be equal. A preset's own
`pipeline.yml` can also override a pipe's device outright
(`_preset_device_override`), which this file also covers.
"""

from unittest.mock import Mock

import pytest

from src.features.backends.backend_config import NativeBackendConfig
from src.features.backends.base_backend import ExecutionDeviceEvidence
from src.features.backends.native_backend import NativeBackend
from src.features.backends import native_backend as native_backend_module
from src.features.presets.requirements.context_builder import (
    backend_infos_for_engine,
    build_requirement_context,
    build_requirement_context_for_backend,
)
from src.features.presets.requirements.contracts import RequirementBackendInfo
from src.features.presets.templates import ModeTemplate, PipeTemplate, PresetTemplate
from src.platform.runtime.gpu import DeviceIdentity


def _identity(tag: str) -> DeviceIdentity:
    return DeviceIdentity(uuid=f"GPU-{tag}")


def _preset(engine="native", pipes=None):
    modes = {}
    if pipes is not None:
        modes["txt2img"] = ModeTemplate(forms=[], pipes=pipes)
    return PresetTemplate(id="p1", name="Preset", version="1.0.0", path="/tmp/preset", modes=modes, engine=engine)


def _native_backend(id, device):
    """A REAL `NativeBackend` over a REAL `NativeBackendConfig` - every
    field that would otherwise probe this host's hardware
    (`detect_native_hardware_defaults`) is pinned explicitly so the fixture
    is deterministic. Its `resolve_execution_device()` GPU-identity lookup
    still touches real torch/CUDA unless the caller monkeypatches
    `native_backend_module._cuda_device_identity` - see
    `_patch_cuda_identity` below, used by every test that needs a
    deterministic identity."""
    config = NativeBackendConfig(id=id, name=id, device=device, dtype="float16", gpu_max_vram=0)
    return NativeBackend(backend_config=config)


def _patch_cuda_identity(monkeypatch, by_index: dict):
    """Replaces `native_backend._cuda_device_identity` with a fake lookup -
    never touches real CUDA (per the reopened bug's own instruction: inject
    fake identities, don't probe real hardware in tests). `by_index` maps a
    CUDA ordinal to the `DeviceIdentity` (or `None`) it should resolve to;
    an index absent from it resolves to `None`, matching a real "torch
    could not report this ordinal" outcome."""
    monkeypatch.setattr(native_backend_module, "_cuda_device_identity", lambda index: by_index.get(index))


class _FakeConfig:
    def __init__(self, id, name, engine, driver=None, device=None):
        self.id = id
        self.name = name
        self.engine = engine
        self.driver = driver or engine
        if device is not None:
            self.device = device


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
    def __init__(self, total_vram_mb, available=True, device_index=0, device_identity=None):
        self.available = available
        self._total_vram_mb = total_vram_mb
        self.device_index = device_index
        self.device_identity = device_identity

    def get_total_vram(self):
        return self._total_vram_mb


class TestBackendInfosCarryExecutionDevice:
    def test_backend_infos_for_engine_copies_execution_device(self):
        registry = _FakeRegistry([
            _FakeBackend(
                _FakeConfig("a", "A", "native"),
                ExecutionDeviceEvidence(kind="this_host_gpu", gpu_index=0, identity=_identity("x")),
            ),
            _FakeBackend(_FakeConfig("b", "B", "native"), ExecutionDeviceEvidence(kind="unestablished")),
        ])

        infos = backend_infos_for_engine(registry, "native")

        by_id = {i.id: i for i in infos}
        assert by_id["a"].execution_device == ExecutionDeviceEvidence(
            kind="this_host_gpu", gpu_index=0, identity=_identity("x"),
        )
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
                ExecutionDeviceEvidence(kind="this_host_gpu", gpu_index=0, identity=_identity("x")),
            )],
            default_id="native-local",
        )

        ctx = build_requirement_context(
            _preset(engine="native"), None, _FakeGpuMonitor(8 * 1024, device_identity=_identity("x")), registry,
        )

        assert ctx.backend.execution_device == ExecutionDeviceEvidence(
            kind="this_host_gpu", gpu_index=0, identity=_identity("x"),
        )
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
                ExecutionDeviceEvidence(kind="this_host_gpu", gpu_index=0, identity=_identity("x")),
            )],
            default_id="native-local",
        )

        ctx = build_requirement_context(_preset(engine="native"), None, _FakeGpuMonitor(8 * 1024), registry)

        assert ctx.backend.execution_device == ExecutionDeviceEvidence(kind="unestablished")
        assert ctx.gpu_total_vram_gb is None


class TestBuildRequirementContextForBackendExecutionDeviceGate:
    def test_this_host_gpu_reads_local_vram_when_identity_matches(self):
        info = RequirementBackendInfo(
            id="a", engine="native", driver="native",
            execution_device=ExecutionDeviceEvidence(kind="this_host_gpu", gpu_index=0, identity=_identity("x")),
        )

        ctx = build_requirement_context_for_backend(
            _preset(), None, _FakeGpuMonitor(24 * 1024, device_identity=_identity("x")), info,
        )

        assert ctx.gpu_total_vram_gb == pytest.approx(24.0)
        assert ctx.gpu_unavailable_reason is None

    def test_this_host_gpu_reads_unknown_when_indices_match_but_identity_does_not(self):
        """The device-identity gap this rework closes: NVML's enumeration
        index and a CUDA ordinal can each independently be remapped, so two
        sides agreeing on "index 0" is not proof of the same physical
        card - a backend resolved to GPU 1's ordinal 0 slot on a remapped
        host must never read GPU 0's monitor just because both say "0"."""
        info = RequirementBackendInfo(
            id="a", engine="native", driver="native",
            execution_device=ExecutionDeviceEvidence(kind="this_host_gpu", gpu_index=0, identity=_identity("physical-gpu-1")),
        )

        ctx = build_requirement_context_for_backend(
            _preset(), None, _FakeGpuMonitor(24 * 1024, device_identity=_identity("physical-gpu-0")), info,
        )

        assert ctx.gpu_total_vram_gb is None
        assert "identity mismatch" in ctx.gpu_unavailable_reason

    def test_this_host_gpu_reads_unknown_when_backend_identity_is_unestablished(self):
        info = RequirementBackendInfo(
            id="a", engine="native", driver="native",
            execution_device=ExecutionDeviceEvidence(kind="this_host_gpu", gpu_index=0, identity=None),
        )

        ctx = build_requirement_context_for_backend(
            _preset(), None, _FakeGpuMonitor(24 * 1024, device_identity=_identity("x")), info,
        )

        assert ctx.gpu_total_vram_gb is None
        assert "could not be established" in ctx.gpu_unavailable_reason

    def test_this_host_gpu_reads_unknown_when_monitor_identity_is_unestablished(self):
        info = RequirementBackendInfo(
            id="a", engine="native", driver="native",
            execution_device=ExecutionDeviceEvidence(kind="this_host_gpu", gpu_index=0, identity=_identity("x")),
        )

        ctx = build_requirement_context_for_backend(
            _preset(), None, _FakeGpuMonitor(24 * 1024, device_identity=None), info,
        )

        assert ctx.gpu_total_vram_gb is None
        assert "could not be established" in ctx.gpu_unavailable_reason

    def test_no_gpu_never_reads_local_vram_even_with_a_gpu_available(self):
        """A NativeBackend explicitly configured with `device="cpu"` (see
        `NativeBackend.resolve_execution_device`) must not borrow this
        host's GPU reading just because one happens to exist."""
        info = RequirementBackendInfo(
            id="a", engine="native", driver="native", execution_device=ExecutionDeviceEvidence(kind="no_gpu"),
        )

        ctx = build_requirement_context_for_backend(
            _preset(), None, _FakeGpuMonitor(24 * 1024, device_identity=_identity("x")), info,
        )

        assert ctx.gpu_total_vram_gb is None
        assert "no GPU" in ctx.gpu_unavailable_reason

    def test_remote_never_reads_local_vram_even_with_a_gpu_available(self):
        info = RequirementBackendInfo(
            id="a", engine="native", driver="native.remote", execution_device=ExecutionDeviceEvidence(kind="remote"),
        )

        ctx = build_requirement_context_for_backend(
            _preset(), None, _FakeGpuMonitor(24 * 1024, device_identity=_identity("x")), info,
        )

        assert ctx.gpu_total_vram_gb is None

    def test_unestablished_never_reads_local_vram_regardless_of_driver_name(self):
        """A driver name that doesn't contain "remote" (e.g. a plugin's own
        engine name, like "comfyui") must never be read as local - only an
        explicit `ExecutionDeviceEvidence` may grant that."""
        info = RequirementBackendInfo(
            id="a", engine="comfyui", driver="comfyui", execution_device=ExecutionDeviceEvidence(kind="unestablished"),
        )

        ctx = build_requirement_context_for_backend(
            _preset(), None, _FakeGpuMonitor(24 * 1024, device_identity=_identity("x")), info,
        )

        assert ctx.gpu_total_vram_gb is None

    def test_no_backend_never_reads_local_vram(self):
        ctx = build_requirement_context_for_backend(_preset(), None, _FakeGpuMonitor(24 * 1024), None)

        assert ctx.gpu_total_vram_gb is None


class TestNativeBackendRealInstanceDeviceIdentity:
    """REAL `NativeBackend`/`NativeBackendConfig` instances (not a fake) -
    proves `resolve_execution_device()` reads the instance's own configured
    device, with its GPU-identity lookup (`_cuda_device_identity`)
    monkeypatched rather than touching real CUDA."""

    def test_bare_cuda_has_no_established_identity(self, monkeypatch):
        """A bare "cuda" (no explicit `:N`) is forwarded unchanged to the
        pipes that actually run inference, and torch resolves it at
        EXECUTION time to `torch.cuda.current_device()` for whichever
        thread runs the pipe - not knowable in advance, and never 0 by
        assumption. `_cuda_device_identity` must not even be called."""
        calls = []
        monkeypatch.setattr(
            native_backend_module, "_cuda_device_identity", lambda index: calls.append(index),
        )
        backend = _native_backend("gpu-backend", "cuda")

        evidence = backend.resolve_execution_device()

        assert evidence == ExecutionDeviceEvidence(
            kind="this_host_gpu", gpu_index=None, identity=None,
            reason="configured device 'cuda' has no explicit ordinal; the worker's device cannot be established",
        )
        assert calls == []

    def test_bare_cuda_ignores_this_threads_current_device_even_when_reported(self, monkeypatch):
        """Reading THIS thread's current CUDA device would not be proof of
        anything - the thread that actually executes the pipe may differ.
        Fake it as both 0 and 1 and confirm neither is even consulted, let
        alone trusted."""
        current_device = Mock(return_value=0)
        monkeypatch.setattr("torch.cuda.current_device", current_device)
        backend = _native_backend("gpu-backend", "cuda")

        evidence = backend.resolve_execution_device()

        assert evidence.identity is None
        current_device.assert_not_called()

        current_device_other = Mock(return_value=1)
        monkeypatch.setattr("torch.cuda.current_device", current_device_other)

        evidence_again = backend.resolve_execution_device()

        assert evidence_again.identity is None
        current_device_other.assert_not_called()

    def test_bare_cuda_when_cuda_unavailable_still_reads_unestablished_not_no_gpu(self, monkeypatch):
        """Unlike `device="cpu"` (definite "no_gpu" evidence), a bare
        "cuda" on a host with no usable CUDA is still `kind="this_host_gpu"`
        (that IS the configured intent) with no identity - "unestablished"
        for a different, already-covered reason, not reclassified."""
        monkeypatch.setattr("torch.cuda.is_available", lambda: False)
        backend = _native_backend("gpu-backend", "cuda")

        evidence = backend.resolve_execution_device()

        assert evidence.kind == "this_host_gpu"
        assert evidence.identity is None
        assert "no explicit ordinal" in evidence.reason

    def test_cuda_colon_n_resolves_to_that_index(self, monkeypatch):
        _patch_cuda_identity(monkeypatch, {1: _identity("gpu1")})
        backend = _native_backend("gpu1-backend", "cuda:1")

        assert backend.resolve_execution_device() == ExecutionDeviceEvidence(
            kind="this_host_gpu", gpu_index=1, identity=_identity("gpu1"),
        )

    def test_cpu_device_resolves_to_no_gpu(self, monkeypatch):
        _patch_cuda_identity(monkeypatch, {0: _identity("gpu0")})  # must never be consulted
        backend = _native_backend("cpu-backend", "cpu")

        assert backend.resolve_execution_device() == ExecutionDeviceEvidence(kind="no_gpu")

    def test_torch_unable_to_report_identity_resolves_to_none(self, monkeypatch):
        _patch_cuda_identity(monkeypatch, {})  # index 0 absent -> None
        backend = _native_backend("gpu0-backend", "cuda:0")

        assert backend.resolve_execution_device() == ExecutionDeviceEvidence(
            kind="this_host_gpu", gpu_index=0, identity=None,
        )

    def test_cuda0_backend_matches_an_index0_monitor_with_the_same_identity(self, monkeypatch):
        _patch_cuda_identity(monkeypatch, {0: _identity("gpu0")})
        backend = _native_backend("gpu0-backend", "cuda:0")
        info = RequirementBackendInfo(
            id="gpu0-backend", engine="native", driver="native",
            execution_device=backend.resolve_execution_device(),
        )

        ctx = build_requirement_context_for_backend(
            _preset(), None, _FakeGpuMonitor(24 * 1024, device_index=0, device_identity=_identity("gpu0")), info,
        )

        assert ctx.gpu_total_vram_gb == pytest.approx(24.0)

    def test_cuda1_backend_is_unknown_against_an_index0_monitor(self, monkeypatch):
        """GPU0=24 GiB (this process's one monitor), GPU1=8 GiB, 16 GiB
        requirement: the cuda:1 candidate must read `unknown`, never GPU0's
        total - exactly the false "ok" the reopened bug produced."""
        _patch_cuda_identity(monkeypatch, {0: _identity("gpu0"), 1: _identity("gpu1")})
        backend = _native_backend("gpu1-backend", "cuda:1")
        info = RequirementBackendInfo(
            id="gpu1-backend", engine="native", driver="native",
            execution_device=backend.resolve_execution_device(),
        )

        ctx = build_requirement_context_for_backend(
            _preset(), None, _FakeGpuMonitor(24 * 1024, device_index=0, device_identity=_identity("gpu0")), info,
        )

        assert ctx.gpu_total_vram_gb is None

    def test_cuda0_ordinal_remapped_to_a_different_physical_card_is_unknown(self, monkeypatch):
        """The physical-identity gap specifically: CUDA_VISIBLE_DEVICES (or
        any other remap) can make CUDA ordinal 0 point at physical GPU 1
        while this process's NVML-bound monitor is still physical GPU 0 -
        both call it "index 0", but they are not the same card."""
        _patch_cuda_identity(monkeypatch, {0: _identity("physical-gpu-1")})  # ordinal 0 -> physical GPU 1
        backend = _native_backend("remapped-backend", "cuda:0")
        info = RequirementBackendInfo(
            id="remapped-backend", engine="native", driver="native",
            execution_device=backend.resolve_execution_device(),
        )

        ctx = build_requirement_context_for_backend(
            _preset(), None,
            _FakeGpuMonitor(24 * 1024, device_index=0, device_identity=_identity("physical-gpu-0")),
            info,
        )

        assert ctx.gpu_total_vram_gb is None
        assert "identity mismatch" in ctx.gpu_unavailable_reason

    def test_cuda1_backend_matches_an_index1_monitor_with_the_same_identity(self, monkeypatch):
        _patch_cuda_identity(monkeypatch, {1: _identity("gpu1")})
        backend = _native_backend("gpu1-backend", "cuda:1")
        info = RequirementBackendInfo(
            id="gpu1-backend", engine="native", driver="native",
            execution_device=backend.resolve_execution_device(),
        )

        ctx = build_requirement_context_for_backend(
            _preset(), None, _FakeGpuMonitor(8 * 1024, device_index=1, device_identity=_identity("gpu1")), info,
        )

        assert ctx.gpu_total_vram_gb == pytest.approx(8.0)

    def test_cpu_configured_backend_never_reads_a_gpu_on_a_gpu_host(self, monkeypatch):
        _patch_cuda_identity(monkeypatch, {0: _identity("gpu0")})
        backend = _native_backend("cpu-backend", "cpu")
        info = RequirementBackendInfo(
            id="cpu-backend", engine="native", driver="native",
            execution_device=backend.resolve_execution_device(),
        )

        ctx = build_requirement_context_for_backend(
            _preset(), None, _FakeGpuMonitor(24 * 1024, device_index=0, device_identity=_identity("gpu0")), info,
        )

        assert ctx.gpu_total_vram_gb is None

    def test_torch_side_identity_unavailable_is_unknown_even_with_a_monitor_identity(self, monkeypatch):
        _patch_cuda_identity(monkeypatch, {})  # torch can't report ordinal 0's identity
        backend = _native_backend("gpu0-backend", "cuda:0")
        info = RequirementBackendInfo(
            id="gpu0-backend", engine="native", driver="native",
            execution_device=backend.resolve_execution_device(),
        )

        ctx = build_requirement_context_for_backend(
            _preset(), None, _FakeGpuMonitor(24 * 1024, device_index=0, device_identity=_identity("gpu0")), info,
        )

        assert ctx.gpu_total_vram_gb is None
        assert "could not be established" in ctx.gpu_unavailable_reason


class TestPresetDeviceOverride:
    """A preset's own `pipeline.yml` `configuration: {device: ...}` on any
    pipe wins over the backend's admin-configured device
    (`NativeBackend.prepare_pipes` only `setdefault`s) - `vram_min_gb` has
    no visibility into which mode/pipe a generation will actually use, so
    ANY pipe declaring a conflicting or unresolvable override degrades the
    whole preset's reading to `unknown`, never asserting past it."""

    def _matching_info(self):
        return RequirementBackendInfo(
            id="gpu0-backend", engine="native", driver="native",
            execution_device=ExecutionDeviceEvidence(kind="this_host_gpu", gpu_index=0, identity=_identity("gpu0")),
            config=NativeBackendConfig(id="gpu0-backend", name="GPU 0", device="cuda:0", dtype="float16", gpu_max_vram=0),
        )

    def _monitor(self):
        return _FakeGpuMonitor(24 * 1024, device_index=0, device_identity=_identity("gpu0"))

    def test_no_override_reads_normally(self):
        preset = _preset(pipes=[PipeTemplate(name="generator/native", configuration={"steps": 20})])

        ctx = build_requirement_context_for_backend(preset, None, self._monitor(), self._matching_info())

        assert ctx.gpu_total_vram_gb == pytest.approx(24.0)

    def test_matching_literal_override_reads_normally(self):
        preset = _preset(pipes=[PipeTemplate(name="generator/native", configuration={"device": "cuda:0"})])

        ctx = build_requirement_context_for_backend(preset, None, self._monitor(), self._matching_info())

        assert ctx.gpu_total_vram_gb == pytest.approx(24.0)

    def test_conflicting_literal_override_is_unknown(self):
        preset = _preset(pipes=[PipeTemplate(name="generator/native", configuration={"device": "cuda:1"})])

        ctx = build_requirement_context_for_backend(preset, None, self._monitor(), self._matching_info())

        assert ctx.gpu_total_vram_gb is None
        assert "overrides its device to 'cuda:1'" in ctx.gpu_unavailable_reason

    def test_templated_unresolvable_override_is_unknown(self):
        preset = _preset(pipes=[PipeTemplate(name="generator/native", configuration={"device": "{{ form.device }}"})])

        ctx = build_requirement_context_for_backend(preset, None, self._monitor(), self._matching_info())

        assert ctx.gpu_total_vram_gb is None
        assert "template this check has no form data to resolve" in ctx.gpu_unavailable_reason

    def test_non_string_override_is_unknown_not_silently_skipped(self):
        """A `device` value that is present but not a plain string (a
        dict/list/int - an authoring mistake, or an unresolved `@config:`-
        style indirection) must be treated conservatively, never as "no
        override" - the check cannot reason about its shape at all."""
        preset = _preset(pipes=[PipeTemplate(name="generator/native", configuration={"device": {"nested": "value"}})])

        ctx = build_requirement_context_for_backend(preset, None, self._monitor(), self._matching_info())

        assert ctx.gpu_total_vram_gb is None
        assert "unresolvable device override" in ctx.gpu_unavailable_reason

    def test_disabled_pipes_overrides_are_ignored(self):
        preset = _preset(pipes=[
            PipeTemplate(name="generator/native", enabled=False, configuration={"device": "cuda:1"}),
        ])

        ctx = build_requirement_context_for_backend(preset, None, self._monitor(), self._matching_info())

        assert ctx.gpu_total_vram_gb == pytest.approx(24.0)

    def test_override_in_a_different_mode_still_counts(self):
        """`vram_min_gb` is a whole-preset requirement, not mode-scoped -
        an override anywhere in the preset must degrade the reading, since
        this check has no way to know which mode a generation will use."""
        preset = _preset(pipes=[PipeTemplate(name="generator/native", configuration={"steps": 20})])
        preset.modes["img2img"] = ModeTemplate(
            forms=[], pipes=[PipeTemplate(name="generator/native", configuration={"device": "cuda:1"})],
        )

        ctx = build_requirement_context_for_backend(preset, None, self._monitor(), self._matching_info())

        assert ctx.gpu_total_vram_gb is None
