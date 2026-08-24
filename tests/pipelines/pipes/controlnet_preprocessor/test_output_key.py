"""Every early return in `process` must key its output as `image`, matching
what `outputs()` declares and what the success path emits - a pass-through
that emits `control_image` produces a value no downstream pipe input can
bind to.
"""

from unittest.mock import Mock

import pytest

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.controlnet_preprocessor import main as main_module
from src.pipelines.pipes.controlnet_preprocessor.main import ControlNetPreprocessorPipe


def _pipe(config=None):
    pipe = ControlNetPreprocessorPipe(config=config or {})
    return pipe


def _noop_outputs(_output):
    pass


class TestFallbackOutputKey:
    def test_no_input_images_emits_image_key(self):
        pipe = _pipe(config={"preprocessors": [{"type": "canny", "enabled": True}]})

        result = pipe.process(PipeInput(input={"image": []}), _noop_outputs)

        assert "image" in result.output
        assert "control_image" not in result.output
        assert result.output["image"] == []

    def test_no_preprocessors_configured_passes_through_under_image_key(self):
        pipe = _pipe(config={"preprocessors": []})
        images = [Mock(name="pil-image")]

        result = pipe.process(PipeInput(input={"image": images}), _noop_outputs)

        assert "image" in result.output
        assert "control_image" not in result.output
        assert result.output["image"] == images

    def test_controlnet_aux_missing_passes_through_under_image_key(self, monkeypatch):
        monkeypatch.setattr(main_module, "CONTROLNET_AUX_AVAILABLE", False)
        pipe = _pipe(config={"preprocessors": [{"type": "canny", "enabled": True}]})
        images = [Mock(name="pil-image")]

        result = pipe.process(PipeInput(input={"image": images}), _noop_outputs)

        assert "image" in result.output
        assert "control_image" not in result.output
        assert result.output["image"] == images
