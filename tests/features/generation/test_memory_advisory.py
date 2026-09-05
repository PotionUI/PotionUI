"""Pure-function tests for `src.features.generation.memory_advisory`.

No orchestrator, no database, no GPU - `estimate_request_memory` takes a
built pipeline's processed pipes, a model lookup callable, and form data;
`resolve_device_evidence`/`resolve_budget_evidence` take plain values/fakes.
"""

import types

import pytest

from src.features.backends.backend_config import NativeBackendConfig, NativeRemoteBackendConfig
from src.features.backends.native_backend import NativeBackend
from src.features.backends.native_remote_backend import RemoteNativeBackend
from src.platform.runtime.gpu import DeviceIdentity
from src.features.generation.memory_advisory import (
    active_model_ids,
    active_loader_settings,
    active_pipe_vram_hints,
    estimate_request_memory,
    resolve_budget_evidence,
    resolve_device_evidence,
    DeviceEvidence,
    PipeVramHint,
)

GIB = 1024 ** 3


def _model(size):
    return types.SimpleNamespace(file_size=size)


def _lookup(sizes):
    """model_id -> SimpleNamespace(file_size=...) | None, from a plain dict."""
    def lookup(model_id):
        if model_id not in sizes:
            return None
        return _model(sizes[model_id])
    return lookup


def _pipes(
    active_refs=None,
    disabled_refs=None,
    non_loader_refs=None,
    active_config_extra=None,
):
    """A minimal built-pipeline pipe list: one enabled loader-family pipe
    carrying `active_refs` (+ any extra config), one disabled loader-family
    pipe carrying `disabled_refs`, and one enabled non-loader pipe carrying
    `non_loader_refs` - exercising exactly the enabled+family filter
    `active_model_ids`/`active_loader_settings` apply."""
    pipes = []
    config = dict(active_config_extra or {})
    for i, ref in enumerate(active_refs or []):
        config[f"field_{i}"] = f"model:{ref}"
    pipes.append({"name": "model_loader/krea2", "id": "loader", "enabled": True, "config": config})

    if disabled_refs:
        disabled_config = {f"field_{i}": f"model:{ref}" for i, ref in enumerate(disabled_refs)}
        pipes.append({"name": "model_loader/krea2_alt", "id": "loader_alt", "enabled": False, "config": disabled_config})

    if non_loader_refs:
        non_loader_config = {f"field_{i}": f"model:{ref}" for i, ref in enumerate(non_loader_refs)}
        pipes.append({"name": "generator/krea2", "id": "gen", "enabled": True, "config": non_loader_config})

    return pipes


# -- active model id / settings walk --------------------------------------------

def test_active_model_ids_only_enabled_loader_family_pipes():
    pipes = _pipes(active_refs=["ckpt1"], disabled_refs=["ckpt2"], non_loader_refs=["ckpt3"])
    assert active_model_ids(pipes) == ["ckpt1"]


def test_active_loader_settings_collects_dtype_and_quant():
    pipes = _pipes(active_refs=["ckpt1"], active_config_extra={"dtype": "bf16", "quant_mode": "nvfp4", "unrelated": 1})
    settings = active_loader_settings(pipes)
    assert "loader.dtype=bf16" in settings
    assert "loader.quant_mode=nvfp4" in settings
    assert not any("unrelated" in s for s in settings)


def test_active_pipe_vram_hints_ignores_disabled_and_missing_reports_every_active_one():
    pipes = [
        {"name": "tiled_detailer/sdxl", "id": "det1", "enabled": False, "config": {"vram_limit_gb": 8}},
        {"name": "generator/x", "id": "gen1", "enabled": True, "config": {}},
        {"name": "tiled_detailer/sdxl2", "id": "det2", "enabled": True, "config": {"vram_limit_gb": 12}},
        {"name": "model_loader/krea2", "id": "loader1", "enabled": True, "config": {"vram_limit_gb": 6}},
    ]
    hints = active_pipe_vram_hints(pipes)
    assert hints == [PipeVramHint(pipe="det2", hint_gb=12.0), PipeVramHint(pipe="loader1", hint_gb=6.0)]


