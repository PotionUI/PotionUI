from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes._shared.generation.loader_helpers import lora_stack_fingerprint
from src.pipelines.pipes.model_loader.minimax_h3_image.main import ModelLoaderMiniMaxH3ImagePipe
from src.platform.runtime.native.engine import NativeModel

DIT_KEY = "native/dit//m/dit.safetensors"
STYLE = [{"file_path": "/m/style.safetensors", "weight": 0.8}]
EXPANDED = [{"file_path": "/m/style.safetensors", "weight": 0.8, "window": None}]


class _FakeModule:
    def named_modules(self):
        return iter(())


class _FakeModels:
    def __init__(self):
        self.calls = []
        self.loaders = {}
        self.entries = {}

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        self.calls.append((key, fingerprint))
        self.loaders[key] = loader
        entry = self.entries.get(key)
        if entry is not None and entry[0] == fingerprint:
            return entry[1]
        value = NativeModel("fake", _FakeModule(), spec=None, estimated_vram_gb=1.0)
        self.entries[key] = (fingerprint, value)
        return value


@pytest.fixture(autouse=True)
def _apply_spy(monkeypatch):
    calls = []
    monkeypatch.setattr(
        ModelLoaderMiniMaxH3ImagePipe, "_apply_loras", staticmethod(lambda dit_model, loras: calls.append(list(loras))),
    )
    return calls


def _config(**over):
    cfg = ModelLoaderMiniMaxH3ImagePipe.get_default_config()
    cfg.update({
        "diffusion_model": {"file_path": "/m/dit.safetensors", "name": "dit"},
        "text_encoder": {"file_path": "/m/te.safetensors", "name": "te"},
        "vae": {"file_path": "/m/vae.safetensors", "name": "vae"},
    })
    cfg.update(over)
    return cfg


def _run(pipe, models=None):
    models = models if models is not None else _FakeModels()
    out = pipe.process(PipeInput(input={"MODELS": models}), lambda o: None)
    return models, out


def test_the_loader_configuration_accepts_loras():
    names = {spec.name for spec in ModelLoaderMiniMaxH3ImagePipe.configuration()}
    assert "loras" in names
    assert ModelLoaderMiniMaxH3ImagePipe.get_default_config()["loras"] == []


def test_a_lora_is_applied_when_the_dit_loads(_apply_spy):
    loaded = SimpleNamespace(module=_FakeModule())

    class _Loader:
        def __init__(self, **_):
            pass

        def load(self, path, kind, **kw):
            return loaded

    with patch("src.pipelines.pipes._shared.generation.three_component_loader.NativeEngineLoader", _Loader):
        models, _out = _run(ModelLoaderMiniMaxH3ImagePipe(config=_config(loras=STYLE)))
        applied_before = len(_apply_spy)
        models.loaders[DIT_KEY]()
    assert _apply_spy[applied_before:] == [EXPANDED]


def test_a_changed_lora_stack_reloads_the_dit_but_not_the_other_components():
    models = _FakeModels()
    _run(ModelLoaderMiniMaxH3ImagePipe(config=_config()), models)
    _run(ModelLoaderMiniMaxH3ImagePipe(config=_config(loras=STYLE)), models)
    dit_fps = [fp for key, fp in models.calls if key == DIT_KEY]
    assert len(set(dit_fps)) == 2
    assert dit_fps[1].endswith(lora_stack_fingerprint(EXPANDED))
    vae_fps = {fp for key, fp in models.calls if key.startswith("native/vae/")}
    assert len(vae_fps) == 1


def test_an_unchanged_stack_reuses_the_dit():
    models = _FakeModels()
    _run(ModelLoaderMiniMaxH3ImagePipe(config=_config(loras=STYLE)), models)
    _run(ModelLoaderMiniMaxH3ImagePipe(config=_config(loras=STYLE)), models)
    assert len({fp for key, fp in models.calls if key == DIT_KEY}) == 1


def test_a_zero_weight_lora_is_ignored(_apply_spy):
    _run(ModelLoaderMiniMaxH3ImagePipe(config=_config(loras=[{"file_path": "/m/off.safetensors", "weight": 0.0}])))
    assert [call for call in _apply_spy if call] == []


def test_a_step_window_is_refused():
    pipe = ModelLoaderMiniMaxH3ImagePipe(config=_config(loras=[{**STYLE[0], "step_start": 1, "step_end": 4}]))
    with pytest.raises(ValueError, match="step window"):
        _run(pipe)


def test_lora_models_are_reported_for_history():
    pipe = ModelLoaderMiniMaxH3ImagePipe(config=_config(loras=STYLE))
    assert [(m.name, m.weight) for m in pipe.describe_models() if m.type == "lora"] == [("style", 0.8)]
