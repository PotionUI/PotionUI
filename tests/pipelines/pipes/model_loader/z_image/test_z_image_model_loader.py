"""Tests for the model_loader/z_image pipe.

Mirrors the Qwen/Anima loaders: three independent MODELS cache keys (TE / VAE /
DiT), a LoRA change busting only the DiT fingerprint, and the dtype folded into
every fingerprint. A fake MODELS service records the acquire calls so no
checkpoints are touched.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.model_loader.z_image.bundle import ZImageModelBundle
from src.pipelines.pipes.model_loader.z_image.z_image_clip import ZImageClipTextEncoder
from src.pipelines.pipes.model_loader.z_image.main import ModelLoaderZImagePipe


class _FakeModels:
    def __init__(self):
        self.calls = []

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        self.calls.append((key, fingerprint))
        return SimpleNamespace(module=object(), spec=None, estimated_vram_gb=1.0)


def _config(loras=None, **over):
    cfg = ModelLoaderZImagePipe.get_default_config()
    cfg.update({
        "diffusion_model": {"file_path": "/m/z_image.safetensors", "name": "z_image"},
        "text_encoder": {"file_path": "/m/qwen3_4b.safetensors", "name": "te"},
        "vae": {"file_path": "/m/z_image_vae.safetensors", "name": "vae"},
        "loras": loras or [],
    })
    cfg.update(over)
    return cfg


def _run(pipe):
    models = _FakeModels()
    out = pipe.process(PipeInput(input={"MODELS": models}), lambda o: None)
    return models, out


def test_name_and_outputs():
    assert ModelLoaderZImagePipe.name == "model_loader"
    out_names = {o.name: o.io_type for o in ModelLoaderZImagePipe.outputs()}
    assert out_names["model"] == IOType.MODEL
    assert out_names["text_encoder"] == IOType.TEXT_ENCODER


def test_single_text_encoder_no_clip_l():
    names = {s.name for s in ModelLoaderZImagePipe.configuration()}
    assert "clip_l" not in names
    assert {"diffusion_model", "text_encoder", "vae", "loras"} <= names


def test_process_eagerly_acquires_only_vae_and_dit():
    """The TE's acquire() is deferred (see ZImageClipTextEncoder) -- process()
    itself must only ever touch VAE + DiT, never the TE."""
    models, _ = _run(ModelLoaderZImagePipe(config=_config()))
    keys = [k for k, _ in models.calls]
    assert keys == [
        "native/vae//m/z_image_vae.safetensors",
        "native/dit//m/z_image.safetensors",
    ]


def test_te_acquire_deferred_until_encoder_is_read():
    models, out = _run(ModelLoaderZImagePipe(config=_config()))
    clip = out.output["text_encoder"]
    _ = clip.encoder
    _ = clip.encoder  # a second read must not re-acquire
    te_calls = [k for k, _ in models.calls if k == "native/te//m/qwen3_4b.safetensors|zimage"]
    assert len(te_calls) == 1


def test_te_never_acquired_when_encoder_is_never_read():
    models, out = _run(ModelLoaderZImagePipe(config=_config()))
    assert not any(k == "native/te//m/qwen3_4b.safetensors|zimage" for k, _ in models.calls)
    _ = out.output["text_encoder"].encoder
    assert any(k == "native/te//m/qwen3_4b.safetensors|zimage" for k, _ in models.calls)


def test_outputs_are_bundle_and_clip():
    _models, out = _run(ModelLoaderZImagePipe(config=_config()))
    assert isinstance(out.output["model"], ZImageModelBundle)
    assert isinstance(out.output["text_encoder"], ZImageClipTextEncoder)


def test_bundle_unload_tolerates_a_never_acquired_te():
    _models, out = _run(ModelLoaderZImagePipe(config=_config()))
    out.output["model"].unload()


def test_bundle_carries_the_te_cache_key():
    """generator/z_image's TE eviction reads bundle.te_cache_key -- it
    must match the exact key the TE was acquire()'d under, or evict_dead_weight
    would target the wrong (or no) cache entry."""
    _models, out = _run(ModelLoaderZImagePipe(config=_config()))
    assert out.output["model"].te_cache_key == "native/te//m/qwen3_4b.safetensors|zimage"


def test_missing_file_paths_raise():
    cfg = _config()
    cfg["vae"] = None
    with pytest.raises(ValueError):
        _run(ModelLoaderZImagePipe(config=cfg))


def _fps(loras):
    models, out = _run(ModelLoaderZImagePipe(config=_config(loras=loras)))
    _ = out.output["text_encoder"].encoder  # force the deferred TE acquire
    return dict(zip([k for k, _ in models.calls], [f for _, f in models.calls]))


def test_lora_change_busts_only_dit():
    no_lora = _fps([])
    with_lora = _fps([{"model": "/m/style.safetensors", "strength": 0.8}])
    te = "native/te//m/qwen3_4b.safetensors|zimage"
    vae = "native/vae//m/z_image_vae.safetensors"
    dit = "native/dit//m/z_image.safetensors"
    assert no_lora[te] == with_lora[te]
    assert no_lora[vae] == with_lora[vae]
    assert no_lora[dit] != with_lora[dit]
    assert "style.safetensors@0.8" in with_lora[dit]


def test_dtype_in_all_fingerprints():
    models, _ = _run(ModelLoaderZImagePipe(config=_config(dtype="float16")))
    assert all("float16" in fp for _, fp in models.calls)


def test_zero_weight_lora_ignored():
    fps = _fps([{"model": "/m/off.safetensors", "strength": 0.0}])
    assert "off.safetensors" not in fps["native/dit//m/z_image.safetensors"]
