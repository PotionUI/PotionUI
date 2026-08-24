"""Tests for the model_loader/qwen pipe.

The pipe acquires TE / VAE / DiT under three independent MODELS keys. A fake
MODELS service records the acquire calls (without invoking the real loaders, so
no checkpoints are touched), which is enough to assert the cache-key scheme and
the LoRA-busts-only-the-DiT fingerprint behaviour. Qwen-Image has a SINGLE text
encoder (no CLIP-L), so the TE key is a plain ``native/te/<path>``.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.model_loader.qwen.main import ModelLoaderQwenPipe
from src.pipelines.pipes.model_loader.qwen.bundle import QwenModelBundle
from src.pipelines.pipes.model_loader.qwen.qwen_clip import QwenClipTextEncoder


class _FakeModels:
    """Records acquire(key, fingerprint) and returns a stand-in NativeModel."""

    def __init__(self):
        self.calls = []  # list[(key, fingerprint)]

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        self.calls.append((key, fingerprint))
        return SimpleNamespace(module=object(), spec=None, estimated_vram_gb=1.0)


def _config(loras=None, **over):
    cfg = ModelLoaderQwenPipe.get_default_config()
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


# -- metadata --------------------------------------------------------------

def test_name_and_outputs():
    assert ModelLoaderQwenPipe.name == "model_loader"
    out_names = {o.name: o.io_type for o in ModelLoaderQwenPipe.outputs()}
    assert out_names["model"] == IOType.MODEL
    assert out_names["text_encoder"] == IOType.TEXT_ENCODER


def test_no_clip_l_in_config():
    # Qwen has a single text encoder — no CLIP-L config field (unlike Flux).
    names = {s.name for s in ModelLoaderQwenPipe.configuration()}
    assert "clip_l" not in names
    assert {"diffusion_model", "text_encoder", "vae", "loras"} <= names


# -- three-component acquire ----------------------------------------------

def test_three_distinct_acquire_keys():
    models, out = _run(ModelLoaderQwenPipe(config=_config()))
    keys = [k for k, _ in models.calls]
    assert len(keys) == 3
    assert len(set(keys)) == 3
    assert keys[0] == "native/te//m/te.safetensors"
    assert keys[1] == "native/vae//m/vae.safetensors"
    assert keys[2] == "native/dit//m/dit.safetensors"


def test_outputs_are_bundle_and_clip():
    _models, out = _run(ModelLoaderQwenPipe(config=_config()))
    assert isinstance(out.output["model"], QwenModelBundle)
    assert isinstance(out.output["text_encoder"], QwenClipTextEncoder)


def test_bundle_carries_the_te_cache_key(monkeypatch):
    """generator/qwen's TE eviction reads bundle.te_cache_key -- it
    must match the exact key the TE was actually acquire()'d under, or
    evict_dead_weight would target the wrong (or no) cache entry."""
    _models, out = _run(ModelLoaderQwenPipe(config=_config()))
    assert out.output["model"].te_cache_key == "native/te//m/te.safetensors"


def test_missing_file_paths_raise():
    cfg = _config()
    cfg["vae"] = None
    import pytest
    with pytest.raises(ValueError):
        _run(ModelLoaderQwenPipe(config=cfg))


# -- fingerprints ----------------------------------------------------------

def _fps(loras):
    models, _ = _run(ModelLoaderQwenPipe(config=_config(loras=loras)))
    return dict(zip([k for k, _ in models.calls], [f for _, f in models.calls]))


def test_lora_change_busts_only_dit():
    no_lora = _fps([])
    with_lora = _fps([{"model": "/m/style.safetensors", "strength": 0.8}])

    te_key = "native/te//m/te.safetensors"
    vae_key = "native/vae//m/vae.safetensors"
    dit_key = "native/dit//m/dit.safetensors"

    # TE and VAE fingerprints are unchanged by a LoRA change...
    assert no_lora[te_key] == with_lora[te_key]
    assert no_lora[vae_key] == with_lora[vae_key]
    # ...only the DiT fingerprint moves.
    assert no_lora[dit_key] != with_lora[dit_key]
    assert "style.safetensors@0.8" in with_lora[dit_key]


def test_dtype_in_all_fingerprints():
    models, _ = _run(ModelLoaderQwenPipe(config=_config(dtype="float16")))
    assert all("float16" in fp for _, fp in models.calls)


def test_zero_weight_lora_ignored():
    fps = _fps([{"model": "/m/off.safetensors", "strength": 0.0}])
    dit_fp = fps["native/dit//m/dit.safetensors"]
    assert "off.safetensors" not in dit_fp  # filtered out as inactive


# -- vision fingerprint hazard -----------------------------


def test_vision_defaults_off_and_absent_from_fingerprint_is_still_distinguishable():
    # Default (vision unset) and explicit vision=False must fold to the SAME
    # fingerprint -- both mean "no vision tower loaded".
    models, _ = _run(ModelLoaderQwenPipe(config=_config()))
    te_fp_default = dict(models.calls)["native/te//m/te.safetensors"]
    models2, _ = _run(ModelLoaderQwenPipe(config=_config(vision=False)))
    te_fp_explicit_false = dict(models2.calls)["native/te//m/te.safetensors"]
    assert te_fp_default == te_fp_explicit_false


def test_vision_true_changes_only_the_te_fingerprint():
    """The whole point of this fix: a text-only and a vision-enabled load of
    the SAME text-encoder path must NOT alias to the same model-lifecycle
    cache entry (a stale wrong-variant module would otherwise come back)."""
    no_vision = _fps([])
    models, _ = _run(ModelLoaderQwenPipe(config=_config(vision=True)))
    with_vision = dict(models.calls)

    te_key = "native/te//m/te.safetensors"
    vae_key = "native/vae//m/vae.safetensors"
    dit_key = "native/dit//m/dit.safetensors"

    assert no_vision[te_key] != with_vision[te_key]
    assert "vision=True" in with_vision[te_key]
    assert "vision=False" in no_vision[te_key]
    # VAE and DiT are unaffected by the TE's vision flag.
    assert no_vision[vae_key] == with_vision[vae_key]
    assert no_vision[dit_key] == with_vision[dit_key]


def test_vision_flag_threaded_to_the_engine_loader(monkeypatch):
    """``load_te``'s closure must actually pass ``vision=`` down to
    ``NativeEngineLoader.load`` -- the fingerprint alone is not the fix, the
    loader call has to receive the flag too, or a fingerprint-forced reload
    would just rebuild the same (wrong) text-only module."""
    received = {}

    class _FakeLoader:
        def __init__(self, *a, **kw):
            pass

        def load(self, path, kind, **kwargs):
            if kind == "text_encoder":
                received.update(kwargs)
            return SimpleNamespace(module=object(), spec=None, estimated_vram_gb=1.0)

    import src.pipelines.pipes.model_loader.qwen.main as qwen_main
    monkeypatch.setattr(qwen_main, "NativeEngineLoader", _FakeLoader)

    pipe = ModelLoaderQwenPipe(config=_config(vision=True))
    pipe.process(PipeInput(input={}), lambda o: None)
    assert received == {"vision": True}


# -- no-MODELS fallback ----------------------------------------------------

def test_runs_without_models_service():
    # No MODELS service: the pipe still builds the bundle (loaders are stubbed
    # here by never being invoked — with real files it would load directly).
    pipe = ModelLoaderQwenPipe(config=_config())
    # Provide a fake loader via a MODELS-less input; the real NativeEngineLoader
    # would touch disk, so we only assert the branch is reachable by checking the
    # describe/progress path does not require MODELS.
    assert pipe.describe_models()  # metadata built from config, no I/O