def test_active_pipe_vram_hints_empty_when_absent():
    assert active_pipe_vram_hints([{"name": "generator/x", "enabled": True, "config": {}}]) == []


# -- active device-override conflict (resolve_device_evidence's `pipes` arg) ------
#
# An active stage's own pinned `device`, conflicting with the backend's
# configured one, must make the device evidence for THIS request "unknown" -
# checked FIRST, before the backend/monitor's own kind-based resolution runs
# at all (see resolve_device_evidence's docstring). These tests exercise the
# ordering directly against a bare backend double (no real NativeBackend
# needed - the override check runs before `resolve_execution_device()` is
# even consulted for a conflicting case).

def _backend_with_device(device):
    return types.SimpleNamespace(config=types.SimpleNamespace(device=device))


def test_conflicting_active_override_forces_unknown_even_for_a_would_be_local_backend(monkeypatch):
    """A backend that WOULD resolve to a confident "local" reading (matched
    identity, real numbers) must still come back "unknown" once an active
    stage pins a conflicting device - the monitor here is never even READ."""
    monkeypatch.setattr(
        "src.features.generation.memory_advisory._resolve_execution_device_evidence",
        lambda backend: (_ for _ in ()).throw(AssertionError("must not be called - override short-circuits first")),
    )
    pipes = [{"name": "generator/x", "id": "gen1", "enabled": True, "config": {"device": "cuda:1"}}]
    monitor = types.SimpleNamespace(
        available=True,
        get_free_vram=lambda: (_ for _ in ()).throw(AssertionError("must not be called")),
        get_total_vram=lambda: (_ for _ in ()).throw(AssertionError("must not be called")),
    )

    device = resolve_device_evidence(_backend_with_device("cuda:0"), monitor, pipes)

    assert device.kind == "unknown"
    assert device.free_gb is None and device.total_gb is None
    assert "gen1" in device.provenance
    assert "cuda:1" in device.provenance and "cuda:0" in device.provenance


def test_conflicting_active_override_on_a_no_gpu_backend_is_unknown_not_none():
    """A CPU-configured backend normally reports a confident "none" - an
    active stage pinning a GPU device must upgrade that to "unknown", never
    leave it as the more confident "none"."""
    pipes = [{"name": "generator/x", "id": "gen1", "enabled": True, "config": {"device": "cuda:0"}}]
    device = resolve_device_evidence(_native_backend("cpu"), None, pipes)
    assert device.kind == "unknown"


def test_matching_active_override_leaves_the_ordinary_path_unchanged(monkeypatch):
    _patch_cuda_identity(monkeypatch, {0: GPU_0})
    pipes = [{"name": "generator/x", "id": "gen1", "enabled": True, "config": {"device": "cuda:0"}}]
    device = resolve_device_evidence(_native_backend("cuda:0"), _monitor(device_identity=GPU_0), pipes)
    assert device.kind == "local"


def test_absent_override_leaves_the_ordinary_path_unchanged(monkeypatch):
    _patch_cuda_identity(monkeypatch, {0: GPU_0})
    pipes = [{"name": "generator/x", "id": "gen1", "enabled": True, "config": {}}]
    device = resolve_device_evidence(_native_backend("cuda:0"), _monitor(device_identity=GPU_0), pipes)
    assert device.kind == "local"


def test_override_on_a_disabled_stage_is_never_consulted(monkeypatch):
    _patch_cuda_identity(monkeypatch, {0: GPU_0})
    pipes = [{"name": "generator/x", "id": "gen1", "enabled": False, "config": {"device": "cuda:1"}}]
    device = resolve_device_evidence(_native_backend("cuda:0"), _monitor(device_identity=GPU_0), pipes)
    assert device.kind == "local"


def test_non_string_templated_override_is_conservatively_unknown():
    """A non-string `device` (an unrendered template, or anything else this
    module can't compare as a plain literal) is treated the same as a real
    conflict - never assumed to match just because it isn't a differing
    string."""
    pipes = [{"name": "generator/x", "id": "gen1", "enabled": True, "config": {"device": {"unexpected": "shape"}}}]
    device = resolve_device_evidence(_backend_with_device("cuda:0"), None, pipes)
    assert device.kind == "unknown"
    assert "gen1" in device.provenance


