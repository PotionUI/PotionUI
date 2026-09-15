"""Tests for the model_loader/yue2 pipe: two EAGER standalone-file acquires
at load time (AR/NAR backbone via NativeEngineLoader `kind="diffusion_model"`,
VAE decoder via `kind="audio_vae"`), the latent_dim=64 admission guard, the
once-only tokenizer attachment, and the returned bundle wiring. No real
weights -- `models.acquire` never calls its own `loader` callable."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import torch

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.model_loader.yue2.bundle import YuE2ModelBundle
from src.pipelines.pipes.model_loader.yue2.main import ModelLoaderYuE2Pipe

MODULE = "src.pipelines.pipes.model_loader.yue2.main"


def _lm_module(latent_dim=64):
    return SimpleNamespace(cfg=SimpleNamespace(latent_dim=latent_dim))


class _FakeModels:
    def __init__(self, latent_dim=64):
        self.calls = []
        self._latent_dim = latent_dim

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        self.calls.append((key, fingerprint))
        if "dit" in key:
            return SimpleNamespace(
                module=_lm_module(self._latent_dim), spec=None,
                estimated_vram_gb=1.0, compute_dtype=torch.bfloat16, tokenizer=None,
            )
        return SimpleNamespace(module=object(), spec=None, estimated_vram_gb=1.0, compute_dtype=torch.float32)


def _config():
    cfg = ModelLoaderYuE2Pipe.get_default_config()
    cfg.update({
        "model": {"file_path": "/m/yue2_lm.safetensors", "name": "yue2_lm"},
        "vae": {"file_path": "/m/yue2_vae.safetensors", "name": "yue2_vae"},
    })
    return cfg


def _run(cfg=None, models=None):
    models = models or _FakeModels()
    with patch(f"{MODULE}.YuE2Tokenizer", return_value=object()):
        out = ModelLoaderYuE2Pipe(cfg or _config()).process(PipeInput(input={"MODELS": models}), lambda o: None)
    return models, out


def test_name_and_outputs():
    assert ModelLoaderYuE2Pipe.name == "model_loader"
    out = {o.name: o.io_type for o in ModelLoaderYuE2Pipe.outputs()}
    assert out == {"model": IOType.MODEL}


def test_two_eager_standalone_component_acquires():
    models, _out = _run()
    keys = [k for k, _ in models.calls]
    assert keys == [
        "native/dit//m/yue2_lm.safetensors",
        "native/audio_vae//m/yue2_vae.safetensors",
    ]


def test_returns_bundle_with_lm_cache_key():
    _models, out = _run()
    bundle = out.output["model"]
    assert isinstance(bundle, YuE2ModelBundle)
    assert bundle.lm_cache_key == "native/dit//m/yue2_lm.safetensors"


def test_tokenizer_attached_once():
    models = _FakeModels()
    with patch(f"{MODULE}.YuE2Tokenizer") as mock_tok:
        mock_tok.return_value = object()
        out = ModelLoaderYuE2Pipe(_config()).process(PipeInput(input={"MODELS": models}), lambda o: None)
        assert mock_tok.call_count == 1
        bundle = out.output["model"]
        assert bundle.tokenizer is mock_tok.return_value


def test_tokenizer_not_rebuilt_when_already_present():
    tokenizer = object()
    models = _FakeModels()
    models.acquire = lambda key, fingerprint, loader, estimated_vram_gb=None: (
        SimpleNamespace(module=_lm_module(), spec=None, tokenizer=tokenizer)
        if "dit" in key else SimpleNamespace(module=object(), spec=None)
    )
    with patch(f"{MODULE}.YuE2Tokenizer") as mock_tok:
        _models, out = _run(models=models)
        mock_tok.assert_not_called()
    assert out.output["model"].tokenizer is tokenizer


def test_wrong_latent_dim_raises():
    models = _FakeModels(latent_dim=128)
    try:
        _run(models=models)
        assert False, "expected a ValueError"
    except ValueError as e:
        assert "latent_dim" in str(e)


def test_missing_required_path_raises():
    cfg = _config()
    cfg["vae"] = None
    try:
        ModelLoaderYuE2Pipe(cfg).process(PipeInput(input={"MODELS": _FakeModels()}), lambda o: None)
        assert False, "expected a ValueError"
    except ValueError as e:
        assert "requires" in str(e)


def test_no_models_service_loads_directly():
    cfg = _config()
    fake_lm = SimpleNamespace(module=_lm_module(), spec=None, tokenizer=None)
    fake_vae = SimpleNamespace(module=object(), spec=None)

    with patch(f"{MODULE}.NativeEngineLoader.load", side_effect=[fake_lm, fake_vae]) as mock_load, \
         patch(f"{MODULE}.YuE2Tokenizer", return_value=object()):
        out = ModelLoaderYuE2Pipe(cfg).process(PipeInput(input={}), lambda o: None)
        assert mock_load.call_count == 2

    assert isinstance(out.output["model"], YuE2ModelBundle)


def test_describe_models_names_every_component():
    pipe = ModelLoaderYuE2Pipe(_config())
    names = {m.name for m in pipe.describe_models()}
    assert names == {"yue2_lm", "yue2_vae"}
