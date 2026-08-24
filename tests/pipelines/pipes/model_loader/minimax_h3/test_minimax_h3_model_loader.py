"""Tests for the model_loader/minimax_h3 pipe: three EAGER standalone-file
acquires at load time (DiT + video VAE + audio VAE) plus a FOURTH, lazy one
(the TE, deferred into `clip`'s own `te_factory` -- see clip.py's "Lazy TE
acquisition"), the vision-enabled TE fingerprint fold, and the returned
bundle/clip wiring. No real weights -- ``models.acquire`` never calls its own
``loader`` callable, matching ``model_loader/ltx``'s own test pattern."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import torch

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.model_loader.minimax_h3.bundle import MiniMaxH3ModelBundle
from src.pipelines.pipes.model_loader.minimax_h3.clip import MiniMaxH3ClipTextEncoder
from src.pipelines.pipes.model_loader.minimax_h3.main import ModelLoaderMinimaxH3Pipe


class _FakeModels:
    def __init__(self):
        self.calls = []  # (key, fingerprint)

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        self.calls.append((key, fingerprint))
        return SimpleNamespace(module=object(), spec=None, estimated_vram_gb=1.0, compute_dtype=torch.bfloat16)


def _config():
    cfg = ModelLoaderMinimaxH3Pipe.get_default_config()
    cfg.update({
        "model": {"file_path": "/m/h3_dit.safetensors", "name": "h3_dit"},
        "text_encoder": {"file_path": "/m/qwen3vl_32b.safetensors", "name": "qwen3vl_32b"},
        "video_vae": {"file_path": "/m/h3_video_vae.safetensors", "name": "h3_video_vae"},
        "audio_vae": {"file_path": "/m/h3_audio_vae.safetensors", "name": "h3_audio_vae"},
    })
    return cfg


def _run(cfg=None):
    models = _FakeModels()
    out = ModelLoaderMinimaxH3Pipe(cfg or _config()).process(PipeInput(input={"MODELS": models}), lambda o: None)
    return models, out


def test_name_and_outputs():
    assert ModelLoaderMinimaxH3Pipe.name == "model_loader"
    out = {o.name: o.io_type for o in ModelLoaderMinimaxH3Pipe.outputs()}
    assert out["model"] == IOType.MODEL and out["clip"] == IOType.CLIP


def test_three_eager_standalone_component_acquires():
    # The DiT/video VAE/audio VAE are always needed for sampling regardless
    # of prompt_encoder's own conditioning cache state, so they load eagerly
    # at process() time. The TE does NOT appear here -- see the lazy tests
    # below.
    models, out = _run()
    keys = [k for k, _ in models.calls]
    assert keys == [
        "native/dit//m/h3_dit.safetensors",
        "native/vae//m/h3_video_vae.safetensors",
        "native/audio_vae//m/h3_audio_vae.safetensors",
    ]
    assert not any("native/te/" in k for k in keys)


def test_te_is_not_acquired_at_load_time():
    # The root-cause regression test for the reported bug: a real warm-run
    # trace showed the 32B TE reloaded from disk (~21s) even on a
    # SAME-PROMPT (prompt_encoder conditioning-cache HIT) generation that
    # never touched it -- because the loader used to acquire it
    # unconditionally, every generation, regardless of need.
    models, out = _run()
    assert not any(key.startswith("native/te/") for key, _ in models.calls)
    bundle = out.output["model"]
    assert bundle.te is None  # never resolved -- nothing to hold onto yet


def test_te_is_acquired_lazily_on_first_clip_encoder_access():
    models, out = _run()
    clip = out.output["clip"]
    assert not any(key.startswith("native/te/") for key, _ in models.calls)
    _ = clip.encoder  # the first genuine "need to encode" moment
    keys = [k for k, _ in models.calls]
    assert "native/te//m/qwen3vl_32b.safetensors" in keys


def test_te_fingerprint_folds_in_vision_enabled():
    # documented hazard (text_encoders/loader.py:435-449): a text-only and a
    # vision-enabled load of the SAME path build DIFFERENT modules -- the
    # cache fingerprint MUST differ, or a stale text-only module could be
    # handed back for a vision (fl2va) request. Fingerprint is only known
    # once the (lazy) TE acquire actually runs.
    models, out = _run()
    _ = out.output["clip"].encoder
    te_fingerprint = dict(models.calls)["native/te//m/qwen3vl_32b.safetensors"]
    assert "vision=True" in te_fingerprint


def test_clip_model_fingerprint_available_without_resolving_the_te():
    # prompt_encoder's OWN conditioning-cache key reads clip._model_fingerprint
    # directly -- must be set at construction, with zero TE acquisition, or
    # the cache lookup itself would force the very load this fix avoids.
    models, out = _run()
    assert out.output["clip"]._model_fingerprint == "/m/qwen3vl_32b.safetensors|vision=True"
    assert not any(key.startswith("native/te/") for key, _ in models.calls)


def test_dit_fingerprint_changes_with_loras():
    models_no_lora, _ = _run()
    cfg = _config()
    cfg["loras"] = [{"file_path": "/m/some.safetensors", "weight": 0.7}]
    models_with_lora, _ = _run(cfg)
    fp_no_lora = dict(models_no_lora.calls)["native/dit//m/h3_dit.safetensors"]
    fp_with_lora = dict(models_with_lora.calls)["native/dit//m/h3_dit.safetensors"]
    assert fp_no_lora != fp_with_lora


def test_returns_bundle_and_clip():
    _, out = _run()
    assert isinstance(out.output["model"], MiniMaxH3ModelBundle)
    assert isinstance(out.output["clip"], MiniMaxH3ClipTextEncoder)
    bundle = out.output["model"]
    assert bundle.te_cache_key == "native/te//m/qwen3vl_32b.safetensors"


def test_missing_required_path_raises():
    cfg = _config()
    cfg["audio_vae"] = None
    try:
        ModelLoaderMinimaxH3Pipe(cfg).process(PipeInput(input={"MODELS": _FakeModels()}), lambda o: None)
        assert False, "expected a ValueError"
    except ValueError as e:
        assert "audio_vae" in str(e) or "requires" in str(e)


def test_no_models_service_loads_directly():
    # Isolated pipe test path (no MODELS injected) -- loader.load() is called
    # directly instead of going through models.acquire(). Only 3 calls at
    # process() time (dit, video_vae, audio_vae) -- the TE is still deferred
    # into clip's lazy factory even without a MODELS service.
    cfg = _config()
    fake_dit = SimpleNamespace(module=object(), spec=None, estimated_vram_gb=1.0, compute_dtype=torch.bfloat16)
    with patch(
        "src.pipelines.pipes.model_loader.minimax_h3.main.NativeEngineLoader.load",
        return_value=fake_dit,
    ) as mock_load:
        out = ModelLoaderMinimaxH3Pipe(cfg).process(PipeInput(input={}), lambda o: None)
        assert mock_load.call_count == 3  # dit, video_vae, audio_vae
        _ = out.output["clip"].encoder  # force the deferred 4th (TE) load
        assert mock_load.call_count == 4
    assert isinstance(out.output["model"], MiniMaxH3ModelBundle)
