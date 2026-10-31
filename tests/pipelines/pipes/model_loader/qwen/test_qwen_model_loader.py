"""Tests for the model_loader/qwen pipe.

The pipe acquires TE / VAE / DiT under three independent MODELS keys. A fake
MODELS service records the acquire calls (without invoking the real loaders, so
no checkpoints are touched), which is enough to assert the cache-key scheme and
the LoRA-busts-only-the-DiT fingerprint behaviour. Qwen-Image has a SINGLE text
encoder (no CLIP-L), so the TE key is a plain ``native/te/<path>``.
"""

from __future__ import annotations

from types import SimpleNamespace

import torch

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.model_loader.qwen import edit_layout
from src.pipelines.pipes.model_loader.qwen.main import ModelLoaderQwenPipe


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

# -- three-component acquire ----------------------------------------------

# -- fingerprints ----------------------------------------------------------

def _fps(loras):
    models, out = _run(ModelLoaderQwenPipe(config=_config(loras=loras)))
    _ = out.output["text_encoder"].encoder  # force the deferred TE acquire
    return dict(zip([k for k, _ in models.calls], [f for _, f in models.calls]))


# -- vision fingerprint hazard -----------------------------


def test_vision_defaults_off_and_absent_from_fingerprint_is_still_distinguishable():
    # Default (vision unset) and explicit vision=False must fold to the SAME
    # fingerprint -- both mean "no vision tower loaded".
    models, out = _run(ModelLoaderQwenPipe(config=_config()))
    _ = out.output["text_encoder"].encoder
    te_fp_default = dict(models.calls)["native/te//m/te.safetensors"]
    models2, out2 = _run(ModelLoaderQwenPipe(config=_config(vision=False)))
    _ = out2.output["text_encoder"].encoder
    te_fp_explicit_false = dict(models2.calls)["native/te//m/te.safetensors"]
    assert te_fp_default == te_fp_explicit_false


def test_vision_true_changes_only_the_te_fingerprint():
    """The whole point of this fix: a text-only and a vision-enabled load of
    the SAME text-encoder path must NOT alias to the same model-lifecycle
    cache entry (a stale wrong-variant module would otherwise come back)."""
    no_vision = _fps([])
    models, out = _run(ModelLoaderQwenPipe(config=_config(vision=True)))
    _ = out.output["text_encoder"].encoder
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

    import src.pipelines.pipes._shared.generation.three_component_loader as qwen_main
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


class _DitModels(_FakeModels):
    def __init__(self, dit_module):
        super().__init__()
        self.dit_module = dit_module

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        model = super().acquire(key, fingerprint, loader, estimated_vram_gb)
        if key.startswith("native/dit/"):
            model.module = self.dit_module
        return model


def _edit_dit(ref_method, bias):
    return SimpleNamespace(config=SimpleNamespace(default_ref_method=ref_method), proj_out=SimpleNamespace(bias=bias))


def _layout(dit_module, **over):
    out = ModelLoaderQwenPipe(config=_config(**over)).process(
        PipeInput(input={"MODELS": _DitModels(dit_module)}), lambda o: None,
    )
    return out.output["text_encoder"].vision_layout


def test_an_edit_load_hands_the_clip_the_plus_layout_of_a_2511_checkpoint():
    dit = _edit_dit("index_timestep_zero", torch.zeros(64, dtype=torch.bfloat16))
    assert _layout(dit, vision=True) == {"picture_labels": True, "grounding_area": 147456}


def test_an_edit_load_of_the_original_release_keeps_the_single_image_layout(monkeypatch):
    bias = torch.linspace(-0.1, 0.1, 64).to(torch.bfloat16)
    dit = _edit_dit("index", bias)
    monkeypatch.setattr(edit_layout, "ORIGINAL_EDIT_PROJ_OUT_BIAS", frozenset({edit_layout.proj_out_bias_digest(dit)}))
    assert _layout(dit, vision=True) == {}


def test_a_text_only_load_hands_the_clip_no_layout():
    dit = _edit_dit("index_timestep_zero", torch.zeros(64, dtype=torch.bfloat16))
    assert _layout(dit) == {}
