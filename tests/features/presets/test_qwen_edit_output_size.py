from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest
import torch
from PIL import Image

from src.features.forms.binding import FormBindingError
from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.generator.qwen.main import GeneratorQwenPipe
from src.pipelines.pipes.generator.qwen_image21.main import GeneratorQwenImage21Pipe
from src.pipelines.pipes.image_fit.main import ImageFitPipe
from src.platform.runtime.native.resolution import snap_resolution
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

from ._bound_form import bound_form_data

CASES = {
    "qwen21_combine": ("marketplace/QwenImage-2.1", "combine", "images", "generator/qwen_image21"),
    "qwen10_edit": ("marketplace/QwenImage", "edit", "source_image", "generator/qwen"),
}


@pytest.fixture(scope="module")
def presets():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    return {Path(p.path).as_posix().split("content/presets/")[-1]: p for p in loader.presets}


def _pipes(presets, case, **over):
    preset_key, mode, image_field, generator = CASES[case]
    template = presets[preset_key]
    form = {
        "diffusion_model": "/models/dit.safetensors",
        "text_encoder": "/models/te.safetensors",
        "vae": "/models/vae.safetensors",
        image_field: ["/uploads/base.png", "/uploads/ref.png"],
        **over,
    }
    form = bound_form_data(template, mode, form)
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()), settings=Mock(), preset_template_loader=Mock(),
    )
    pipes = processor.process(template, {"prompts": [], "mode": mode, "form_data": form})
    return {p.get("id") or p["name"]: p for p in pipes}


def _generator_config(presets, case, **over):
    return _pipes(presets, case, **over)[CASES[case][3]]["config"]


@pytest.mark.parametrize("case", list(CASES))
def test_the_default_matches_the_first_image(presets, case):
    cfg = _generator_config(presets, case)
    assert cfg["fit"] == "match"
    assert cfg["resolution"] == "1024x1024"


@pytest.mark.parametrize("case", list(CASES))
def test_a_resolution_alone_keeps_matching_the_first_image(presets, case):
    assert _generator_config(presets, case, resolution="1664x928")["fit"] == "match"


@pytest.mark.parametrize("case", list(CASES))
def test_custom_sends_the_resolution_and_crops_by_default(presets, case):
    cfg = _generator_config(presets, case, output_size="custom", resolution="1664x928")
    assert (cfg["resolution"], cfg["fit"]) == ("1664x928", "crop")


@pytest.mark.parametrize("case", list(CASES))
def test_custom_with_pad_sends_pad(presets, case):
    cfg = _generator_config(presets, case, output_size="custom", resolution="928x1664", output_fit="pad")
    assert (cfg["resolution"], cfg["fit"]) == ("928x1664", "pad")


@pytest.mark.parametrize("case", list(CASES))
def test_an_unknown_output_size_is_rejected(presets, case):
    with pytest.raises(FormBindingError):
        _generator_config(presets, case, output_size="huge")



GENERATORS = {"qwen21_combine": GeneratorQwenImage21Pipe, "qwen10_edit": GeneratorQwenPipe}


class _Gen:
    last = None

    def __init__(self, *_, **__):
        self.spec = SimpleNamespace(family="qwen", variant="qwen", sampling_settings={"shift": 1.0})
        self.encoded = []
        _Gen.last = self

    def snap_resolution(self, width, height):
        return snap_resolution(width, height, 16, 1)

    def latent_shape_for(self, width, height, batch=1):
        return (batch, 16, 1, height // 16, width // 16)

    def sample(self, conditioning, latents_shape, **_):
        return torch.zeros(latents_shape)

    def decode(self, latent, **_):
        return np.zeros((1, 8, 8, 3), dtype=np.uint8)

    def encode_image(self, pixels, **_):
        self.encoded.append(np.array(pixels))
        return torch.zeros(1, 16, 1, 1, 1)


def _consumer_inputs(pipes, provider):
    return sorted(
        name for name, pipe in pipes.items()
        for i in pipe.get("input") or [] if i["name"] == "image" and i["provider"] == provider
    )


@pytest.mark.parametrize("case", list(CASES))
def test_the_encoder_and_the_generator_both_read_the_fitted_pictures(presets, case):
    pipes = _pipes(presets, case)
    assert _consumer_inputs(pipes, "image_fit") == sorted(["prompt_encoder", CASES[case][3]])
    assert _consumer_inputs(pipes, "media_loader") == ["image_fit"]


@pytest.mark.parametrize("case", list(CASES))
@pytest.mark.parametrize("fit", ["crop", "pad"])
def test_both_consumers_get_the_same_first_picture_the_generator_renders_at(presets, case, fit):
    pipes = _pipes(presets, case, output_size="custom", resolution="1664x928", output_fit=fit)
    refs = [Image.new("RGB", (600, 900), (200, 30, 30)), Image.new("RGB", (500, 700), (10, 10, 240))]
    fit_pipe = ImageFitPipe(config={**ImageFitPipe.get_default_config(), **pipes["image_fit"]["config"]})
    fitted = fit_pipe.process(PipeInput(input={"image": refs}), lambda _o: None).output["image"]
    encoder_first = fitted[0]
    generator_cls = GENERATORS[case]
    rendered = pipes[CASES[case][3]]["config"]
    cfg = {**generator_cls.get_default_config(), "mode": "edit", "fit": rendered["fit"], "resolution": rendered["resolution"]}
    cond = SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds={"context": torch.ones(1, 4, 8)})
    bundle = SimpleNamespace(dit=SimpleNamespace(estimated_vram_gb=1.0), te_encoder=object(), vae=object())
    inp = PipeInput(input={"model": bundle, "conditioning": [cond], "seed": [1], "image": fitted})
    with patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None), \
            patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _Gen):
        generator_cls(config=cfg).process(inp, lambda _o: None)
    assert encoder_first.size == (1664, 928)
    assert np.array_equal(_Gen.last.encoded[0], np.asarray(encoder_first))