def test_no_pipes_argument_is_backward_compatible():
    """Every existing 2-arg call site (and every test above this section)
    must keep working unchanged - `pipes` defaults to `None`, meaning "no
    override information available", not "no active pipes"."""
    device = resolve_device_evidence(_remote_backend(), None)
    assert device.kind == "remote"


# -- estimate_request_memory: coverage/known/unknown ----------------------------

def test_all_sizes_known_no_resolution():
    pipes = _pipes(active_refs=["ckpt1", "lora1"])
    result = estimate_request_memory(pipes, _lookup({"ckpt1": 2 * GIB, "lora1": 1 * GIB}), {})

    assert result.estimate.weights_gb == pytest.approx(3.0)
    assert result.estimate.activation_gb == 0.0
    assert result.estimate.checkpoint_estimate_gb == pytest.approx(round(3.0 * 1.1, 2))
    assert {k.ref for k in result.coverage.known} == {"ckpt1", "lora1"}
    assert result.coverage.unknown == []
    assert result.coverage.active_set_resolved is True


def test_partially_unknown_sizes_still_produce_an_estimate():
    pipes = _pipes(active_refs=["ckpt1", "lora1"])
    result = estimate_request_memory(pipes, _lookup({"ckpt1": 4 * GIB}), {})

    assert [k.ref for k in result.coverage.known] == ["ckpt1"]
    assert result.coverage.unknown == ["lora1"]
    assert result.estimate.checkpoint_estimate_gb == pytest.approx(round(4.0 * 1.1, 2))
    assert any("no indexed file size" in note for note in result.coverage.uncertainty)


def test_inactive_references_never_counted():
    """A disabled loader's selection and a non-loader pipe's own model refs
    (tracking/display only) must never inflate the estimate."""
    pipes = _pipes(active_refs=["ckpt1"], disabled_refs=["ckpt2"], non_loader_refs=["ckpt3"])
    result = estimate_request_memory(
        pipes, _lookup({"ckpt1": 1 * GIB, "ckpt2": 100 * GIB, "ckpt3": 100 * GIB}), {},
    )

    assert [k.ref for k in result.coverage.known] == ["ckpt1"]
    assert result.estimate.weights_gb == pytest.approx(1.0)


def test_dtype_quant_settings_surfaced_as_uncertainty():
    pipes = _pipes(active_refs=["ckpt1"], active_config_extra={"dtype": "fp8"})
    result = estimate_request_memory(pipes, _lookup({"ckpt1": 1 * GIB}), {})

    assert any("dtype" in note.lower() and "fp8" in note for note in result.coverage.uncertainty)


def test_no_model_sizes_resolvable_yields_null_estimate():
    pipes = _pipes(active_refs=["ckpt1"])
    result = estimate_request_memory(pipes, _lookup({}), {"resolution": "1024x1024"})

    assert result.estimate.checkpoint_estimate_gb is None
    assert result.coverage.known == []
    assert result.coverage.unknown == ["ckpt1"]


def test_unresolved_active_set_when_pipes_is_none():
    result = estimate_request_memory(None, _lookup({}), {})

    assert result.coverage.active_set_resolved is False
    assert result.estimate.checkpoint_estimate_gb is None
    assert any("could not be resolved" in note for note in result.coverage.uncertainty)


def test_activation_term_folds_into_estimate_when_weights_known():
    pipes = _pipes(active_refs=["ckpt1"])
    no_res = estimate_request_memory(pipes, _lookup({"ckpt1": 1 * GIB}), {})
    with_res = estimate_request_memory(pipes, _lookup({"ckpt1": 1 * GIB}), {"resolution": "1024x1024"})

    assert with_res.estimate.activation_gb > 0
    assert with_res.estimate.checkpoint_estimate_gb > no_res.estimate.checkpoint_estimate_gb


def test_pinned_components_note_always_present():
    pipes = _pipes(active_refs=["ckpt1"])
    result = estimate_request_memory(pipes, _lookup({"ckpt1": 1 * GIB}), {})

    assert result.coverage.pinned_components_uncounted is True
    assert any("pinned" in note.lower() for note in result.coverage.uncertainty)


