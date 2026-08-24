"""`src/platform/runtime/native/matting.py`: BiRefNet background matting.

The remap helpers and `BackgroundMattingModel`'s load/use/release lifecycle
were previously defined inside `content/plugins/marketplace/trellis2`'s own pipe
module and are exercised in depth there (`tests/plugins/trellis2/test_matting.py`,
via the re-exported `BiRefNetBackgroundRemover = BackgroundMattingModel`
alias). This file covers the module directly, independent of that plugin,
and the one path that plugin's suite never exercised: `load_matting_model`/
`from_checkpoint`'s missing-file error, which needs no real weights.
"""

import pytest

from src.platform.runtime.native.matting import (
    MATTING_WRAPPER_PREFIXES,
    BackgroundMattingModel,
    load_matting_model,
    remap_matting_key,
    remap_matting_state_dict,
)


class TestRemap:
    def test_strips_a_dataparallel_prefix(self):
        assert remap_matting_key("module.bb.norm3.weight") == "bb.norm3.weight"

    def test_strips_a_torch_compile_prefix(self):
        assert remap_matting_key("_orig_mod.bb.norm3.weight") == "bb.norm3.weight"

    def test_leaves_an_unwrapped_key_alone(self):
        assert remap_matting_key("bb.norm3.weight") == "bb.norm3.weight"

    def test_does_not_eat_squeeze_module(self):
        assert remap_matting_key("squeeze_module.0.conv_in.weight") == "squeeze_module.0.conv_in.weight"

    def test_state_dict_remap_applies_to_every_key(self):
        state = {"module.a": 1, "_orig_mod.b": 2, "c": 3}
        assert remap_matting_state_dict(state) == {"a": 1, "b": 2, "c": 3}

    def test_wrapper_prefixes_are_the_documented_two(self):
        assert MATTING_WRAPPER_PREFIXES == ("module.", "_orig_mod.")


class TestLoadMattingModel:
    def test_missing_checkpoint_raises_naming_the_path(self, tmp_path):
        missing = tmp_path / "nonexistent.safetensors"
        with pytest.raises(ValueError) as excinfo:
            load_matting_model(missing)
        assert str(missing) in str(excinfo.value)

    def test_empty_path_raises(self):
        with pytest.raises(ValueError):
            load_matting_model("")


class TestBackgroundMattingModelLifecycle:
    """Same call-shape contract `content/plugins/marketplace/trellis2` depends on
    (`preprocess_image` reads `np.array(out)[:, :, 3]` off the return), with
    a fake model - no real BiRefNet, no weights."""

    def test_returns_rgba_at_the_input_size(self):
        import numpy as np
        import torch
        from PIL import Image

        class OneBlobModel(torch.nn.Module):
            def forward(self, batch):
                logits = torch.full((batch.shape[0], 1, 1024, 1024), -10.0)
                logits[:, :, 341:683, 341:683] = 10.0
                return [logits]

        model = BackgroundMattingModel(OneBlobModel().eval())
        source = Image.new("RGB", (320, 240), (12, 34, 56))

        output = model(source)

        assert output.mode == "RGBA"
        assert output.size == source.size
        alpha = np.array(output)[:, :, 3]
        assert alpha[120, 160] > 0.8 * 255
        assert alpha[2, 2] < 0.8 * 255

    def test_follows_the_model_between_devices(self):
        import torch

        moved = []

        class RecordingModel(torch.nn.Module):
            def to(self, *args, **kwargs):
                moved.append(args[0])
                return self

            def cpu(self):
                moved.append("cpu")
                return self

        model = BackgroundMattingModel(RecordingModel())
        assert model.device == "cpu"

        model.to("cuda:1")
        assert model.device == "cuda:1"

        model.cpu()
        assert model.device == "cpu"
        assert moved == ["cuda:1", "cpu"]

    def test_from_checkpoint_wraps_the_loaded_model(self, monkeypatch):
        import src.platform.runtime.native.matting as matting_module

        sentinel = object()
        monkeypatch.setattr(matting_module, "load_matting_model", lambda path: sentinel)

        wrapped = BackgroundMattingModel.from_checkpoint("irrelevant.safetensors")

        assert isinstance(wrapped, BackgroundMattingModel)
        assert wrapped.model is sentinel
