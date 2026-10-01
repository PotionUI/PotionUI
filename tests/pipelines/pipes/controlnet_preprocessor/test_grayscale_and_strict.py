import numpy as np
import pytest
from PIL import Image

from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import GenerationExecutionError
from src.pipelines.pipes.controlnet_preprocessor import main as main_module
from src.pipelines.pipes.controlnet_preprocessor.main import ControlNetPreprocessorPipe


def _process(preprocessors, images, **config):
    pipe = ControlNetPreprocessorPipe(config={"preprocessors": preprocessors, **config})
    return pipe.process(PipeInput(input={"image": images}), lambda _o: None)


@pytest.mark.parametrize("aux_available", [True, False])
def test_grayscale_needs_neither_annotator_weights_nor_controlnet_aux(monkeypatch, aux_available):
    monkeypatch.setattr(main_module, "CONTROLNET_AUX_AVAILABLE", aux_available)
    image = Image.new("RGB", (8, 4), (200, 30, 90))

    result = _process([{"type": "grayscale", "enabled": True}], [image], strict=True)

    out = np.asarray(result.output["image"][0])
    assert out.shape == (4, 8, 3)
    assert (out[..., 0] == out[..., 1]).all() and (out[..., 1] == out[..., 2]).all()
    assert out[0, 0, 0] == np.asarray(image.convert("L"))[0, 0]


def test_strict_refuses_to_pass_a_photo_through_without_controlnet_aux(monkeypatch):
    monkeypatch.setattr(main_module, "CONTROLNET_AUX_AVAILABLE", False)
    with pytest.raises(GenerationExecutionError, match="controlnet-aux"):
        _process([{"type": "canny", "enabled": True}], [Image.new("RGB", (4, 4))], strict=True)


def test_strict_refuses_a_failed_detector(monkeypatch):
    monkeypatch.setattr(main_module, "CONTROLNET_AUX_AVAILABLE", True)
    monkeypatch.setattr(ControlNetPreprocessorPipe, "_preprocess_canny", lambda self, image, params: image)
    with pytest.raises(GenerationExecutionError, match="canny"):
        _process([{"type": "canny", "enabled": True}], [Image.new("RGB", (4, 4))], strict=True)


def test_without_strict_a_missing_controlnet_aux_still_passes_through(monkeypatch):
    monkeypatch.setattr(main_module, "CONTROLNET_AUX_AVAILABLE", False)
    image = Image.new("RGB", (4, 4))
    result = _process([{"type": "canny", "enabled": True}], [image])
    assert result.output["image"] == [image]