# -- device evidence -------------------------------------------------------------
#
# Real `NativeBackend`/`NativeRemoteBackend` instances (not bare
# execution_device strings) so `resolve_device_evidence` exercises the
# REQ-01 `resolve_execution_device()` seam exactly as `preview_memory` calls
# it. `NativeBackend.resolve_execution_device()` calls the module-level
# `_cuda_device_identity(index)` to resolve a real CUDA UUID - monkeypatched
# below rather than touched for real, per docs/backends.md's device-identity
# section ("never touch real CUDA" in tests).

def _native_backend(device: str) -> NativeBackend:
    config = NativeBackendConfig(id="native_1", name="Local", device=device, dtype="float32", gpu_max_vram=10)
    return NativeBackend(config)


def _remote_backend() -> RemoteNativeBackend:
    return RemoteNativeBackend(NativeRemoteBackendConfig(id="remote_1", name="Remote"))


def _monitor(free_mb=8192, total_mb=24576, available=True, device_identity=None):
    return types.SimpleNamespace(
        available=available, device_identity=device_identity,
        get_free_vram=lambda: free_mb, get_total_vram=lambda: total_mb,
    )


def _patch_cuda_identity(monkeypatch, by_index: dict):
    """`_cuda_device_identity(index) -> by_index.get(index)` - the seam
    `NativeBackend.resolve_execution_device()` calls, patched module-level so
    no real `torch.cuda` call ever happens in this test file."""
    monkeypatch.setattr(
        "src.features.backends.native_backend._cuda_device_identity",
        lambda index: by_index.get(index),
    )


GPU_0 = DeviceIdentity(uuid="GPU-aaaa")
GPU_1 = DeviceIdentity(uuid="GPU-bbbb")


def test_device_evidence_local_reads_gpu_monitor_when_identity_matches(monkeypatch):
    _patch_cuda_identity(monkeypatch, {0: GPU_0})
    device = resolve_device_evidence(_native_backend("cuda:0"), _monitor(device_identity=GPU_0))

    assert device.kind == "local"
    assert device.free_gb == 8.0
    assert device.total_gb == 24.0
    assert device.provenance == "this host's GPU monitor"


def test_device_evidence_unknown_when_remapped_ordinal_but_identity_mismatches(monkeypatch):
    """A `NativeBackend` configured for cuda:1 that HAPPENS to enumerate at
    the same NVML index the monitor watches (a remapped ordinal) must NEVER
    be treated as a match on index alone - only a differing UUID proves it's
    a different physical card. Two distinct fake totals (24GB "GPU 0" vs the
    backend's own cuda:1) prove nothing is borrowed: the assertion-throwing
    monitor below must never even be READ from."""
    _patch_cuda_identity(monkeypatch, {1: GPU_1})
    monitor = types.SimpleNamespace(
        available=True, device_identity=GPU_0,  # a DIFFERENT physical card than GPU_1
        get_free_vram=lambda: (_ for _ in ()).throw(AssertionError("must not be called - identity mismatch")),
        get_total_vram=lambda: (_ for _ in ()).throw(AssertionError("must not be called - identity mismatch")),
    )
    device = resolve_device_evidence(_native_backend("cuda:1"), monitor)

    assert device.kind == "unknown"
    assert device.free_gb is None and device.total_gb is None
    assert device.provenance == "this backend's configured GPU is not the specific GPU this process monitors (identity mismatch)"


def test_device_evidence_unknown_when_backend_identity_unavailable(monkeypatch):
    """`_cuda_device_identity` returning `None` (torch/CUDA unavailable, or
    the index is out of range) must never be treated as "assume it matches"."""
    _patch_cuda_identity(monkeypatch, {})  # every index resolves to None
    monitor = types.SimpleNamespace(
        available=True, device_identity=GPU_0,
        get_free_vram=lambda: (_ for _ in ()).throw(AssertionError("must not be called - backend identity unresolved")),
        get_total_vram=lambda: (_ for _ in ()).throw(AssertionError("must not be called - backend identity unresolved")),
    )
    device = resolve_device_evidence(_native_backend("cuda:0"), monitor)

    assert device.kind == "unknown"
    assert "backend's GPU identity could not be established" in device.provenance


