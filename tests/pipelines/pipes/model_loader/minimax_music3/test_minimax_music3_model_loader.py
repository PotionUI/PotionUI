"""Tests for the model_loader/minimax_music3 pipe: three EAGER standalone-file
acquires at load time (DiT via NativeEngineLoader, DAV via NativeEngineLoader
`kind="audio_vae"`, fused TE via this family's OWN loader closure -- see
`te_loader.py`, deliberately NOT `text_encoders/loader.py`), and the returned
bundle wiring. No real weights -- `models.acquire` never calls its own
`loader` callable, matching `model_loader/minimax_h3`'s own test pattern."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import torch

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.model_loader.minimax_music3.bundle import MiniMaxMusic3ModelBundle
from src.pipelines.pipes.model_loader.minimax_music3.main import ModelLoaderMinimaxMusic3Pipe


class _FakeModels:
    def __init__(self):
        self.calls = []  # (key, fingerprint)

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        self.calls.append((key, fingerprint))
        return SimpleNamespace(module=object(), spec=None, estimated_vram_gb=1.0, compute_dtype=torch.bfloat16)


def _config():
    cfg = ModelLoaderMinimaxMusic3Pipe.get_default_config()
    cfg.update({
        "model": {"file_path": "/m/music3_dit.safetensors", "name": "music3_dit"},
        "text_encoder": {"file_path": "/m/music3_te.safetensors", "name": "music3_te"},
        "vae": {"file_path": "/m/music3_dav.safetensors", "name": "music3_dav"},
    })
    return cfg


def _run(cfg=None):
    models = _FakeModels()
    out = ModelLoaderMinimaxMusic3Pipe(cfg or _config()).process(PipeInput(input={"MODELS": models}), lambda o: None)
    return models, out


def test_name_and_outputs():
    assert ModelLoaderMinimaxMusic3Pipe.name == "model_loader"
    out = {o.name: o.io_type for o in ModelLoaderMinimaxMusic3Pipe.outputs()}
    assert out == {"model": IOType.MODEL}


def test_three_eager_standalone_component_acquires():
    # Unlike MiniMax-H3's TE, Music3's fused text encoder is acquired
    # EAGERLY too: there is no separate prompt_encoder stage whose own
    # conditioning cache could skip needing it (see main.py's module
    # docstring), so all three components load at process() time.
    models, _out = _run()
    keys = [k for k, _ in models.calls]
    assert keys == [
        "native/dit//m/music3_dit.safetensors",
        "native/audio_vae//m/music3_dav.safetensors",
        "native/te//m/music3_te.safetensors",
    ]


def test_returns_bundle_with_lm_cache_key():
    _models, out = _run()
    bundle = out.output["model"]
    assert isinstance(bundle, MiniMaxMusic3ModelBundle)
    assert bundle.lm_cache_key == "native/te//m/music3_te.safetensors"


def test_missing_required_path_raises():
    cfg = _config()
    cfg["vae"] = None
    try:
        ModelLoaderMinimaxMusic3Pipe(cfg).process(PipeInput(input={"MODELS": _FakeModels()}), lambda o: None)
        assert False, "expected a ValueError"
    except ValueError as e:
        assert "requires" in str(e)


def test_no_models_service_loads_directly():
    # Isolated pipe test path (no MODELS injected) -- loader.load() is
    # called directly for the DiT/DAV, and load_minimax_music3_te directly
    # for the TE, instead of going through models.acquire().
    cfg = _config()
    fake_component = SimpleNamespace(module=object(), spec=None, estimated_vram_gb=1.0, compute_dtype=torch.bfloat16)
    with patch(
        "src.pipelines.pipes.model_loader.minimax_music3.main.NativeEngineLoader.load",
        return_value=fake_component,
    ) as mock_load, patch(
        "src.pipelines.pipes.model_loader.minimax_music3.main.load_minimax_music3_te",
        return_value=fake_component,
    ) as mock_te:
        out = ModelLoaderMinimaxMusic3Pipe(cfg).process(PipeInput(input={}), lambda o: None)
        assert mock_load.call_count == 2  # dit, vae
        assert mock_te.call_count == 1
    assert isinstance(out.output["model"], MiniMaxMusic3ModelBundle)


def test_describe_models_names_every_component():
    pipe = ModelLoaderMinimaxMusic3Pipe(_config())
    names = {m.name for m in pipe.describe_models()}
    assert names == {"music3_dit", "music3_te", "music3_dav"}
