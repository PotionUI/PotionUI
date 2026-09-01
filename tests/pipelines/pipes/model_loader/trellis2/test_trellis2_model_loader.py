"""Tests for the model_loader/trellis2 pipe.

Mirrors the krea2/flux loader tests: a fake MODELS service with real hit/miss
semantics records every acquire, so the cache-key scheme is asserted without
touching real checkpoints. The scheme is the point of this pipe — eight models
come out of four files, and keying per component rather than per file is what
makes a resolution-tier change re-acquire one model instead of re-reading an
8GB checkpoint.
"""

from __future__ import annotations

import pytest

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.model_loader.trellis2 import main as trellis2_main
from src.pipelines.pipes.model_loader.trellis2.main import ModelLoaderTrellis2Pipe

DIT = "/m/trellis_2_bf16.safetensors"
SHAPE_VAE = "/m/trellis_2_shape_vae_bf16.safetensors"
TEXTURE_VAE = "/m/trellis_2_texture_vae_bf16.safetensors"
ENCODER = "/m/dino_v3_vit_l.safetensors"
MATTING = "/m/birefnet.safetensors"


class _FakeModels:
    """Real hit/miss cache semantics (key + fingerprint -> value), matching
    ``ModelLifecycle.acquire``: a fingerprint match returns the SAME object
    without re-running ``loader()``. Entries are retained for the life of the
    fake, which is also what keeps the bundle's weak references alive."""

    def __init__(self):
        self.calls = []
        self.estimates = {}
        self.loads = 0
        self._entries = {}

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        self.calls.append((key, fingerprint))
        self.estimates[key] = estimated_vram_gb
        entry = self._entries.get(key)
        if entry is not None and entry[0] == fingerprint:
            return entry[1]
        self.loads += 1
        value = loader()
        self._entries[key] = (fingerprint, value)
        return value

    def keys(self):
        return [key for key, _ in self.calls]


class _FakeComponent:
    def __init__(self, label):
        self.label = label

    def to(self, device):
        return self


@pytest.fixture(autouse=True)
def _fake_loaders(monkeypatch):
    """Replace every real checkpoint read with a labelled stand-in, and count
    the loads per component so a warm re-acquire can be told from a cold one."""
    built = []

    def _record(label):
        def _build(*args, **kwargs):
            built.append((label, args, kwargs))
            return _FakeComponent(label)

        return _build

    for name, label in (
        ("load_dino_conditioner", "dino"),
        ("load_ss_flow", "ss_flow"),
        ("load_ss_vae_decoder", "ss_vae"),
        ("load_shape_slat_flow", "shape_flow"),
        ("load_shape_slat_decoder", "shape_decoder"),
        ("load_tex_slat_flow", "tex_flow"),
        ("load_tex_slat_decoder", "tex_decoder"),
    ):
        monkeypatch.setattr(trellis2_main.trellis2_load, name, _record(label))

    monkeypatch.setattr(trellis2_main, "_load_matting", lambda path: _FakeComponent("matting"))
    monkeypatch.setattr(trellis2_main, "prefix_size_gb", lambda path, prefix: 1.5)
    monkeypatch.setattr(trellis2_main, "file_size_gb", lambda path: 0.75)
    return built


def _config(**over):
    config = ModelLoaderTrellis2Pipe.get_default_config()
    config.update({
        "diffusion_model": {"file_path": DIT, "name": "trellis2"},
        "shape_vae": {"file_path": SHAPE_VAE, "name": "shape-vae"},
        "texture_vae": {"file_path": TEXTURE_VAE, "name": "texture-vae"},
        "image_encoder": {"file_path": ENCODER, "name": "dino"},
    })
    config.update(over)
    return config


def _run(models=None, **over):
    pipe = ModelLoaderTrellis2Pipe(_config(**over))
    models = models if models is not None else _FakeModels()
    out = pipe.process(PipeInput(input={"MODELS": models}), lambda output: None)
    return models, out.output["model"]


# -- cache keys -------------------------------------------------------------


def test_every_component_is_acquired_under_its_own_key():
    models, _ = _run()
    assert models.keys() == [
        f"native/trellis2/dino/{ENCODER}",
        f"native/trellis2/ss_flow/{DIT}",
        f"native/trellis2/ss_vae/{SHAPE_VAE}",
        f"native/trellis2/shape_flow_512/{DIT}",
        f"native/trellis2/shape_decoder/{SHAPE_VAE}",
        f"native/trellis2/tex_flow_1024/{DIT}",
        f"native/trellis2/tex_decoder/{TEXTURE_VAE}",
        f"native/trellis2/shape_flow_1024/{DIT}",
    ]


def test_the_512_tier_loads_no_high_resolution_shape_flow():
    models, bundle = _run(resolution_tier="512")
    assert f"native/trellis2/shape_flow_1024/{DIT}" not in models.keys()
    assert bundle.shape_flow_hr is None


