"""Tests for the matting/birefnet pipe: config/IO contract, the LIST-in/
LIST-out array contract every pipe in this family shares with its upstream
producer (`media_loader`) and downstream consumer (`gallery`), model-path
resolution (file_path used as-is, never joined onto a models dir - see
main.py's docstring and the historical double-prefix bug), matte_strength
commitment of mid-grey pixels, feather, and MODELS-service caching/device
handling - all against a fake matting model, no real checkpoint or GPU.
"""

from __future__ import annotations

import time

import numpy as np
import pytest
from PIL import Image

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.matting.birefnet import main as matting_main
from src.pipelines.pipes.matting.birefnet.main import MattingBirefnetPipe

#: A real file (this test module itself) so `_model_fingerprint`'s `stat()`
#: has something to fingerprint - the pipe no longer accepts a made-up path
#: once MEDIA-03 fingerprints on-disk revision rather than the raw string.
_MODEL_PATH = __file__


@pytest.fixture(autouse=True)
def _clear_raw_matte_cache():
    """`_RAW_MATTE_CACHE` (INF-B01) is a module-level global: without this,
    two tests using the same image size/content but a different fake model's
    `raw_alpha` would leak a cache hit from one test into the other."""
    matting_main._RAW_MATTE_CACHE._entries.clear()
    matting_main._RAW_MATTE_CACHE._bytes = 0
    yield
    matting_main._RAW_MATTE_CACHE._entries.clear()
    matting_main._RAW_MATTE_CACHE._bytes = 0


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
    cfg.update({"model": {"file_path": _MODEL_PATH, "name": "birefnet"}})
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
    assert key == f"native/matting/{_MODEL_PATH}"
    assert fingerprint == matting_main._model_fingerprint(_MODEL_PATH)

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
    pipe = MattingBirefnetPipe(_config(model={"name": _MODEL_PATH}))
    pipe.process(PipeInput(input={"image": [_input_image()], "MODELS": models}), lambda o: None)
    assert models.calls[0][0] == f"native/matting/{_MODEL_PATH}"


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

    assert calls == [_MODEL_PATH]
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


# -- raw-matte cache (INF-B01): BiRefNet's transform is a fixed constant, so
# the raw alpha it produces is a pure function of (checkpoint identity +
# revision, RGB bytes, dimensions) - a re-run that changes only
# matte_strength/feather should skip the model entirely. -------------------

class _CountingMattingModel(_FakeMattingModel):
    """Same call shape as `_FakeMattingModel`, plus an inference-call count -
    the "avoided model calls" signal these tests assert on."""

    def __init__(self, raw_alpha=200):
        super().__init__(raw_alpha)
        self.call_count = 0

    def __call__(self, image: Image.Image) -> Image.Image:
        self.call_count += 1
        return super().__call__(image)


def test_warm_cache_skips_the_model_and_returns_the_same_output(monkeypatch):
    import torch
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    model = _CountingMattingModel(raw_alpha=170)
    models = _FakeModels(model)
    pipe = MattingBirefnetPipe(_config())
    image = _input_image()

    cold = pipe.process(PipeInput(input={"image": [image], "MODELS": models}), lambda o: None)
    warm = pipe.process(PipeInput(input={"image": [image], "MODELS": models}), lambda o: None)

    assert model.call_count == 1  # inference ran only on the cold call
    assert len(models.calls) == 1  # the warm call never re-acquires the model either
    assert np.array_equal(np.array(cold.output["image"][0]), np.array(warm.output["image"][0]))
    # No device round-trip on the warm, cache-only call.
    assert model.device_calls == [("to", "cpu"), ("cpu", None)]


def test_warm_cache_applies_a_changed_matte_strength_without_calling_the_model():
    model = _CountingMattingModel(raw_alpha=140)
    models = _FakeModels(model)
    image = _input_image()

    pipe_a = MattingBirefnetPipe(_config(matte_strength=0))
    pipe_a.process(PipeInput(input={"image": [image], "MODELS": models}), lambda o: None)
    assert model.call_count == 1

    pipe_b = MattingBirefnetPipe(_config(matte_strength=100))
    warm = pipe_b.process(PipeInput(input={"image": [image], "MODELS": models}), lambda o: None)

    assert model.call_count == 1  # still one -- the raw matte was reused, not recomputed
    alpha = np.array(warm.output["image"][0])[..., 3]
    assert np.all(alpha == 255)  # matte_strength=100 on a raw 140 commits to opaque

    # Equals an independent, uncached computation at the same strength.
    matting_main._RAW_MATTE_CACHE._entries.clear()
    matting_main._RAW_MATTE_CACHE._bytes = 0
    fresh_model = _CountingMattingModel(raw_alpha=140)
    fresh_models = _FakeModels(fresh_model)
    fresh_pipe = MattingBirefnetPipe(_config(matte_strength=100))
    fresh = fresh_pipe.process(PipeInput(input={"image": [image], "MODELS": fresh_models}), lambda o: None)
    assert np.array_equal(np.array(warm.output["image"][0]), np.array(fresh.output["image"][0]))


