from __future__ import annotations

from types import SimpleNamespace

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.model_loader.qwen_image21.main import ModelLoaderQwenImage21Pipe
from src.pipelines.pipes.model_loader.qwen_image21.bundle import QwenImage21ModelBundle
from src.pipelines.pipes.model_loader.qwen_image21.qwen_image21_clip import QwenImage21ClipTextEncoder


class _FakeModels:
    def __init__(self):
        self.calls = []

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        self.calls.append((key, fingerprint))
        return SimpleNamespace(module=object(), spec=None, estimated_vram_gb=1.0)


def _config(loras=None, **over):
    cfg = ModelLoaderQwenImage21Pipe.get_default_config()
    cfg.update({
        "diffusion_model": {"file_path": "/m/dit.safetensors", "name": "dit"},
        "text_encoder": {"file_path": "/m/te.safetensors", "name": "te"},
        "vae": {"file_path": "/m/vae.safetensors", "name": "vae"},
        "loras": loras or [],
    })
    cfg.update(over)
    return cfg


def _run(pipe):
    models = _FakeModels()
    out = pipe.process(PipeInput(input={"MODELS": models}), lambda o: None)
    return models, out


def test_name_and_outputs():
    assert ModelLoaderQwenImage21Pipe.name == "model_loader"
    out_names = {o.name: o.io_type for o in ModelLoaderQwenImage21Pipe.outputs()}
    assert out_names["model"] == IOType.MODEL
    assert out_names["text_encoder"] == IOType.TEXT_ENCODER


def test_no_clip_l_in_config():
    names = {s.name for s in ModelLoaderQwenImage21Pipe.configuration()}
    assert "clip_l" not in names
    assert {"diffusion_model", "text_encoder", "vae", "loras", "vision"} <= names


def test_process_eagerly_acquires_only_vae_and_dit():
    models, out = _run(ModelLoaderQwenImage21Pipe(config=_config()))
    keys = [k for k, _ in models.calls]
    assert len(keys) == 2 and len(set(keys)) == 2
    assert keys[0] == "native/vae//m/vae.safetensors"
    assert keys[1] == "native/dit//m/dit.safetensors"


def test_te_acquire_deferred_until_encoder_is_read():
    models, out = _run(ModelLoaderQwenImage21Pipe(config=_config()))
    assert [k for k, _ in models.calls] == [
        "native/vae//m/vae.safetensors", "native/dit//m/dit.safetensors",
    ]
    clip = out.output["text_encoder"]
    _ = clip.encoder
    _ = clip.encoder
    te_calls = [k for k, _ in models.calls if k == "native/te//m/te.safetensors"]
    assert len(te_calls) == 1


def test_te_never_acquired_when_encoder_is_never_read():
    models, out = _run(ModelLoaderQwenImage21Pipe(config=_config()))
    assert not any(k == "native/te//m/te.safetensors" for k, _ in models.calls)
    _ = out.output["text_encoder"].encoder
    assert any(k == "native/te//m/te.safetensors" for k, _ in models.calls)


def test_outputs_are_bundle_and_clip():
    _models, out = _run(ModelLoaderQwenImage21Pipe(config=_config()))
    assert isinstance(out.output["model"], QwenImage21ModelBundle)
    assert isinstance(out.output["text_encoder"], QwenImage21ClipTextEncoder)


def test_bundle_unload_tolerates_a_never_acquired_te():
    _models, out = _run(ModelLoaderQwenImage21Pipe(config=_config()))
    out.output["model"].unload()


def test_bundle_carries_the_te_cache_key():
    _models, out = _run(ModelLoaderQwenImage21Pipe(config=_config()))
    assert out.output["model"].te_cache_key == "native/te//m/te.safetensors"


def test_missing_file_paths_raise():
    cfg = _config()
    cfg["vae"] = None
    import pytest
    with pytest.raises(ValueError):
        _run(ModelLoaderQwenImage21Pipe(config=cfg))


def _fps(loras):
    models, out = _run(ModelLoaderQwenImage21Pipe(config=_config(loras=loras)))
    _ = out.output["text_encoder"].encoder
    return dict(zip([k for k, _ in models.calls], [f for _, f in models.calls]))


def test_lora_change_busts_only_dit():
    no_lora = _fps([])
    with_lora = _fps([{"model": "/m/style.safetensors", "strength": 0.8}])

    te_key = "native/te//m/te.safetensors"
    vae_key = "native/vae//m/vae.safetensors"
    dit_key = "native/dit//m/dit.safetensors"

    assert no_lora[te_key] == with_lora[te_key]
    assert no_lora[vae_key] == with_lora[vae_key]
    assert no_lora[dit_key] != with_lora[dit_key]
    assert "style.safetensors@0.8" in with_lora[dit_key]


def test_dtype_in_all_fingerprints():
    models, _ = _run(ModelLoaderQwenImage21Pipe(config=_config(dtype="float16")))
    assert all("float16" in fp for _, fp in models.calls)


def test_zero_weight_lora_ignored():
    fps = _fps([{"model": "/m/off.safetensors", "strength": 0.0}])
    dit_fp = fps["native/dit//m/dit.safetensors"]
    assert "off.safetensors" not in dit_fp


def test_runs_without_models_service():
    pipe = ModelLoaderQwenImage21Pipe(config=_config())
    assert pipe.describe_models()


# -- vision fingerprint hazard -----------------------------


def test_vision_defaults_off_and_absent_from_fingerprint_is_still_distinguishable():
    models, out = _run(ModelLoaderQwenImage21Pipe(config=_config()))
    _ = out.output["text_encoder"].encoder
    te_fp_default = dict(models.calls)["native/te//m/te.safetensors"]
    models2, out2 = _run(ModelLoaderQwenImage21Pipe(config=_config(vision=False)))
    _ = out2.output["text_encoder"].encoder
    te_fp_explicit_false = dict(models2.calls)["native/te//m/te.safetensors"]
    assert te_fp_default == te_fp_explicit_false


def test_vision_true_changes_only_the_te_fingerprint():
    """A text-only and a vision-enabled load of the SAME text-encoder path
    must NOT alias to the same model-lifecycle cache entry."""
    no_vision = _fps([])
    models, out = _run(ModelLoaderQwenImage21Pipe(config=_config(vision=True)))
    _ = out.output["text_encoder"].encoder
    with_vision = dict(models.calls)

    te_key = "native/te//m/te.safetensors"
    vae_key = "native/vae//m/vae.safetensors"
    dit_key = "native/dit//m/dit.safetensors"

    assert no_vision[te_key] != with_vision[te_key]
    assert "vision=True" in with_vision[te_key]
    assert "vision=False" in no_vision[te_key]
    assert no_vision[vae_key] == with_vision[vae_key]
    assert no_vision[dit_key] == with_vision[dit_key]


def test_vision_flag_threaded_to_the_engine_loader(monkeypatch):
    """``load_te``'s closure must actually pass ``vision=`` down to
    ``NativeEngineLoader.load``."""
    received = {}

    class _FakeLoader:
        def __init__(self, *a, **kw):
            pass

        def load(self, path, kind, **kwargs):
            if kind == "text_encoder":
                received.update(kwargs)
            return SimpleNamespace(module=object(), spec=None, estimated_vram_gb=1.0)

    import src.pipelines.pipes.model_loader.qwen_image21.main as qi21_main
    monkeypatch.setattr(qi21_main, "NativeEngineLoader", _FakeLoader)

    pipe = ModelLoaderQwenImage21Pipe(config=_config(vision=True))
    pipe.process(PipeInput(input={}), lambda o: None)
    assert received == {"vision": True}
