"""Tests for `GenerationOrchestrator.preview_memory`.

Reuses the same fixture shape as test_orchestrator_routing_decision.py: a
`GenerationOrchestrator` built with fakes standing in for its collaborators.
Asserts the preview reports device/budget/estimate evidence for local,
remote and no-GPU backends, and that it never enqueues, hooks, persists, or
starts a real generation.
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch

from src.features.backends.backend_config import NativeBackendConfig, NativeRemoteBackendConfig
from src.features.backends.native_backend import NativeBackend
from src.features.backends.native_remote_backend import RemoteNativeBackend
from src.platform.runtime.gpu import DeviceIdentity
from src.features.generation.pipeline_builder import BuiltPipeline
from src.features.generation.queue_dispatcher import QueueDispatcher

# Two distinct fake hardware identities - `native_1`'s default `cuda:0`
# resolves to GPU_0, `cuda:1` to GPU_1. `_gpu_monitor()`'s own default
# identity is GPU_0, so most tests below (which don't care about identity
# nuance) keep matching "local" behavior without repeating this everywhere.
GPU_0 = DeviceIdentity(uuid="GPU-aaaa")
GPU_1 = DeviceIdentity(uuid="GPU-bbbb")


@pytest.fixture(autouse=True)
def _bind_form_passthrough():
    """See test_orchestrator.py::_bind_form_passthrough."""
    from src.features.forms.binding import BoundForm

    def _passthrough(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None):
        return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or 'txt2img', coercions=[], stripped=[])

    with patch('src.features.generation.orchestrator.bind_form', side_effect=_passthrough):
        yield


@pytest.fixture(autouse=True)
def _patch_cuda_identity(monkeypatch):
    """`NativeBackend.resolve_execution_device()` calls the module-level
    `_cuda_device_identity(index)` to resolve a real CUDA UUID - monkeypatched
    here for every test in this file rather than touched for real (REQ-01's
    device-identity contract; see docs/backends.md)."""
    monkeypatch.setattr(
        'src.features.backends.native_backend._cuda_device_identity',
        lambda index: {0: GPU_0, 1: GPU_1}.get(index),
    )


_KNOWN_PIPES = [
    {
        "name": "model_loader/krea2",
        "id": "loader",
        "enabled": True,
        "config": {"checkpoint": "model:ckpt1"},
    }
]


def _pipeline_builder(pipes=_KNOWN_PIPES, build_error=False):
    builder = Mock()
    if build_error:
        builder.build_pipeline = Mock(side_effect=RuntimeError("template error"))
    else:
        builder.build_pipeline = Mock(return_value=BuiltPipeline(
            generation_id='preview', preset_id='preset_1', preset_template=Mock(version='1.0.0'), pipes=pipes,
        ))
    return builder


def _preset_template_loader(engine='native'):
    loader = Mock()
    preset = Mock()
    preset.engine = engine
    preset.id = 'preset_1'
    loader.load_preset_by_id = Mock(return_value=preset)
    return loader


def _local_backend(gpu_max_vram=10, device='cuda:0'):
    """A REAL `NativeBackend` (REQ-01's `resolve_execution_device()` reads
    `self.config.device` at the INSTANCE level - a bare Mock can't exercise
    that seam, since `Mock().resolve_execution_device()` returns another
    Mock, not a real `ExecutionDeviceEvidence`)."""
    config = NativeBackendConfig(id='native_1', name='Local', device=device, dtype='float32', gpu_max_vram=gpu_max_vram)
    backend = NativeBackend(config)
    backend.start_generation = AsyncMock()
    return backend


def _remote_backend():
    config = NativeRemoteBackendConfig(id='remote_1', name='Remote')
    backend = RemoteNativeBackend(config)
    backend.start_generation = AsyncMock()
    return backend


def _comfyui_shaped_backend_with_remote_host():
    """A comfyui-driver in-process plugin backend pointed at a non-local
    host - `driver` alone must never be read as evidence of locality
    (REQ-01); it hasn't declared `execution_device` at all."""
    backend = Mock(spec=['backend_id', 'name', 'engine', 'config', 'start_generation'])
    backend.backend_id = 'comfy_1'
    backend.name = 'ComfyUI'
    backend.engine = 'comfyui'
    backend.config = Mock(spec=['driver', 'host'], driver='comfyui', host='192.0.2.10')
    backend.start_generation = AsyncMock()
    return backend


