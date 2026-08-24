"""Tests for the model_loader/seedvr2 pipe.

SeedVR2 is the first native family with NO text encoder: the bundle carries the
DiT + self-normalizing VAE + a fixed prompt-embedding tensor. Two independent
MODELS cache keys (VAE / DiT), the embedding path folded into the DiT
fingerprint, and no ``clip`` output / no LoRAs. A fake MODELS service and a
patched embedding loader keep the test off disk.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import torch

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.model_loader.seedvr2.bundle import SeedVR2ModelBundle
from src.pipelines.pipes.model_loader.seedvr2.main import ModelLoaderSeedVR2Pipe


class _FakeModels:
    def __init__(self):
        self.calls = []

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        self.calls.append((key, fingerprint))
        return SimpleNamespace(module=object(), spec=None, estimated_vram_gb=1.0)


def _config(**over):
    cfg = ModelLoaderSeedVR2Pipe.get_default_config()
    cfg.update({
        "diffusion_model": {"file_path": "/m/seedvr2_3b.safetensors", "name": "seedvr2"},
        "vae": {"file_path": "/m/ema_vae.safetensors", "name": "vae"},
        "prompt_embedding": {"file_path": "/m/seedvr2_pos_emb.pt"},
    })
    cfg.update(over)
    return cfg


_EMB = torch.zeros((58, 5120))


def _run(pipe):
    models = _FakeModels()
    with patch(
        "src.pipelines.pipes.model_loader.seedvr2.main.load_seedvr2_prompt_embedding",
        return_value=_EMB,
    ) as loader:
        out = pipe.process(PipeInput(input={"MODELS": models}), lambda o: None)
    return models, out, loader


def test_name_and_single_model_output():
    assert ModelLoaderSeedVR2Pipe.name == "model_loader"
    out_names = {o.name: o.io_type for o in ModelLoaderSeedVR2Pipe.outputs()}
    assert out_names == {"model": IOType.MODEL}  # no clip output


def test_config_has_no_te_or_loras():
    names = {s.name for s in ModelLoaderSeedVR2Pipe.configuration()}
    assert "text_encoder" not in names
    assert "loras" not in names
    assert {"diffusion_model", "vae", "prompt_embedding"} <= names


def test_two_distinct_acquire_keys_vae_then_dit():
    models, _out, _loader = _run(ModelLoaderSeedVR2Pipe(config=_config()))
    keys = [k for k, _ in models.calls]
    assert keys == [
        "native/vae//m/ema_vae.safetensors",
        "native/dit//m/seedvr2_3b.safetensors",
    ]


def test_embedding_path_in_dit_fingerprint_only():
    models, _out, loader = _run(ModelLoaderSeedVR2Pipe(config=_config()))
    fps = dict(models.calls)
    dit_fp = fps["native/dit//m/seedvr2_3b.safetensors"]
    vae_fp = fps["native/vae//m/ema_vae.safetensors"]
    assert "emb:/m/seedvr2_pos_emb.pt" in dit_fp
    assert "emb:" not in vae_fp
    loader.assert_called_once_with("/m/seedvr2_pos_emb.pt")


def test_output_is_bundle_with_embedding():
    _models, out, _loader = _run(ModelLoaderSeedVR2Pipe(config=_config()))
    bundle = out.output["model"]
    assert isinstance(bundle, SeedVR2ModelBundle)
    assert bundle.te_encoder is None
    assert bundle.prompt_embedding is _EMB


def test_missing_file_paths_raise():
    cfg = _config()
    cfg["prompt_embedding"] = None
    with pytest.raises(ValueError):
        _run(ModelLoaderSeedVR2Pipe(config=cfg))
