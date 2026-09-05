"""Cancellation contract for GeneratorSDXLPipe.process().

`is_cancelled` is polled between images in both the txt2img and img2img batch
loops (`_generate_txt2img`/`_generate_img2img`) and, when the target model
method declares the parameter, forwarded into it (`_call_with_cancellation`).
Cancelling must raise the shared `SamplingCancelled` -- never a partial
gallery reported as success -- and pipeline cleanup must still run.
"""
from unittest.mock import Mock

import pytest
from PIL import Image

from src.pipelines.pipes.generator.sdxl.main import GeneratorSDXLPipe
from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import ImageGenerationOutput
from src.platform.runtime.native.errors import SamplingCancelled


@pytest.fixture(autouse=True)
def no_real_model_lifecycle(monkeypatch):
    # Deterministic cleanup path: _aggressive_cleanup falls back to
    # model.clear_cuda_cache(aggressive=True) only when the real lifecycle
    # singleton is absent.
    monkeypatch.setattr(
        "src.platform.runtime.model_lifecycle.lifecycle.get_model_lifecycle",
        lambda: None,
    )


@pytest.fixture
def mock_model():
    model = Mock()
    model.txt2img = Mock(side_effect=lambda *a, **kw: ImageGenerationOutput(
        image=Image.new("RGB", (64, 64), color="red"), seed=1,
    ))
    model.img2img = Mock(side_effect=lambda *a, **kw: ImageGenerationOutput(
        image=Image.new("RGB", (64, 64), color="blue"), seed=1,
    ))
    model.load_with_controlnet = Mock()
    return model


@pytest.fixture
def mock_conditioning():
    import torch
    conditioning = Mock()
    conditioning.embeds = {"embeds": torch.randn(1, 77, 2048), "pooled": torch.randn(1, 1280)}
    conditioning.n_embeds = {"embeds": torch.randn(1, 77, 2048), "pooled": torch.randn(1, 1280)}
    return conditioning


@pytest.fixture
def generator_pipe():
    config = GeneratorSDXLPipe.get_default_config()
    config["quantity"] = 3
    return GeneratorSDXLPipe(config)


class TestTxt2ImgCancellation:
    def test_stops_between_images_and_raises(self, generator_pipe, mock_model, mock_conditioning):
        pipe_input = PipeInput(input={
            "model": mock_model, "conditioning": [mock_conditioning], "seed": [1],
        })

        with pytest.raises(SamplingCancelled):
            generator_pipe.process(pipe_input, Mock(), is_cancelled=lambda: mock_model.txt2img.call_count >= 1)

        # The 2nd/3rd of the 3 configured images never started.
        assert mock_model.txt2img.call_count == 1

    def test_cancelling_before_the_first_image_calls_nothing(self, generator_pipe, mock_model, mock_conditioning):
        pipe_input = PipeInput(input={
            "model": mock_model, "conditioning": [mock_conditioning], "seed": [1],
        })
        outputs = Mock()

        with pytest.raises(SamplingCancelled):
            generator_pipe.process(pipe_input, outputs, is_cancelled=lambda: True)

        assert mock_model.txt2img.call_count == 0
        # No partial gallery is ever reported as a successful result.
        outputs.assert_not_called()

    def test_cleanup_runs_on_cancellation(self, generator_pipe, mock_model, mock_conditioning):
        pipe_input = PipeInput(input={
            "model": mock_model, "conditioning": [mock_conditioning], "seed": [1],
        })

        with pytest.raises(SamplingCancelled):
            generator_pipe.process(pipe_input, Mock(), is_cancelled=lambda: True)

        assert mock_model.clear_cuda_cache.called

    def test_no_cancellation_runs_every_image_in_order(self, generator_pipe, mock_model, mock_conditioning):
        pipe_input = PipeInput(input={
            "model": mock_model, "conditioning": [mock_conditioning], "seed": [1],
        })

        result = generator_pipe.process(pipe_input, Mock(), is_cancelled=lambda: False)

        assert mock_model.txt2img.call_count == 3
        assert len(result.output["image"]) == 3
        assert mock_model.clear_cuda_cache.called

    def test_is_cancelled_forwarded_when_model_method_declares_it(
        self, generator_pipe, mock_model, mock_conditioning
    ):
        received = {}

        def fake_txt2img(generation_input, generation_outputs, is_cancelled=None):
            received["probe"] = is_cancelled
            return ImageGenerationOutput(image=Image.new("RGB", (64, 64)), seed=1)

        mock_model.txt2img = fake_txt2img
        probe = lambda: False
        pipe_input = PipeInput(input={
            "model": mock_model, "conditioning": [mock_conditioning], "seed": [1],
        })

        generator_pipe.process(pipe_input, Mock(), is_cancelled=probe)

        assert received["probe"] is probe

    def test_is_cancelled_not_forwarded_when_model_method_lacks_it(
        self, generator_pipe, mock_model, mock_conditioning
    ):
        """Existing (unmigrated) model methods must keep working unchanged --
        the two-arg Mock() signature from test_integration_refactored.py."""
        pipe_input = PipeInput(input={
            "model": mock_model, "conditioning": [mock_conditioning], "seed": [1],
        })

        generator_pipe.process(pipe_input, Mock(), is_cancelled=lambda: False)

        for call in mock_model.txt2img.call_args_list:
            assert "is_cancelled" not in call.kwargs


class TestImg2ImgCancellation:
    def test_stops_between_images_and_raises(self, generator_pipe, mock_model, mock_conditioning):
        pipe_input = PipeInput(input={
            "model": mock_model,
            "conditioning": [mock_conditioning],
            "seed": [1],
            "image": [Image.new("RGB", (64, 64)), Image.new("RGB", (64, 64))],
        })

        with pytest.raises(SamplingCancelled):
            generator_pipe.process(pipe_input, Mock(), is_cancelled=lambda: mock_model.img2img.call_count >= 1)

        assert mock_model.img2img.call_count == 1