def _settings():
    settings = Mock()
    settings.get_file_storage_directory = Mock(return_value='/tmp/storage')
    return settings


def _gpu_monitor(free_mb=8192, total_mb=24576, available=True, device_identity=GPU_0):
    monitor = Mock()
    monitor.available = available
    monitor.device_identity = device_identity
    monitor.get_free_vram = Mock(return_value=free_mb)
    monitor.get_total_vram = Mock(return_value=total_mb)
    return monitor


def _model_repo_with(sizes):
    """Patch `src.features.models.repository.model_repo.get_by_id`."""
    import types
    def get_by_id(model_id, include_providers=True, include_tags=True):
        if model_id not in sizes:
            return None
        return types.SimpleNamespace(file_size=sizes[model_id])
    repo = Mock()
    repo.get_by_id = Mock(side_effect=get_by_id)
    return repo


def _orchestrator(backend, gpu_monitor=None, pipes=_KNOWN_PIPES, build_error=False, router=None, plugin_registry=None):
    from src.features.generation.orchestrator import GenerationOrchestrator

    backend_registry = Mock()
    backend_registry.select_backend_for_generation = Mock(return_value=backend)

    return GenerationOrchestrator(
        pipeline_builder=_pipeline_builder(pipes=pipes, build_error=build_error),
        backend_registry=backend_registry,
        connection_hub=Mock(),
        settings=_settings(),
        output_processor=Mock(),
        preset_template_loader=_preset_template_loader(),
        gpu_monitor=gpu_monitor,
        router=router,
        plugin_registry=plugin_registry,
    )


def _make_request(backend_id=None):
    request = Mock()
    request.preset_id = 'preset_1'
    request.form_data = {'steps': 20}
    request.mode = 'txt2img'
    request.form_name = None
    request.backend_id = backend_id
    return request


@pytest.mark.asyncio
async def test_local_backend_reports_gpu_monitor_device_and_bounded_budget():
    backend = _local_backend(gpu_max_vram=10)
    orchestrator = _orchestrator(backend, gpu_monitor=_gpu_monitor(free_mb=8192, total_mb=24576))

    with patch('src.features.models.repository.model_repo', _model_repo_with({"ckpt1": 4 * 1024 ** 3})):
        result = await orchestrator.preview_memory(_make_request(), 'user_1')

    assert result['device']['kind'] == 'local'
    assert result['device']['free_gb'] == 8.0
    assert result['device']['total_gb'] == 24.0
    assert result['estimate']['checkpoint_estimate_gb'] == pytest.approx(round(4.0 * 1.1, 2))
    assert result['coverage']['known'] == [{'ref': 'ckpt1', 'size_gb': 4.0}]
    # backend cap (10) is stricter than device free (8)? no: min(10, 8) = 8
    assert result['budget']['configured_gb'] == 8.0
    assert result['backend']['driver'] == 'native.local'


@pytest.mark.asyncio
async def test_remote_backend_device_unknown_never_uses_this_hosts_gpu():
    backend = _remote_backend()
    # A GpuMonitor IS wired (this host has one), but the chosen backend's
    # driver is native.remote - its numbers must never be read.
    orchestrator = _orchestrator(backend, gpu_monitor=_gpu_monitor())

    with patch('src.features.models.repository.model_repo', _model_repo_with({})):
        result = await orchestrator.preview_memory(_make_request(), 'user_1')

    assert result['device'] == {
        'kind': 'remote', 'free_gb': None, 'total_gb': None,
        'provenance': 'not reported by the remote worker',
    }