def test_changed_pixels_miss_the_cache():
    model = _CountingMattingModel(raw_alpha=200)
    models = _FakeModels(model)
    pipe = MattingBirefnetPipe(_config())

    pipe.process(PipeInput(input={"image": [_input_image(color=(1, 1, 1))], "MODELS": models}), lambda o: None)
    pipe.process(PipeInput(input={"image": [_input_image(color=(2, 2, 2))], "MODELS": models}), lambda o: None)

    assert model.call_count == 2


def test_changed_dimensions_miss_the_cache():
    model = _CountingMattingModel(raw_alpha=200)
    models = _FakeModels(model)
    pipe = MattingBirefnetPipe(_config())

    pipe.process(PipeInput(input={"image": [_input_image(size=(16, 16))], "MODELS": models}), lambda o: None)
    pipe.process(PipeInput(input={"image": [_input_image(size=(32, 32))], "MODELS": models}), lambda o: None)

    assert model.call_count == 2


def test_changed_checkpoint_revision_misses_the_cache(tmp_path):
    checkpoint = tmp_path / "birefnet.safetensors"
    checkpoint.write_bytes(b"v1")
    model = _CountingMattingModel(raw_alpha=200)
    models = _FakeModels(model)
    pipe = MattingBirefnetPipe(_config(model={"file_path": str(checkpoint)}))
    image = _input_image()

    pipe.process(PipeInput(input={"image": [image], "MODELS": models}), lambda o: None)
    time.sleep(0.01)
    checkpoint.write_bytes(b"v2-is-longer")
    pipe.process(PipeInput(input={"image": [image], "MODELS": models}), lambda o: None)

    assert model.call_count == 2


def test_downstream_mutation_of_a_fetched_raw_alpha_does_not_corrupt_the_cache():
    """`np.dstack` in `_finalize` already copies its inputs, so mutating the
    PIL image `process()` returns can never reach the cache - the actual risk
    is a caller holding the array `_RAW_MATTE_CACHE.get()` itself hands back,
    keyed exactly as the pipe derives it."""
    model = _CountingMattingModel(raw_alpha=200)
    models = _FakeModels(model)
    pipe = MattingBirefnetPipe(_config(matte_strength=0, feather=0.0))
    image = _input_image()

    pipe.process(PipeInput(input={"image": [image], "MODELS": models}), lambda o: None)

    rgb = image.convert("RGB")
    fingerprint = matting_main._model_fingerprint(_MODEL_PATH)
    key = matting_main._raw_matte_cache_key(_MODEL_PATH, fingerprint, rgb)
    fetched = matting_main._RAW_MATTE_CACHE.get(key)
    fetched[:] = 0  # mutate what a caller got back from the cache

    second = pipe.process(PipeInput(input={"image": [image], "MODELS": models}), lambda o: None)

    assert model.call_count == 1  # still a cache hit
    alpha = np.array(second.output["image"][0])[..., 3]
    assert np.all(alpha == 200)  # unaffected by the mutation of the fetched copy


# -- _RawMatteCache: budget/eviction/oversize-skip, in isolation ------------

def test_raw_matte_cache_evicts_lru_under_its_byte_budget():
    cache = matting_main._RawMatteCache(max_bytes=30)
    a = np.zeros((5, 5), dtype=np.uint8)  # 25 bytes
    b = np.ones((5, 5), dtype=np.uint8)  # 25 bytes

    cache.put("a", a)
    cache.put("b", b)  # 25 + 25 = 50 > 30 -> "a" (least recently used) evicted

    assert cache.get("a") is None
    assert cache.get("b") is not None


def test_raw_matte_cache_skips_an_entry_larger_than_the_whole_budget():
    cache = matting_main._RawMatteCache(max_bytes=10)
    huge = np.zeros((5, 5), dtype=np.uint8)  # 25 bytes > 10-byte budget

    cache.put("k", huge)  # must not be stored, and must not raise

    assert cache.get("k") is None
    assert cache._bytes == 0


def test_raw_matte_cache_get_and_put_are_isolated_copies():
    cache = matting_main._RawMatteCache(max_bytes=1000)
    original = np.array([[1, 2], [3, 4]], dtype=np.uint8)

    cache.put("k", original)
    original[0, 0] = 99  # mutating the source after put() must not reach the cache

    fetched = cache.get("k")
    assert fetched[0, 0] == 1

    fetched[0, 0] = 50  # mutating what get() returned must not reach the cache
    assert cache.get("k")[0, 0] == 1