def test_device_evidence_bare_cuda_surfaces_the_backends_specific_reason():
    """A bare `"cuda"` device (no explicit `:N` ordinal) resolves
    `identity=None` with a SPECIFIC `reason` from the backend itself
    (REQ-01) - the advisory must surface that verbatim rather than its own
    generic "could not be established" wording, and never read any monitor
    numbers (there is no ordinal to attribute them to)."""
    monitor = types.SimpleNamespace(
        available=True, device_identity=GPU_0,
        get_free_vram=lambda: (_ for _ in ()).throw(AssertionError("must not be called - no explicit ordinal")),
        get_total_vram=lambda: (_ for _ in ()).throw(AssertionError("must not be called - no explicit ordinal")),
    )
    device = resolve_device_evidence(_native_backend("cuda"), monitor)

    assert device.kind == "unknown"
    assert device.free_gb is None and device.total_gb is None
    assert device.provenance == "configured device 'cuda' has no explicit ordinal; the worker's device cannot be established"


def test_device_evidence_unknown_when_monitor_identity_unavailable(monkeypatch):
    """NVML not reporting a UUID for the monitored device (`device_identity`
    is `None`) must never be treated as "assume it matches" either."""
    _patch_cuda_identity(monkeypatch, {0: GPU_0})
    monitor = types.SimpleNamespace(
        available=True, device_identity=None,
        get_free_vram=lambda: (_ for _ in ()).throw(AssertionError("must not be called - monitor identity unresolved")),
        get_total_vram=lambda: (_ for _ in ()).throw(AssertionError("must not be called - monitor identity unresolved")),
    )
    device = resolve_device_evidence(_native_backend("cuda:0"), monitor)

    assert device.kind == "unknown"
    assert "host's GPU identity could not be established" in device.provenance


def test_device_evidence_cpu_on_a_gpu_host_is_no_gpu_regardless_of_the_monitor():
    """`NativeBackendConfig(device="cpu")` is definite evidence of no GPU for
    THIS backend - even when the host's own monitor has a real GPU (proven
    unread via the assertion-throwing lambdas), it must never be consulted."""
    monitor = types.SimpleNamespace(
        available=True, device_identity=GPU_0,
        get_free_vram=lambda: (_ for _ in ()).throw(AssertionError("must not be called - backend is cpu")),
        get_total_vram=lambda: (_ for _ in ()).throw(AssertionError("must not be called - backend is cpu")),
    )
    device = resolve_device_evidence(_native_backend("cpu"), monitor)

    assert device.kind == "none"
    assert device.free_gb is None and device.total_gb is None
    assert device.provenance == "this backend is configured with no GPU"


def test_device_evidence_none_without_a_monitor_on_the_host(monkeypatch):
    _patch_cuda_identity(monkeypatch, {0: GPU_0})
    device = resolve_device_evidence(_native_backend("cuda:0"), None)
    assert device.kind == "none"
    assert device.free_gb is None

    device = resolve_device_evidence(_native_backend("cuda:0"), _monitor(available=False))
    assert device.kind == "none"


def test_device_evidence_remote_never_reads_this_host():
    monitor = types.SimpleNamespace(
        available=True, device_identity=GPU_0,
        get_free_vram=lambda: (_ for _ in ()).throw(AssertionError("must not be called")),
        get_total_vram=lambda: (_ for _ in ()).throw(AssertionError("must not be called")),
    )
    device = resolve_device_evidence(_remote_backend(), monitor)

    assert device.kind == "remote"
    assert device.free_gb is None and device.total_gb is None
    assert device.provenance == "not reported by the remote worker"


def test_device_evidence_unknown_for_undeclared_execution_device():
    """A test-double backend that doesn't even implement
    `resolve_execution_device()` (REQ-01) must fall back to "unestablished",
    never be inferred as local from a driver substring."""
    device = resolve_device_evidence(types.SimpleNamespace(), None)
    assert device.kind == "unknown"
    assert device.free_gb is None and device.total_gb is None
    assert device.provenance == "execution device not declared by this backend"