@pytest.mark.asyncio
async def test_comfyui_shaped_backend_with_remote_host_is_unknown_not_local():
    """A plugin backend that hasn't declared `execution_device` must never be
    read as local off its driver string - even with a GpuMonitor wired for
    this host, and even though it runs in-process like NativeBackend does."""
    backend = _comfyui_shaped_backend_with_remote_host()
    # Real numbers, not an exception: a broad `except Exception` around a
    # driver-substring check could otherwise mask a real regression here.
    monitor = _gpu_monitor(free_mb=8192, total_mb=24576)
    orchestrator = _orchestrator(backend, gpu_monitor=monitor)

    with patch('src.features.models.repository.model_repo', _model_repo_with({})):
        result = await orchestrator.preview_memory(_make_request(), 'user_1')

    assert result['device'] == {
        'kind': 'unknown', 'free_gb': None, 'total_gb': None,
        'provenance': 'execution device not declared by this backend',
    }


@pytest.mark.asyncio
async def test_no_gpu_monitor_device_is_none():
    backend = _local_backend()
    orchestrator = _orchestrator(backend, gpu_monitor=None)

    with patch('src.features.models.repository.model_repo', _model_repo_with({})):
        result = await orchestrator.preview_memory(_make_request(), 'user_1')

    assert result['device']['kind'] == 'none'


@pytest.mark.asyncio
async def test_cpu_configured_native_backend_reports_no_gpu_end_to_end():
    """`NativeBackendConfig(device="cpu")` on a host that DOES have a GPU
    (the monitor below reports real numbers) must never borrow that
    reading - this backend is definite "no GPU", not "unknown"."""
    backend = _local_backend(device='cpu')
    orchestrator = _orchestrator(backend, gpu_monitor=_gpu_monitor(free_mb=8192, total_mb=24576))

    with patch('src.features.models.repository.model_repo', _model_repo_with({})):
        result = await orchestrator.preview_memory(_make_request(), 'user_1')

    assert result['device'] == {
        'kind': 'none', 'free_gb': None, 'total_gb': None,
        'provenance': 'this backend is configured with no GPU',
    }


@pytest.mark.asyncio
async def test_remapped_ordinal_but_identity_mismatch_reports_unknown_end_to_end():
    """A `NativeBackend` configured for cuda:1 must never borrow a monitor
    watching a DIFFERENT physical card just because some enumeration index
    happens to match - only a differing UUID proves it. Two DISTINCT fake
    totals (24GB "GPU 0" vs a 99GB cap) prove nothing is borrowed: the
    returned numbers must be null, not either total."""
    backend = _local_backend(device='cuda:1', gpu_max_vram=99)
    orchestrator = _orchestrator(backend, gpu_monitor=_gpu_monitor(free_mb=8192, total_mb=24576, device_identity=GPU_0))

    with patch('src.features.models.repository.model_repo', _model_repo_with({})):
        result = await orchestrator.preview_memory(_make_request(), 'user_1')

    assert result['device']['kind'] == 'unknown'
    assert result['device']['free_gb'] is None and result['device']['total_gb'] is None
    assert result['device']['provenance'] == 'this backend\'s configured GPU is not the specific GPU this process monitors (identity mismatch)'


@pytest.mark.asyncio
async def test_backend_identity_unavailable_reports_unknown_end_to_end(monkeypatch):
    """`_cuda_device_identity` returning `None` for every index (torch/CUDA
    genuinely unavailable) must never be treated as "assume it matches"."""
    monkeypatch.setattr('src.features.backends.native_backend._cuda_device_identity', lambda index: None)
    backend = _local_backend(device='cuda:0')
    orchestrator = _orchestrator(backend, gpu_monitor=_gpu_monitor())

    with patch('src.features.models.repository.model_repo', _model_repo_with({})):
        result = await orchestrator.preview_memory(_make_request(), 'user_1')

    assert result['device']['kind'] == 'unknown'
    assert 'backend\'s GPU identity could not be established' in result['device']['provenance']


@pytest.mark.asyncio
async def test_bare_cuda_device_reports_unknown_with_the_backends_own_reason_end_to_end():
    """A bare `"cuda"` (no explicit `:N` ordinal) must never be attributed
    this host's GPU numbers - not even the monitor's real, matching ones -
    and the advisory must surface REQ-01's specific reason, not its own
    generic wording."""
    backend = _local_backend(device='cuda')
    orchestrator = _orchestrator(backend, gpu_monitor=_gpu_monitor(free_mb=8192, total_mb=24576, device_identity=GPU_0))

    with patch('src.features.models.repository.model_repo', _model_repo_with({})):
        result = await orchestrator.preview_memory(_make_request(), 'user_1')

    assert result['device']['kind'] == 'unknown'
    assert result['device']['free_gb'] is None and result['device']['total_gb'] is None
    assert result['device']['provenance'] == "configured device 'cuda' has no explicit ordinal; the worker's device cannot be established"


