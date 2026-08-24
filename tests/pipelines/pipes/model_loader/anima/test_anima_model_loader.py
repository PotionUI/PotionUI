"""Tests for the model_loader/anima pipe.

Mirrors the Qwen loader: three independent MODELS cache keys (TE / VAE / DiT), a
LoRA change busting only the DiT fingerprint, and the dtype folded into every
fingerprint. A fake MODELS service records the acquire calls so no checkpoints
are touched.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.model_loader.anima.bundle import AnimaModelBundle
from src.pipelines.pipes.model_loader.anima.anima_clip import AnimaClipTextEncoder
from src.pipelines.pipes.model_loader.anima.main import ModelLoaderAnimaPipe


class _FakeModels:
    def __init__(self):
        self.calls = []

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        self.calls.append((key, fingerprint))
        return SimpleNamespace(module=object(), spec=None, estimated_vram_gb=1.0)


def _config(loras=None, **over):
    cfg = ModelLoaderAnimaPipe.get_default_config()
    cfg.update({
        "diffusion_model": {"file_path": "/m/anima.safetensors", "name": "anima"},
        "text_encoder": {"file_path": "/m/qwen3_06b.safetensors", "name": "te"},
        "vae": {"file_path": "/m/wan21_vae.safetensors", "name": "vae"},
        "loras": loras or [],
    })
    cfg.update(over)
    return cfg


def _run(pipe):
    models = _FakeModels()
    out = pipe.process(PipeInput(input={"MODELS": models}), lambda o: None)
    return models, out


def test_name_and_outputs():
    assert ModelLoaderAnimaPipe.name == "model_loader"
    out_names = {o.name: o.io_type for o in ModelLoaderAnimaPipe.outputs()}
    assert out_names["model"] == IOType.MODEL
    assert out_names["text_encoder"] == IOType.TEXT_ENCODER


def test_single_text_encoder_no_clip_l():
    names = {s.name for s in ModelLoaderAnimaPipe.configuration()}
    assert "clip_l" not in names
    assert {"diffusion_model", "text_encoder", "vae", "loras"} <= names


def test_three_distinct_acquire_keys():
    models, _ = _run(ModelLoaderAnimaPipe(config=_config()))
    keys = [k for k, _ in models.calls]
    assert keys == [
        "native/te//m/qwen3_06b.safetensors",
        "native/vae//m/wan21_vae.safetensors",
        "native/dit//m/anima.safetensors",
    ]


def test_outputs_are_bundle_and_clip():
    _models, out = _run(ModelLoaderAnimaPipe(config=_config()))
    assert isinstance(out.output["model"], AnimaModelBundle)
    assert isinstance(out.output["text_encoder"], AnimaClipTextEncoder)


def test_bundle_carries_the_te_cache_key():
    """generator/anima's TE eviction reads bundle.te_cache_key -- it
    must match the exact key the TE was acquire()'d under, or evict_dead_weight
    would target the wrong (or no) cache entry."""
    _models, out = _run(ModelLoaderAnimaPipe(config=_config()))
    assert out.output["model"].te_cache_key == "native/te//m/qwen3_06b.safetensors"


def test_missing_file_paths_raise():
    cfg = _config()
    cfg["vae"] = None
    with pytest.raises(ValueError):
        _run(ModelLoaderAnimaPipe(config=cfg))


def _fps(loras):
    models, _ = _run(ModelLoaderAnimaPipe(config=_config(loras=loras)))
    return dict(zip([k for k, _ in models.calls], [f for _, f in models.calls]))


def test_lora_change_busts_only_dit():
    no_lora = _fps([])
    with_lora = _fps([{"model": "/m/style.safetensors", "strength": 0.8}])
    te = "native/te//m/qwen3_06b.safetensors"
    vae = "native/vae//m/wan21_vae.safetensors"
    dit = "native/dit//m/anima.safetensors"
    assert no_lora[te] == with_lora[te]
    assert no_lora[vae] == with_lora[vae]
    assert no_lora[dit] != with_lora[dit]
    assert "style.safetensors@0.8" in with_lora[dit]


def test_dtype_in_all_fingerprints():
    models, _ = _run(ModelLoaderAnimaPipe(config=_config(dtype="float16")))
    assert all("float16" in fp for _, fp in models.calls)


def test_zero_weight_lora_ignored():
    fps = _fps([{"model": "/m/off.safetensors", "strength": 0.0}])
    assert "off.safetensors" not in fps["native/dit//m/anima.safetensors"]
