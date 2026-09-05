"""Pure-function tests for `src.features.generation.memory_advisory`.

No orchestrator, no database, no GPU - `estimate_request_memory` takes a
built pipeline's processed pipes, a model lookup callable, and form data;
`resolve_device_evidence`/`resolve_budget_evidence` take plain values/fakes.
"""

import types

import pytest

from src.features.generation.memory_advisory import (
    active_model_ids,
    active_loader_settings,
    active_pipe_vram_hint_gb,
    estimate_request_memory,
    resolve_budget_evidence,
    resolve_device_evidence,
    DeviceEvidence,
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


def test_active_pipe_vram_hint_ignores_disabled_and_missing():
    pipes = [
        {"name": "tiled_detailer/sdxl", "enabled": False, "config": {"vram_limit_gb": 8}},
        {"name": "generator/x", "enabled": True, "config": {}},
        {"name": "tiled_detailer/sdxl2", "enabled": True, "config": {"vram_limit_gb": 12}},
    ]
    assert active_pipe_vram_hint_gb(pipes) == 12.0


def test_active_pipe_vram_hint_none_when_absent():
    assert active_pipe_vram_hint_gb([{"name": "generator/x", "enabled": True, "config": {}}]) is None


# -- estimate_request_memory: coverage/known/unknown ----------------------------

def test_all_sizes_known_no_resolution():
    pipes = _pipes(active_refs=["ckpt1", "lora1"])
    result = estimate_request_memory(pipes, _lookup({"ckpt1": 2 * GIB, "lora1": 1 * GIB}), {})

    assert result.estimate.weights_gb == pytest.approx(3.0)
    assert result.estimate.activation_gb == 0.0
    assert result.estimate.lower_bound_gb == pytest.approx(round(3.0 * 1.1, 2))
    assert {k.ref for k in result.coverage.known} == {"ckpt1", "lora1"}
    assert result.coverage.unknown == []
    assert result.coverage.active_set_resolved is True


def test_partially_unknown_sizes_are_a_lower_bound():
    pipes = _pipes(active_refs=["ckpt1", "lora1"])
    result = estimate_request_memory(pipes, _lookup({"ckpt1": 4 * GIB}), {})

    assert [k.ref for k in result.coverage.known] == ["ckpt1"]
    assert result.coverage.unknown == ["lora1"]
    assert result.estimate.lower_bound_gb == pytest.approx(round(4.0 * 1.1, 2))
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


def test_no_model_sizes_resolvable_yields_null_lower_bound():
    pipes = _pipes(active_refs=["ckpt1"])
    result = estimate_request_memory(pipes, _lookup({}), {"resolution": "1024x1024"})

    assert result.estimate.lower_bound_gb is None
    assert result.coverage.known == []
    assert result.coverage.unknown == ["ckpt1"]


def test_unresolved_active_set_when_pipes_is_none():
    result = estimate_request_memory(None, _lookup({}), {})

    assert result.coverage.active_set_resolved is False
    assert result.estimate.lower_bound_gb is None
    assert any("could not be resolved" in note for note in result.coverage.uncertainty)


def test_activation_term_folds_into_lower_bound_when_weights_known():
    pipes = _pipes(active_refs=["ckpt1"])
    no_res = estimate_request_memory(pipes, _lookup({"ckpt1": 1 * GIB}), {})
    with_res = estimate_request_memory(pipes, _lookup({"ckpt1": 1 * GIB}), {"resolution": "1024x1024"})

    assert with_res.estimate.activation_gb > 0
    assert with_res.estimate.lower_bound_gb > no_res.estimate.lower_bound_gb


def test_pinned_components_note_always_present():
    pipes = _pipes(active_refs=["ckpt1"])
    result = estimate_request_memory(pipes, _lookup({"ckpt1": 1 * GIB}), {})

    assert result.coverage.pinned_components_uncounted is True
    assert any("pinned" in note.lower() for note in result.coverage.uncertainty)


# -- device evidence -------------------------------------------------------------

def test_device_evidence_local_reads_gpu_monitor():
    monitor = types.SimpleNamespace(
        available=True,
        get_free_vram=lambda: 8192,
        get_total_vram=lambda: 24576,
    )
    device = resolve_device_evidence("this_host_gpu", monitor)

    assert device.kind == "local"
    assert device.free_gb == 8.0
    assert device.total_gb == 24.0
    assert device.provenance == "this host's GPU monitor"


def test_device_evidence_remote_never_reads_this_host():
    monitor = types.SimpleNamespace(
        available=True,
        get_free_vram=lambda: (_ for _ in ()).throw(AssertionError("must not be called")),
        get_total_vram=lambda: (_ for _ in ()).throw(AssertionError("must not be called")),
    )
    device = resolve_device_evidence("remote", monitor)

    assert device.kind == "remote"
    assert device.free_gb is None and device.total_gb is None
    assert device.provenance == "not reported by the remote worker"


def test_device_evidence_none_without_gpu():
    monitor = types.SimpleNamespace(available=False)
    device = resolve_device_evidence("this_host_gpu", monitor)

    assert device.kind == "none"
    assert device.free_gb is None


def test_device_evidence_unknown_for_undeclared_execution_device():
    """A backend that hasn't declared `execution_device` (REQ-01) - e.g. an
    in-process plugin backend like ComfyUI's, whose actual host is
    admin-configured and not something a driver NAME can tell you - must
    report `kind: "unknown"`, never be inferred as local from a driver
    substring."""
    device = resolve_device_evidence("unestablished", None)
    assert device.kind == "unknown"
    assert device.free_gb is None and device.total_gb is None
    assert device.provenance == "execution device not declared by this backend"


def test_device_evidence_unknown_for_comfyui_shaped_backend_with_remote_host():
    """A comfyui-driver backend pointed at a non-local host must never be
    read as this host's own GPU just because it runs in-process - it has not
    declared `execution_device` at all. The monitor here returns REAL numbers
    (not an exception) - a broad `except Exception` around a driver-substring
    check could otherwise mask a real regression here."""
    comfyui_backend = types.SimpleNamespace(driver="comfyui", host="192.0.2.10")
    monitor = types.SimpleNamespace(
        available=True,
        get_free_vram=lambda: 8192,
        get_total_vram=lambda: 24576,
    )
    execution_device = getattr(comfyui_backend, "execution_device", "unestablished")

    device = resolve_device_evidence(execution_device, monitor)

    assert device.kind == "unknown"
    assert device.free_gb is None and device.total_gb is None


def test_device_evidence_none_on_read_failure():
    monitor = types.SimpleNamespace(
        available=True,
        get_free_vram=lambda: (_ for _ in ()).throw(RuntimeError("nvml down")),
        get_total_vram=lambda: 24576,
    )
    device = resolve_device_evidence("this_host_gpu", monitor)
    assert device.kind == "none"


# -- budget evidence ---------------------------------------------------------------

def test_budget_not_configured_without_any_cap():
    device = DeviceEvidence(kind="local", free_gb=10.0, total_gb=24.0, provenance="x")
    budget = resolve_budget_evidence(None, None, device)
    assert budget.configured_gb is None
    assert budget.source == "not configured"


def test_budget_unbounded_when_no_device_evidence():
    device = DeviceEvidence(kind="remote", free_gb=None, total_gb=None, provenance="x")
    budget = resolve_budget_evidence(8.0, None, device)
    assert budget.configured_gb == 8.0
    assert "not bounded by device evidence" in budget.source


def test_budget_composes_stricter_cap_bounded_by_device_free():
    device = DeviceEvidence(kind="local", free_gb=6.0, total_gb=24.0, provenance="x")
    budget = resolve_budget_evidence(8.0, 4.0, device)
    assert budget.configured_gb == 4.0
    assert "backend gpu_max_vram" in budget.source
    assert "preset pipe hint" in budget.source
    assert "device free VRAM" in budget.source


def test_budget_bounded_by_device_free_when_caps_exceed_it():
    device = DeviceEvidence(kind="local", free_gb=6.0, total_gb=24.0, provenance="x")
    budget = resolve_budget_evidence(80.0, None, device)
    assert budget.configured_gb == 6.0