def _pipes_with_active_device(device):
    return [
        {"name": "model_loader/krea2", "id": "loader", "enabled": True, "config": {"checkpoint": "model:ckpt1"}},
        {"name": "generator/x", "id": "gen1", "enabled": True, "config": {"device": device}},
    ]


def _pipes_with_disabled_device_override(device):
    return [
        {"name": "model_loader/krea2", "id": "loader", "enabled": True, "config": {"checkpoint": "model:ckpt1"}},
        {"name": "generator/x", "id": "gen1", "enabled": False, "config": {"device": device}},
    ]


@pytest.mark.asyncio
async def test_conflicting_gpu_override_reports_unknown_end_to_end():
    """A GENUINELY matching backend/monitor (would otherwise be "local", real
    numbers) must still report "unknown" once an active stage pins a
    CONFLICTING device - the backend's own reading cannot be attributed to a
    request that pins one of its stages elsewhere. Two distinct fake totals
    (24GB backend monitor vs a 99GB cap) prove nothing is borrowed."""
    backend = _local_backend(device='cuda:0', gpu_max_vram=99)
    orchestrator = _orchestrator(
        backend,
        gpu_monitor=_gpu_monitor(free_mb=8192, total_mb=24576, device_identity=GPU_0),
        pipes=_pipes_with_active_device('cuda:1'),
    )

    with patch('src.features.models.repository.model_repo', _model_repo_with({"ckpt1": 1 * 1024 ** 3})):
        result = await orchestrator.preview_memory(_make_request(), 'user_1')

    assert result['device']['kind'] == 'unknown'
    assert result['device']['free_gb'] is None and result['device']['total_gb'] is None
    assert 'gen1' in result['device']['provenance']
    assert result['budget']['configured_gb'] == 99.0  # the backend's own cap, unbounded - never dropped
    assert 'not bounded by device evidence' in result['budget']['source']


@pytest.mark.asyncio
async def test_conflicting_gpu_override_on_a_cpu_backend_reports_unknown_end_to_end():
    """A `device="cpu"` backend normally reports the more confident "none" -
    an active stage pinning a GPU device must upgrade that to "unknown"."""
    backend = _local_backend(device='cpu')
    orchestrator = _orchestrator(backend, gpu_monitor=_gpu_monitor(), pipes=_pipes_with_active_device('cuda:0'))

    with patch('src.features.models.repository.model_repo', _model_repo_with({})):
        result = await orchestrator.preview_memory(_make_request(), 'user_1')

    assert result['device']['kind'] == 'unknown'


@pytest.mark.asyncio
async def test_matching_gpu_override_stays_local_end_to_end():
    backend = _local_backend(device='cuda:0')
    orchestrator = _orchestrator(
        backend, gpu_monitor=_gpu_monitor(device_identity=GPU_0), pipes=_pipes_with_active_device('cuda:0'),
    )

    with patch('src.features.models.repository.model_repo', _model_repo_with({})):
        result = await orchestrator.preview_memory(_make_request(), 'user_1')

    assert result['device']['kind'] == 'local'


@pytest.mark.asyncio
async def test_absent_override_stays_local_end_to_end():
    backend = _local_backend(device='cuda:0')
    orchestrator = _orchestrator(backend, gpu_monitor=_gpu_monitor(device_identity=GPU_0))  # default _KNOWN_PIPES, no device key

    with patch('src.features.models.repository.model_repo', _model_repo_with({"ckpt1": 1 * 1024 ** 3})):
        result = await orchestrator.preview_memory(_make_request(), 'user_1')

    assert result['device']['kind'] == 'local'


