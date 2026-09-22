"""Tests for the model_loader/minimax_h3 pipe: three EAGER standalone-file
acquires at load time (DiT + video VAE + audio VAE) plus a FOURTH, lazy one
(the TE, deferred into `clip`'s own `te_factory` -- see clip.py's "Lazy TE
acquisition"), the vision-enabled TE fingerprint fold, and the returned
bundle/clip wiring. No real weights -- ``models.acquire`` never calls its own
``loader`` callable, matching ``model_loader/ltx``'s own test pattern."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import torch

from src.pipelines.contracts import IOType, PipeInput
from src.platform.runtime.native.base import NativeArchModule
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
    assert out["model"] == IOType.MODEL and out["text_encoder"] == IOType.TEXT_ENCODER


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
    clip = out.output["text_encoder"]
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
    _ = out.output["text_encoder"].encoder
    te_fingerprint = dict(models.calls)["native/te//m/qwen3vl_32b.safetensors"]
    assert "vision=True" in te_fingerprint


def test_clip_model_fingerprint_available_without_resolving_the_te():
    # prompt_encoder's OWN conditioning-cache key reads clip._model_fingerprint
    # directly -- must be set at construction, with zero TE acquisition, or
    # the cache lookup itself would force the very load this fix avoids.
    models, out = _run()
    assert out.output["text_encoder"]._model_fingerprint == "/m/qwen3vl_32b.safetensors|vision=True"
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
    assert isinstance(out.output["text_encoder"], MiniMaxH3ClipTextEncoder)
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
        _ = out.output["text_encoder"].encoder  # force the deferred 4th (TE) load
        assert mock_load.call_count == 4
    assert isinstance(out.output["model"], MiniMaxH3ModelBundle)


class _ForeignArchModule(NativeArchModule):
    """Stands in for e.g. an LTX VAE loaded from a file the picker offered:
    a REAL engine arch module, just the wrong family for this preset."""

    @classmethod
    def from_config(cls, config, operations):
        return cls()

    def post_load(self):
        return None


class _FakeModelsWithModule(_FakeModels):
    def __init__(self, module_for_key):
        super().__init__()
        self._module_for_key = module_for_key

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        self.calls.append((key, fingerprint))
        module = self._module_for_key(key)
        return SimpleNamespace(module=module, spec=None, estimated_vram_gb=1.0, compute_dtype=torch.bfloat16)


def test_wrong_family_video_vae_is_rejected_at_load_time():
    # The real 5090 failure this guards: an LTX VAE file picked in the Video
    # VAE slot loads cleanly (generic "vae" kind routes by state-dict
    # detection) and only fails deep inside the LTX whole-clip encoder as an
    # OOM. The loader must name the file and the wrong class instead.
    models = _FakeModelsWithModule(
        lambda key: _ForeignArchModule() if key.startswith("native/vae/") else object()
    )
    with pytest.raises(ValueError) as exc:
        ModelLoaderMinimaxH3Pipe(_config()).process(PipeInput(input={"MODELS": models}), lambda o: None)
    msg = str(exc.value)
    assert "h3_video_vae.safetensors" in msg
    assert "_ForeignArchModule" in msg
    assert "video_vae" in msg


def test_wrong_family_audio_vae_is_rejected_too():
    models = _FakeModelsWithModule(
        lambda key: _ForeignArchModule() if key.startswith("native/audio_vae/") else object()
    )
    with pytest.raises(ValueError, match="audio_vae"):
        ModelLoaderMinimaxH3Pipe(_config()).process(PipeInput(input={"MODELS": models}), lambda o: None)


def test_non_arch_module_fakes_pass_the_family_guard():
    # Duck-typed stand-ins (every other test in this file) must not trip the
    # guard -- only a REAL NativeArchModule of the wrong class is rejected.
    models, out = _run()
    assert out.output["model"] is not None


def test_upscale_model_not_acquired_when_unset():
    models, out = _run()
    keys = [k for k, _ in models.calls]
    assert not any(k.startswith("native/h3_upsampler/") for k in keys)
    bundle = out.output["model"]
    assert bundle.upsampler is None


def test_upscale_model_acquired_when_configured():
    cfg = _config()
    cfg["upscale_model"] = {
        "file_path": "/m/minimax_h3_latent_upscaler_3d_bf16.safetensors",
        "name": "minimax_h3_latent_upscaler_3d_bf16",
    }
    models, out = _run(cfg)
    keys = [k for k, _ in models.calls]
    assert "native/h3_upsampler//m/minimax_h3_latent_upscaler_3d_bf16.safetensors" in keys
    bundle = out.output["model"]
    assert bundle.upsampler is not None


def test_wrong_family_upscale_model_is_rejected():
    cfg = _config()
    cfg["upscale_model"] = {"file_path": "/m/ltx_spatial_upscaler.safetensors", "name": "ltx_spatial_upscaler"}
    models = _FakeModelsWithModule(
        lambda key: _ForeignArchModule() if key.startswith("native/h3_upsampler/") else object()
    )
    with pytest.raises(ValueError, match="upscale_model"):
        ModelLoaderMinimaxH3Pipe(cfg).process(PipeInput(input={"MODELS": models}), lambda o: None)


def test_dit_cache_key_is_shared_when_both_calls_have_no_loras():
    """Two separate model_loader acquisitions of the SAME DiT file, both with
    an empty LoRA stack (the default), must resolve to the identical
    (key, fingerprint) pair -- this is what lets a stage-2 call with no
    Refine LoRAs configured reuse stage 1's already-resident DiT instead of
    forcing a needless reload."""
    models_a, _ = _run(_config())
    models_b, _ = _run(_config())
    dit_call_a = models_a.calls[0]
    dit_call_b = models_b.calls[0]
    assert dit_call_a == dit_call_b


def test_dit_cache_fingerprint_differs_when_lora_lists_differ():
    """A stage-2 model_loader call configured with its OWN Refine LoRAs must
    resolve to a DIFFERENT fingerprint than stage 1's (LoRA-free) call under
    the SAME cache key -- the key names the file, the fingerprint names the
    built module, and a LoRA stack changes the built module."""
    cfg_stage1 = _config()
    cfg_stage2 = {**_config(), "loras": [{"model": "/m/loras/distilled.safetensors", "strength": 1.0}]}
    models_stage1, _ = _run(cfg_stage1)
    models_stage2, _ = _run(cfg_stage2)
    key_stage1, fingerprint_stage1 = models_stage1.calls[0]
    key_stage2, fingerprint_stage2 = models_stage2.calls[0]
    assert key_stage1 == key_stage2
    assert fingerprint_stage1 != fingerprint_stage2


def test_dit_cache_fingerprint_differs_between_two_different_nonempty_lora_stacks():
    cfg_a = {**_config(), "loras": [{"model": "/m/loras/a.safetensors", "strength": 1.0}]}
    cfg_b = {**_config(), "loras": [{"model": "/m/loras/b.safetensors", "strength": 1.0}]}
    models_a, _ = _run(cfg_a)
    models_b, _ = _run(cfg_b)
    assert models_a.calls[0][1] != models_b.calls[0][1]
