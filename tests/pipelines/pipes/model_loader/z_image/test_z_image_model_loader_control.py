from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.model_loader.z_image.main import ModelLoaderZImagePipe

_CONTROL = {"file_path": "/m/fun_control.safetensors", "name": "fun_control"}


@pytest.fixture(autouse=True)
def _fake_text_encoder(monkeypatch):
    import src.pipelines.pipes.model_loader.z_image.main as loader_main
    monkeypatch.setattr(loader_main, "load_text_encoder", lambda *a, **kw: SimpleNamespace(name="te"))


class _FakeModels:
    def __init__(self):
        self.calls = []
        self.sizes = {}

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        self.calls.append((key, fingerprint))
        self.sizes[key] = estimated_vram_gb
        return SimpleNamespace(module=object(), spec=None, estimated_vram_gb=1.0)


def _config(**over):
    cfg = ModelLoaderZImagePipe.get_default_config()
    cfg.update({
        "diffusion_model": {"file_path": "/m/dit.safetensors", "name": "dit"},
        "text_encoder": {"file_path": "/m/te.safetensors", "name": "te"},
        "vae": {"file_path": "/m/vae.safetensors", "name": "vae"},
        "loras": [],
    })
    cfg.update(over)
    return cfg


def _run(**over):
    models = _FakeModels()
    pipe = ModelLoaderZImagePipe(config=_config(**over))
    out = pipe.process(PipeInput(input={"MODELS": models}), lambda o: None)
    _ = out.output["text_encoder"].encoder
    return dict(models.calls)


def test_the_control_model_is_an_optional_setting():
    specs = {s.name: s for s in ModelLoaderZImagePipe.configuration()}
    assert "control_model" in specs and not specs["control_model"].required


def test_a_control_model_gets_its_own_dit_cache_entry():
    plain = _run()
    controlled = _run(control_model=_CONTROL)
    plain_key = "native/dit//m/dit.safetensors"
    controlled_key = "native/dit//m/dit.safetensors+control=/m/fun_control.safetensors"
    assert plain_key in plain and plain_key not in controlled
    assert controlled_key in controlled and controlled_key not in plain
    assert "control=/m/fun_control.safetensors" in controlled[controlled_key]
    assert "control=" not in plain[plain_key]
    others = {k: v for k, v in controlled.items() if not k.startswith("native/dit/")}
    assert others and others == {k: v for k, v in plain.items() if not k.startswith("native/dit/")}


def test_an_empty_control_model_leaves_the_dit_fingerprint_alone():
    plain = _run()
    empty = _run(control_model={"file_path": "", "name": ""})
    assert empty["native/dit//m/dit.safetensors"] == plain["native/dit//m/dit.safetensors"]


def test_the_control_model_is_loaded_with_the_dit(monkeypatch):
    received = []

    class _FakeLoader:
        def __init__(self, *a, **kw):
            pass

        def load(self, path, kind, **kwargs):
            received.append((path, kind, kwargs))
            return SimpleNamespace(module=object(), spec=None, estimated_vram_gb=1.0)

    import src.pipelines.pipes._shared.generation.three_component_loader as loader_module
    monkeypatch.setattr(loader_module, "NativeEngineLoader", _FakeLoader)

    ModelLoaderZImagePipe(config=_config(control_model=_CONTROL)).process(PipeInput(input={}), lambda o: None)
    assert ("/m/dit.safetensors", "diffusion_model", {"model_patch": "/m/fun_control.safetensors"}) in received


def test_without_a_control_model_the_dit_loads_without_a_patch(monkeypatch):
    received = []

    class _FakeLoader:
        def __init__(self, *a, **kw):
            pass

        def load(self, path, kind, **kwargs):
            received.append((path, kind, kwargs))
            return SimpleNamespace(module=object(), spec=None, estimated_vram_gb=1.0)

    import src.pipelines.pipes._shared.generation.three_component_loader as loader_module
    monkeypatch.setattr(loader_module, "NativeEngineLoader", _FakeLoader)

    ModelLoaderZImagePipe(config=_config()).process(PipeInput(input={}), lambda o: None)
    assert ("/m/dit.safetensors", "diffusion_model", {}) in received


def test_the_control_model_is_listed_with_the_models():
    pipe = ModelLoaderZImagePipe(config=_config(control_model=_CONTROL))
    assert ("fun_control", "z_image_fun_control") in {(m.name, m.type) for m in pipe.describe_models()}


def test_the_control_file_adds_to_the_dit_size_estimate(tmp_path):
    dit = tmp_path / "dit.safetensors"
    dit.write_bytes(b"0" * 1024 * 1024)
    control = tmp_path / "control.safetensors"
    control.write_bytes(b"0" * 4 * 1024 * 1024)

    def sizes(**over):
        models = _FakeModels()
        pipe = ModelLoaderZImagePipe(config=_config(
            diffusion_model={"file_path": str(dit), "name": "dit"}, **over))
        pipe.process(PipeInput(input={"MODELS": models}), lambda o: None)
        return next(v for k, v in models.sizes.items() if k.startswith("native/dit/"))

    plain = sizes()
    controlled = sizes(control_model={"file_path": str(control), "name": "control"})
    assert plain is not None and controlled > plain