@pytest.mark.asyncio
async def test_disabled_stage_override_stays_local_end_to_end():
    backend = _local_backend(device='cuda:0')
    orchestrator = _orchestrator(
        backend, gpu_monitor=_gpu_monitor(device_identity=GPU_0), pipes=_pipes_with_disabled_device_override('cuda:1'),
    )

    with patch('src.features.models.repository.model_repo', _model_repo_with({})):
        result = await orchestrator.preview_memory(_make_request(), 'user_1')

    assert result['device']['kind'] == 'local'


@pytest.mark.asyncio
async def test_templated_override_reports_unknown_end_to_end():
    """A non-string `device` (an unrendered template, or anything else that
    isn't a plain comparable literal) is treated conservatively as a
    conflict, never assumed to match."""
    backend = _local_backend(device='cuda:0')
    orchestrator = _orchestrator(
        backend, gpu_monitor=_gpu_monitor(device_identity=GPU_0), pipes=_pipes_with_active_device({"unexpected": "shape"}),
    )

    with patch('src.features.models.repository.model_repo', _model_repo_with({})):
        result = await orchestrator.preview_memory(_make_request(), 'user_1')

    assert result['device']['kind'] == 'unknown'


@pytest.mark.asyncio
async def test_budget_reflects_backend_cap_change():
    with patch('src.features.models.repository.model_repo', _model_repo_with({})):
        low = await _orchestrator(_local_backend(gpu_max_vram=4), gpu_monitor=_gpu_monitor(free_mb=8192 * 1024)).preview_memory(
            _make_request(), 'user_1'
        )
        high = await _orchestrator(_local_backend(gpu_max_vram=16), gpu_monitor=_gpu_monitor(free_mb=8192 * 1024)).preview_memory(
            _make_request(), 'user_1'
        )

    assert low['budget']['configured_gb'] == 4.0
    assert high['budget']['configured_gb'] == 16.0


@pytest.mark.asyncio
async def test_explicit_backend_id_is_honored_without_a_router():
    backend = _local_backend()
    orchestrator = _orchestrator(backend)

    with patch('src.features.models.repository.model_repo', _model_repo_with({})):
        await orchestrator.preview_memory(_make_request(backend_id='native_1'), 'user_1')

    orchestrator.backend_registry.select_backend_for_generation.assert_called_once_with(
        engine='native', backend_id='native_1',
    )


@pytest.mark.asyncio
async def test_explicit_backend_id_passed_through_router():
    from src.features.generation.routing.contracts import Candidate, RoutingDecision

    backend = _local_backend()
    router = Mock()
    router.route = AsyncMock(return_value=RoutingDecision(
        chosen=backend, candidates=[Candidate(backend=backend)], rule_trace=[],
    ))
    orchestrator = _orchestrator(backend, router=router)

    with patch('src.features.models.repository.model_repo', _model_repo_with({})):
        await orchestrator.preview_memory(_make_request(backend_id='native_1'), 'user_1')

    assert router.route.call_args[0][0].requested_backend_id == 'native_1'


@pytest.mark.asyncio
async def test_build_failure_leaves_active_set_unresolved():
    backend = _local_backend()
    orchestrator = _orchestrator(backend, build_error=True)

    with patch('src.features.models.repository.model_repo', _model_repo_with({})):
        result = await orchestrator.preview_memory(_make_request(), 'user_1')

    assert result['coverage']['active_set_resolved'] is False
    assert result['estimate']['checkpoint_estimate_gb'] is None


@pytest.mark.asyncio
async def test_never_enqueues_hooks_or_persists():
    backend = _local_backend()
    plugin_registry = Mock()
    plugin_registry.execute_hook = Mock()
    orchestrator = _orchestrator(backend, plugin_registry=plugin_registry)

    with patch('src.features.generation.orchestrator.generation_repo') as mock_generation_repo, \
         patch('src.features.models.repository.model_repo', _model_repo_with({})), \
         patch.object(QueueDispatcher, 'enqueue', new=AsyncMock()) as mock_enqueue:
        await orchestrator.preview_memory(_make_request(), 'user_1')

    mock_generation_repo.create.assert_not_called()
    plugin_registry.execute_hook.assert_not_called()
    mock_enqueue.assert_not_called()
    backend.start_generation.assert_not_called()
