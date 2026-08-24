"""Tests for the matting/birefnet pipe: config/IO contract, the LIST-in/
LIST-out array contract every pipe in this family shares with its upstream
producer (`media_loader`) and downstream consumer (`gallery`), model-path
resolution (file_path used as-is, never joined onto a models dir - see
main.py's docstring and the historical double-prefix bug), matte_strength
commitment of mid-grey pixels, feather, and MODELS-service caching/device
handling - all against a fake matting model, no real checkpoint or GPU.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.matting.birefnet.main import MattingBirefnetPipe


class _FakeMattingModel:
    """A matting model with `BackgroundMattingModel`'s call shape: `.to()`/
    `.cpu()` move it, `__call__` returns an RGBA image the same size as the
    input, alpha = a caller-supplied raw value (mimicking the model's raw
    sigmoid output)."""

    def __init__(self, raw_alpha=200):
        self.raw_alpha = raw_alpha
        self.device_calls = []

    def to(self, device):
        self.device_calls.append(("to", device))
        return self

    def cpu(self):
        self.device_calls.append(("cpu", None))
        return self

    def __call__(self, image: Image.Image) -> Image.Image:
        rgb = image.convert("RGB")
        out = rgb.copy()
        out.putalpha(Image.new("L", rgb.size, self.raw_alpha))
        return out


class _FakeModels:
    def __init__(self, model):
        self.model = model
        self.calls = []

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        self.calls.append((key, fingerprint))
        return self.model


def _config(**over):
    cfg = MattingBirefnetPipe.get_default_config()
    cfg.update({"model": {"file_path": "models/detection_segm/birefnet.safetensors", "name": "birefnet"}})
    cfg.update(over)
    return cfg


def _input_image(size=(16, 16), color=(50, 60, 70)):
    return Image.new("RGB", size, color)


# -- config / IO contract -----------------------------------------------------

def test_name_and_contract():
    assert MattingBirefnetPipe.name == "matting/birefnet"
    ins = {s.name: s for s in MattingBirefnetPipe.inputs()}
    outs = {s.name: s for s in MattingBirefnetPipe.outputs()}
    assert ins["image"].io_type == IOType.IMAGE
    assert ins["MODELS"].io_type == IOType.SERVICE
    assert set(outs) == {"image"}
    assert outs["image"].io_type == IOType.IMAGE


def test_image_io_is_declared_as_an_array():
    """The upstream producer (`media_loader`) always emits a LIST and the
    downstream consumer (`gallery`) always iterates one - a scalar `image`
    spec here is exactly the `'Image' object is not iterable` bug."""
    ins = {s.name: s for s in MattingBirefnetPipe.inputs()}
    outs = {s.name: s for s in MattingBirefnetPipe.outputs()}
    assert ins["image"].is_array is True
    assert outs["image"].is_array is True


def test_config_spec_matches_contract():
    specs = {s.name: s for s in MattingBirefnetPipe.configuration()}
    assert set(specs) == {"model", "matte_strength", "feather"}
    assert specs["model"].param_type is dict and specs["model"].required is True
    assert specs["matte_strength"].default == 50
    assert specs["matte_strength"].min_value == 0 and specs["matte_strength"].max_value == 100
    assert specs["feather"].default == 0.0
    assert specs["feather"].min_value == 0.0 and specs["feather"].max_value == 16.0


def test_missing_model_config_raises():
    pipe = MattingBirefnetPipe({"model": None})
    with pytest.raises(ValueError):
        pipe.process(PipeInput(input={"image": [_input_image()]}), lambda o: None)


def test_missing_image_input_raises():
    pipe = MattingBirefnetPipe(_config())
    with pytest.raises(ValueError):
        pipe.process(PipeInput(input={}), lambda o: None)


# -- array contract: process EVERY image, never just the first --------------

def test_two_image_list_returns_two_images_bite_check():
    """Feed a two-image list; both must come back distinct and in order.
    Bite-check: reverting `process` to unwrap `image[0]` and return a bare
    `PipeOutput(output={"image": result})` makes this go red (a one-element
    result, or a crash iterating a bare Image downstream)."""
    model = _FakeMattingModel(raw_alpha=200)
    models = _FakeModels(model)
    pipe = MattingBirefnetPipe(_config())

    image_a = _input_image(color=(10, 10, 10))
    image_b = _input_image(color=(200, 200, 200))
    result = pipe.process(
        PipeInput(input={"image": [image_a, image_b], "MODELS": models}),
        lambda o: None,
    )

    out = result.output["image"]
    assert isinstance(out, list)
    assert len(out) == 2
    assert all(img.mode == "RGBA" for img in out)
    # Both source pixels' RGB is threaded through (matting only rewrites alpha).
    assert tuple(np.array(out[0])[0, 0, :3]) == (10, 10, 10)
    assert tuple(np.array(out[1])[0, 0, :3]) == (200, 200, 200)


def test_bare_image_input_is_still_accepted_and_wrapped():
    model = _FakeMattingModel(raw_alpha=200)
    models = _FakeModels(model)
    pipe = MattingBirefnetPipe(_config())
    result = pipe.process(
        PipeInput(input={"image": _input_image(), "MODELS": models}), lambda o: None,
    )
    assert isinstance(result.output["image"], list)
    assert len(result.output["image"]) == 1


# -- MODELS caching / device lifecycle ----------------------------------------

def test_acquires_via_models_with_file_path_key_and_releases_to_cpu():
    model = _FakeMattingModel(raw_alpha=200)
    models = _FakeModels(model)
    pipe = MattingBirefnetPipe(_config())

    result = pipe.process(
        PipeInput(input={"image": [_input_image()], "MODELS": models}),
        lambda o: None,
    )

    assert len(models.calls) == 1
    key, fingerprint = models.calls[0]
    # The model-picker's file_path is used AS-IS: never joined onto a models
    # directory (the historical double-prefix bug).
    assert key == "native/matting/models/detection_segm/birefnet.safetensors"
    assert fingerprint == "models/detection_segm/birefnet.safetensors"

    # Moved onto a device and back to CPU (RAM etiquette: released after use).
    assert model.device_calls[0][0] == "to"
    assert model.device_calls[-1] == ("cpu", None)

    assert result.output["image"][0].mode == "RGBA"


def test_models_acquired_once_for_a_multi_image_batch():
    """The model is cached/moved once, not per image - acquiring per image
    would still be correct but defeats the point of caching."""
    model = _FakeMattingModel(raw_alpha=200)
    models = _FakeModels(model)
    pipe = MattingBirefnetPipe(_config())
    pipe.process(
        PipeInput(input={"image": [_input_image(), _input_image(), _input_image()], "MODELS": models}),
        lambda o: None,
    )
    assert len(models.calls) == 1
    assert len(model.device_calls) == 2
    assert model.device_calls[0][0] == "to"
    assert model.device_calls[1] == ("cpu", None)


def test_model_dict_uses_name_when_file_path_missing():
    model = _FakeMattingModel()
    models = _FakeModels(model)
    pipe = MattingBirefnetPipe(_config(model={"name": "checkpoint-only-name"}))
    pipe.process(PipeInput(input={"image": [_input_image()], "MODELS": models}), lambda o: None)
    assert models.calls[0][0] == "native/matting/checkpoint-only-name"


def test_no_models_service_falls_back_to_direct_load(monkeypatch):
    fake = _FakeMattingModel(raw_alpha=180)
    calls = []

    def fake_from_checkpoint(path):
        calls.append(path)
        return fake

    monkeypatch.setattr(
        "src.pipelines.pipes.matting.birefnet.main.BackgroundMattingModel.from_checkpoint",
        fake_from_checkpoint,
    )
    pipe = MattingBirefnetPipe(_config())
    result = pipe.process(PipeInput(input={"image": [_input_image()]}), lambda o: None)

    assert calls == ["models/detection_segm/birefnet.safetensors"]
    assert result.output["image"][0].mode == "RGBA"


# -- matte_strength: the "removed nothing" failure this pipe exists to fix ---

def test_matte_strength_zero_leaves_raw_alpha_unchanged():
    """At strength=0 the alpha is untouched (identity) - a raw model output
    of 140 (barely foreground, the 'removed nothing' failure mode) survives
    as 140."""
    model = _FakeMattingModel(raw_alpha=140)
    models = _FakeModels(model)
    pipe = MattingBirefnetPipe(_config(matte_strength=0))
    result = pipe.process(
        PipeInput(input={"image": [_input_image()], "MODELS": models}), lambda o: None,
    )
    alpha = np.array(result.output["image"][0])[..., 3]
    assert np.all(alpha == 140)


def test_matte_strength_high_commits_mid_grey_alpha_toward_opaque():
    """The bug this pipe must not reintroduce: an empty-subject check on a
    'removed nothing' matte (raw alpha ~140 everywhere, mostly opaque) is
    structurally blind to the failure. matte_strength=100 must commit that
    140 toward fully opaque (140 > 128) rather than leave a wishy-washy
    background residue."""
    model = _FakeMattingModel(raw_alpha=140)
    models = _FakeModels(model)
    pipe = MattingBirefnetPipe(_config(matte_strength=100))
    result = pipe.process(
        PipeInput(input={"image": [_input_image()], "MODELS": models}), lambda o: None,
    )
    alpha = np.array(result.output["image"][0])[..., 3]
    assert np.all(alpha == 255)


def test_matte_strength_below_midpoint_commits_toward_transparent():
    model = _FakeMattingModel(raw_alpha=100)  # below 128
    models = _FakeModels(model)
    pipe = MattingBirefnetPipe(_config(matte_strength=100))
    result = pipe.process(
        PipeInput(input={"image": [_input_image()], "MODELS": models}), lambda o: None,
    )
    alpha = np.array(result.output["image"][0])[..., 3]
    assert np.all(alpha == 0)


# -- feather -------------------------------------------------------------------

def test_feather_softens_a_hard_alpha_edge():
    class _HalfAlphaModel(_FakeMattingModel):
        def __call__(self, image):
            rgb = image.convert("RGB")
            w, h = rgb.size
            alpha = np.zeros((h, w), dtype=np.uint8)
            alpha[:, w // 2:] = 255
            out = rgb.copy()
            out.putalpha(Image.fromarray(alpha, mode="L"))
            return out

    model = _HalfAlphaModel()
    models = _FakeModels(model)
    pipe = MattingBirefnetPipe(_config(matte_strength=0, feather=4.0))
    result = pipe.process(
        PipeInput(input={"image": [_input_image(size=(20, 20))], "MODELS": models}), lambda o: None,
    )
    alpha = np.array(result.output["image"][0])[..., 3]
    edge_strip = alpha[10, 6:14]
    assert any(0 < v < 255 for v in edge_strip)
