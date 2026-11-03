from __future__ import annotations

from unittest.mock import Mock

import pytest
import torch

from src.features.backends.backend_config import NativeBackendConfig
from src.features.backends.native_backend import NativeBackend
from src.features.backends.native_hardware import detect_native_hardware_defaults
from src.platform.runtime.native.memory import residency
from src.platform.runtime.native.memory.residency import module_size_gb, run_text_encode
from tests.fixtures.kernel_capabilities import ada, fake_capabilities
from tests.platform.runtime.native import _review_fit_layouts as L

HISTORICAL_ENGINE_FIELDS = {
    "device": {"label": "Device", "type": "string", "secret": False},
    "dtype": {"label": "Precision", "type": "string", "secret": False,
              "options": ["float32", "float16", "bfloat16"]},
    "gpu_max_vram": {"label": "GPU Max VRAM (GB)", "type": "number", "secret": False},
}


@pytest.fixture(autouse=True)
def _clean_state():
    assert residency.minimum_inference_memory_gb() == L.RESERVE_GB
    residency.get_residency_registry().clear()
    L.reset_placement()
    detect_native_hardware_defaults.cache_clear()
    yield
    detect_native_hardware_defaults.cache_clear()
    L.reset_placement()
    residency.get_residency_registry().clear()


def _backend(cfg: NativeBackendConfig) -> NativeBackend:
    engine = Mock()
    engine.gpu_monitor = Mock()
    return NativeBackend(backend_config=cfg, generation_engine=engine)


def _fake_host(monkeypatch, install_capabilities, total_gb: float) -> None:
    install_capabilities(fake_capabilities(ada()))
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "get_device_name", lambda *a: "Fake GPU")
    monkeypatch.setattr(torch.cuda, "get_device_capability", lambda *a: (8, 9))
    monkeypatch.setattr(residency, "total_vram_gb", lambda device=None: total_gb)
    detect_native_hardware_defaults.cache_clear()


def test_prepare_pipes_injects_only_the_historical_pipe_keys():
    cfg = NativeBackendConfig(id="native", name="Local Generation", device="cuda:0", dtype="bfloat16", gpu_max_vram=10)
    pipes = [{"config": None}, {"config": {"device": "cuda:1"}}, {}]

    out = _backend(cfg).prepare_pipes(pipes)

    assert [p["config"] for p in out] == [
        {"device": "cuda:0", "dtype": "bfloat16", "vram_limit_gb": 10},
        {"device": "cuda:1", "dtype": "bfloat16", "vram_limit_gb": 10},
        {"device": "cuda:0", "dtype": "bfloat16", "vram_limit_gb": 10},
    ]


def test_prepare_pipes_sets_the_historical_gpu_monitor_cap():
    cfg = NativeBackendConfig(id="native", name="Local Generation", device="cuda:0", gpu_max_vram=14)
    backend = _backend(cfg)

    backend.prepare_pipes([])

    backend.generation_engine.gpu_monitor.set_vram_cap_gb.assert_called_once_with(14)


def test_historical_engine_fields_are_unchanged():
    fields = {f["name"]: f for f in NativeBackendConfig.engine_fields()}

    assert list(fields)[:3] == ["device", "dtype", "gpu_max_vram"]
    for name, expected in HISTORICAL_ENGINE_FIELDS.items():
        for key, value in expected.items():
            assert fields[name][key] == value, (name, key)


def test_hardware_default_gpu_max_vram_is_total_minus_reserve(monkeypatch, install_capabilities):
    _fake_host(monkeypatch, install_capabilities, 15.99)

    cfg = NativeBackendConfig(id="native", name="Local Generation", device="cuda:0")

    assert cfg.gpu_max_vram == 14


def test_default_backend_keeps_the_whole_move_for_an_encoder_that_fits_after_eviction(monkeypatch, install_capabilities):
    total = 15.99
    _fake_host(monkeypatch, install_capabilities, total)
    cfg = NativeBackendConfig(id="native", name="Local Generation", device="cuda:0")
    _backend(cfg).prepare_pipes([{"config": {}}])
    log: list = []
    encoder = L.qwen3_8b(log)
    size = module_size_gb(encoder)
    registry = L.fake_card(monkeypatch, log, total)
    L.intercept_streaming(monkeypatch, log)
    resident = L.ResidentModel(log)
    registry.note_resident(resident, L.DEV, total - L.CTX_GB - size)
    log.clear()
    assert size + L.RESERVE_GB <= total - L.CTX_GB

    run_text_encode(encoder, L.DEV, lambda: encoder.encode(["a prompt"]))

    assert log == [
        ("ensure_free", L.DEV, round(size * 1.5 + L.RESERVE_GB, 6), round(size, 6)),
        ("dit.offload",),
        ("empty_cache",),
    ] + L.whole_move(encoder, size, free_gb=size, path="after-evict")