def test_device_evidence_unknown_for_comfyui_shaped_backend_with_remote_host():
    """A comfyui-driver backend pointed at a non-local host must never be
    read as this host's own GPU just because it runs in-process - it doesn't
    implement `resolve_execution_device()` at all. The monitor here returns
    REAL numbers (not an exception) - a broad `except Exception` around a
    driver-substring check could otherwise mask a real regression here."""
    comfyui_backend = types.SimpleNamespace(driver="comfyui", host="192.0.2.10")
    monitor = _monitor(device_identity=GPU_0)

    device = resolve_device_evidence(comfyui_backend, monitor)

    assert device.kind == "unknown"
    assert device.free_gb is None and device.total_gb is None


def test_device_evidence_none_on_read_failure_at_a_matching_identity(monkeypatch):
    _patch_cuda_identity(monkeypatch, {0: GPU_0})
    monitor = types.SimpleNamespace(
        available=True, device_identity=GPU_0,
        get_free_vram=lambda: (_ for _ in ()).throw(RuntimeError("nvml down")),
        get_total_vram=lambda: 24576,
    )
    device = resolve_device_evidence(_native_backend("cuda:0"), monitor)
    assert device.kind == "none"
    assert device.provenance == "could not read this host's GPU"


# -- budget evidence ---------------------------------------------------------------

def test_budget_not_configured_without_any_cap():
    device = DeviceEvidence(kind="local", free_gb=10.0, total_gb=24.0, provenance="x")
    budget = resolve_budget_evidence(None, [], device)
    assert budget.configured_gb is None
    assert budget.source == "not configured"
    assert budget.pipe_hints_gb == []


def test_budget_unbounded_when_no_device_evidence():
    device = DeviceEvidence(kind="remote", free_gb=None, total_gb=None, provenance="x")
    budget = resolve_budget_evidence(8.0, [], device)
    assert budget.configured_gb == 8.0
    assert "not bounded by device evidence" in budget.source


def test_budget_bounded_by_device_free_when_cap_exceeds_it():
    device = DeviceEvidence(kind="local", free_gb=6.0, total_gb=24.0, provenance="x")
    budget = resolve_budget_evidence(80.0, [], device)
    assert budget.configured_gb == 6.0


def test_budget_configured_gb_never_absorbs_a_pipe_hint():
    """The whole-request `configured_gb` must reflect ONLY the backend cap
    (bounded by device evidence) - a per-stage pipe hint, even a stricter
    one, must never silently lower it. That is what `pipe_hints_gb` is for."""
    device = DeviceEvidence(kind="local", free_gb=20.0, total_gb=24.0, provenance="x")
    budget = resolve_budget_evidence(16.0, [PipeVramHint(pipe="detailer", hint_gb=4.0)], device)

    assert budget.configured_gb == 16.0  # backend cap alone, bounded by 20GB free -> 16
    assert "per-stage" in budget.source
    assert "not merged into configured_gb" in budget.source


def test_budget_reports_each_pipe_hint_composed_individually():
    """Two active pipes with DIFFERENT hints must both be reported, each
    composed against the SAME backend cap/device evidence - never collapsed
    to one arbitrary value."""
    device = DeviceEvidence(kind="local", free_gb=20.0, total_gb=24.0, provenance="x")
    budget = resolve_budget_evidence(
        16.0,
        [PipeVramHint(pipe="loader1", hint_gb=4.0), PipeVramHint(pipe="detailer1", hint_gb=32.0)],
        device,
    )

    assert budget.pipe_hints_gb == [
        {"pipe": "loader1", "hint_gb": 4.0, "composed_gb": 4.0},  # hint (4) stricter than cap (16)
        {"pipe": "detailer1", "hint_gb": 32.0, "composed_gb": 16.0},  # cap (16) stricter than hint (32)
    ]


def test_budget_pipe_hints_reported_even_without_a_backend_cap():
    device = DeviceEvidence(kind="local", free_gb=20.0, total_gb=24.0, provenance="x")
    budget = resolve_budget_evidence(None, [PipeVramHint(pipe="loader1", hint_gb=4.0)], device)

    assert budget.configured_gb is None
    assert budget.pipe_hints_gb == [{"pipe": "loader1", "hint_gb": 4.0, "composed_gb": 4.0}]