def test_the_512_tier_takes_the_512_texture_flow_variant():
    """Both tiers read the same texture weights but declare different latent
    resolutions, so the variant has to be part of the key."""
    models, _ = _run(resolution_tier="512")
    assert f"native/trellis2/tex_flow_512/{DIT}" in models.keys()
    assert f"native/trellis2/tex_flow_1024/{DIT}" not in models.keys()


def test_changing_the_tier_reuses_every_shared_component():
    """The whole point of per-component keys: a tier switch must not re-read
    the conditioner, the VAEs, the sparse-structure flow or the shape flow."""
    models = _FakeModels()
    _run(models, resolution_tier="1024")
    cold_loads = models.loads

    _run(models, resolution_tier="1536")
    # 1536 is the same cascade as 1024 — every component is already warm.
    assert models.loads == cold_loads


def test_switching_from_512_to_a_cascade_loads_only_what_is_new():
    models = _FakeModels()
    _run(models, resolution_tier="512")
    after_512 = models.loads

    _run(models, resolution_tier="1024")
    # Only the 1024 texture-flow variant and the high-resolution shape flow.
    assert models.loads - after_512 == 2


def test_the_dtype_is_part_of_every_fingerprint():
    models = _FakeModels()
    _run(models)
    _run(models, dtype="float16")
    fingerprints = {fingerprint for _, fingerprint in models.calls}
    assert any(fingerprint.endswith("|bfloat16") for fingerprint in fingerprints)
    assert any(fingerprint.endswith("|float16") for fingerprint in fingerprints)


def test_a_flow_model_is_sized_by_its_own_prefix_not_the_whole_bundle():
    """Four DiTs share one file; charging each of them the file's size would
    tell admission control the run needs about four times the VRAM it does."""
    models, _ = _run()
    assert models.estimates[f"native/trellis2/ss_flow/{DIT}"] == 1.5
    assert models.estimates[f"native/trellis2/shape_flow_512/{DIT}"] == 1.5


# -- the bundle -------------------------------------------------------------


def test_the_bundle_carries_the_tier_and_device():
    _, bundle = _run(resolution_tier="1536", device="cpu")
    assert bundle.tier == "1536"
    assert bundle.device == "cpu"


def test_the_bundle_resolves_into_the_components_a_run_consumes():
    _, bundle = _run()
    components = bundle.components()
    assert components.conditioner.label == "dino"
    assert components.ss_flow.label == "ss_flow"
    assert components.shape_flow_hr.label == "shape_flow"
    assert components.matting is None


def test_an_evicted_component_is_named_rather_than_surfacing_as_none():
    _, bundle = _run()
    bundle.ss_vae = None
    with pytest.raises(ValueError, match="sparse-structure decoder was evicted"):
        bundle.components()


# -- matting ----------------------------------------------------------------


def test_no_matting_model_is_loaded_unless_one_is_selected():
    models, bundle = _run()
    assert not any(key.startswith("native/matting/") for key in models.keys())
    assert bundle.matting is None


def test_a_selected_matting_model_is_acquired_and_reaches_the_bundle():
    models, bundle = _run(matting_model={"file_path": MATTING, "name": "birefnet"})
    assert f"native/matting/{MATTING}" in models.keys()
    assert bundle.matting.label == "matting"


# -- validation -------------------------------------------------------------


def test_a_missing_checkpoint_is_named_before_anything_loads():
    pipe = ModelLoaderTrellis2Pipe(_config(texture_vae=None))
    models = _FakeModels()
    with pytest.raises(ValueError, match="texture VAE"):
        pipe.process(PipeInput(input={"MODELS": models}), lambda output: None)
    assert models.calls == []


def test_an_unknown_tier_is_refused():
    pipe = ModelLoaderTrellis2Pipe(_config(resolution_tier="4096"))
    with pytest.raises(ValueError, match="unknown resolution tier"):
        pipe.process(PipeInput(input={"MODELS": _FakeModels()}), lambda output: None)


def test_it_loads_without_a_models_service():
    """An isolated run (no MODELS injected) still loads every component."""
    pipe = ModelLoaderTrellis2Pipe(_config())
    out = pipe.process(PipeInput(input={}), lambda output: None)
    assert out.output["model"].tier == "1024"


# -- progress ---------------------------------------------------------------


def test_progress_counts_every_component_it_is_about_to_load():
    pipe = ModelLoaderTrellis2Pipe(_config())
    seen = []
    pipe.process(PipeInput(input={"MODELS": _FakeModels()}), seen.append)

    states = [output.state for output in seen if getattr(output, "state", None)]
    assert any("image encoder (1 of 8)" in state for state in states)
    assert any("high-resolution shape flow (8 of 8)" in state for state in states)
