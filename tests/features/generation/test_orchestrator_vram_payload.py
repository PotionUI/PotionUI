"""VRAM snapshot + estimate the orchestrator attaches to generation.before_start."""

import types
from unittest.mock import MagicMock

import pytest

import src.features.models.repository as models_repo_mod
from src.features.generation.orchestrator import (
    GenerationOrchestrator,
    _WEIGHT_LOAD_MARGIN,
    _activation_headroom_gb,
    _estimate_generation_vram_gb,
    _frame_count,
    _parse_resolution,
)

GIB = 1024 ** 3


def _orchestrator(gpu_monitor=None):
    return GenerationOrchestrator(
        pipeline_builder=MagicMock(),
        backend_registry=MagicMock(),
        connection_hub=MagicMock(),
        settings=MagicMock(),
        output_processor=MagicMock(),
        preset_template_loader=MagicMock(),
        gpu_monitor=gpu_monitor,
    )


class _FakeRepo:
    def __init__(self, sizes):
        self._sizes = sizes  # model_id -> file_size (or None)

    def get_by_id(self, model_id, include_providers=True, include_tags=True):
        if model_id not in self._sizes:
            return None
        return types.SimpleNamespace(file_size=self._sizes[model_id])


# -- estimate: model-weight sum -------------------------------------------------

def test_estimate_none_without_model_refs():
    assert _estimate_generation_vram_gb({"prompt": "hi", "steps": 20}) is None


def test_estimate_sums_sizes_times_weight_margin_no_resolution(monkeypatch):
    monkeypatch.setattr(models_repo_mod, "model_repo",
                        _FakeRepo({"a": 2 * GIB, "b": 1 * GIB}))
    form_data = {"checkpoint": "model:a", "lora": "model:b"}

    est = _estimate_generation_vram_gb(form_data)

    assert est == pytest.approx(round(3 * _WEIGHT_LOAD_MARGIN, 2))


def test_estimate_none_only_when_no_size_resolvable(monkeypatch):
    # Model refs present but no sizes indexed, even with a resolution to price:
    # weights are the anchor, so this is still "nothing resolvable" -> None -> evict.
    monkeypatch.setattr(models_repo_mod, "model_repo", _FakeRepo({"a": None}))
    assert _estimate_generation_vram_gb({"checkpoint": "model:a", "resolution": "1024x1024"}) is None


def test_estimate_partial_sizes_are_a_lower_bound(monkeypatch):
    # "b" unknown -> skipped; the known sum is returned (lower bound).
    monkeypatch.setattr(models_repo_mod, "model_repo", _FakeRepo({"a": 4 * GIB}))
    form_data = {"checkpoint": "model:a", "lora": "model:b"}

    est = _estimate_generation_vram_gb(form_data)

    assert est == pytest.approx(round(4 * _WEIGHT_LOAD_MARGIN, 2))


def test_estimate_adds_resolution_activation_term(monkeypatch):
    # Krea-2 shape: diffusion model + TE + VAE are all model-picker refs.
    monkeypatch.setattr(models_repo_mod, "model_repo",
                        _FakeRepo({"dit": 9 * GIB, "te": 4 * GIB, "vae": 1 * GIB}))
    form_data = {
        "diffusion_model": "model:dit",
        "text_encoder": "model:te",
        "vae": "model:vae",
        "resolution": "1024x1024",
    }

    est = _estimate_generation_vram_gb(form_data)

    weights = 14 * _WEIGHT_LOAD_MARGIN
    activation = _activation_headroom_gb(form_data)
    assert est == pytest.approx(round(weights + activation, 2))
    assert activation == pytest.approx(1.6, abs=0.05)  # ~1.6 GB at 1024^2


# -- activation term ------------------------------------------------------------

def test_activation_zero_without_resolution():
    assert _activation_headroom_gb({"checkpoint": "model:a"}) == 0.0


def test_activation_monotone_in_resolution():
    small = _activation_headroom_gb({"resolution": "512x512"})
    large = _activation_headroom_gb({"resolution": "1024x1024"})
    assert 0 < small < large


def test_activation_scales_with_frames():
    still = _activation_headroom_gb({"resolution": "1024x1024"})
    video = _activation_headroom_gb({"resolution": "1024x1024", "num_frames": 16})
    assert video > still


def test_parse_resolution_string_and_ints():
    assert _parse_resolution({"resolution": "1024x768"}) == (1024, 768)
    assert _parse_resolution({"width": 1280, "height": 720}) == (1280, 720)
    assert _parse_resolution({"resolution": "not-a-res"}) is None
    assert _parse_resolution({"steps": 20}) is None


def test_frame_count_defaults_to_one():
    assert _frame_count({"steps": 20}) == 1
    assert _frame_count({"num_frames": 49}) == 49
    assert _frame_count({"length": 0}) == 1  # non-positive ignored


# -- VRAM read ------------------------------------------------------------------

def test_read_vram_none_without_gpu_monitor():
    assert _orchestrator(gpu_monitor=None)._read_vram_gb() == (None, None)


def test_read_vram_converts_mb_to_gb():
    gpu = MagicMock()
    gpu.get_free_vram.return_value = 8192   # MB
    gpu.get_total_vram.return_value = 24576  # MB

    free_gb, total_gb = _orchestrator(gpu_monitor=gpu)._read_vram_gb()

    assert (free_gb, total_gb) == (8.0, 24.0)


def test_read_vram_soft_fails_on_error():
    gpu = MagicMock()
    gpu.get_free_vram.side_effect = RuntimeError("nvml down")

    assert _orchestrator(gpu_monitor=gpu)._read_vram_gb() == (None, None)
