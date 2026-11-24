from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
import yaml
from PIL import Image

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.pipelines.pipes._shared.generation import qwen_edit
from src.pipelines.pipes.generator.qwen.main import GeneratorQwenPipe
from src.pipelines.pipes.prompt_encoder.main import PromptEncoderPipe
from src.platform.templating.processor import TemplateProcessor

from ._bound_form import bound_form_data

PRESET_DIR = Path("content/presets/marketplace/QwenImage")
LORA = "/models/qwen_image_in_context_control_union.safetensors"
LORA_SHA256 = "8683edcfe741277155989d505995314e0191c21b77c67a0dbc2980103bddbdd5"
TRIGGER = "Context_Control. "


@pytest.fixture(scope="module")
def qwen_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if Path(p.path).as_posix().endswith("marketplace/QwenImage")), None)
    if template is None:
        pytest.fail("marketplace/QwenImage preset not present")
    return template


def _process(template, prompts=None, **form_over):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    form_data = {
        "diffusion_model": "/models/qwen_image_fp8.safetensors",
        "text_encoder": "/models/qwen_te.safetensors",
        "vae": "/models/qwen_vae.safetensors",
        "control_lora": LORA,
        "source_image": "/media/photo.png",
    }
    form_data.update(form_over)
    form_data = bound_form_data(template, "control", form_data)
    pairs = prompts or [{"positive": "a knight", "negative": ""}]
    pipes = processor.process(template, {"prompts": pairs, "mode": "control", "form_data": form_data})
    return {p.get("id") or p["name"]: p for p in pipes}


def _enabled(pipe):
    return str(pipe.get("enabled", True)).strip().lower() == "true"


def _provider(pipe, name):
    return next(edge["provider"] for edge in pipe["input"] if edge["name"] == name)


def _final_prompts(pipes, count):
    config = pipes["prompt_encoder"]["config"]
    pipe = PromptEncoderPipe({**PromptEncoderPipe.get_default_config(), **config})
    return [
        pipe._final_prompt_pair(
            config["p_prompt"]["output"], config["p_prompt"]["input"],
            config["n_prompt"]["output"], config["n_prompt"]["input"], config["pairs"], index,
        )[0]
        for index in range(count)
    ]


def test_the_mode_is_registered_unvalidated_and_in_the_structure_group():
    preset = yaml.safe_load((PRESET_DIR / "preset.yml").read_text(encoding="utf-8"))
    assert "control" in preset["modes"]
    assert "control" in preset["modes_unvalidated"]
    assert "control" in preset["formulas"]["groups"]
    form = yaml.safe_load((PRESET_DIR / "modes/control/form.yml").read_text(encoding="utf-8"))
    assert form["mode_label"] == "Structure"


def test_every_prompt_starts_with_the_trigger(qwen_template):
    prompts = [{"positive": " a knight ", "negative": ""}, {"positive": "a cat", "negative": ""}]
    pipes = _process(qwen_template, prompts=prompts, quantity=2)
    assert _final_prompts(pipes, 2) == [f"{TRIGGER}a knight", f"{TRIGGER}a cat"]


def test_the_encoder_gets_text_only_so_prompt_weights_still_apply(qwen_template):
    pipes = _process(qwen_template)
    assert all(edge["name"] != "image" for edge in pipes["prompt_encoder"]["input"])
    assert pipes["model_loader/qwen"]["config"].get("vision") is not True
    preset = yaml.safe_load((PRESET_DIR / "preset.yml").read_text(encoding="utf-8"))
    assert "control" not in [entry["mode"] for entry in preset["vars"]["prompt_weights_ignored_with_images"]]


def test_the_lora_loads_first_at_the_guide_strength_then_the_users(qwen_template):
    extra = [{"model": "/models/extra.safetensors", "strength": 0.4}]
    loras = _process(qwen_template, loras=extra)["model_loader/qwen"]["config"]["loras"]
    assert loras == [{"file_path": LORA, "weight": 1.0}, {"file_path": "/models/extra.safetensors", "weight": 0.4}]
    loras = _process(qwen_template, control_strength=0.7)["model_loader/qwen"]["config"]["loras"]
    assert loras[0]["weight"] == 0.7


@pytest.mark.parametrize("guide", ["canny", "depth", "openpose", "lineart", "hed", "normal"])
def test_each_guide_reaches_the_preprocessor_and_feeds_the_fitted_reference(qwen_template, guide):
    pipes = _process(qwen_template, guide=guide)
    (prep,) = pipes["controlnet_preprocessor"]["config"]["preprocessors"]
    assert prep["type"] == guide
    assert _provider(pipes["controlnet_preprocessor"], "image") == "source_loader"
    assert _provider(pipes["image_fit"], "image") == "controlnet_preprocessor"
    assert _provider(pipes["generator/qwen"], "image") == "image_fit"


def test_a_ready_made_map_skips_the_preprocessor(qwen_template):
    pipes = _process(qwen_template, guide_extract=False)
    assert not _enabled(pipes["controlnet_preprocessor"])
    assert _provider(pipes["image_fit"], "image") == "source_loader"
    assert not _enabled(pipes["guide_gallery"])


def test_saving_the_guide_adds_a_labelled_output_only_when_extracting(qwen_template):
    pipes = _process(qwen_template, guide="hed", guide_save=True)
    assert _enabled(pipes["guide_gallery"])
    assert pipes["guide_gallery"]["config"]["label"] == "Guide: Soft edges"
    assert not _enabled(_process(qwen_template, guide_save=True, guide_extract=False)["guide_gallery"])


def test_the_run_is_an_edit_whose_reference_and_canvas_share_one_cropped_size(qwen_template):
    pipes = _process(qwen_template, resolution="832x1216")
    generator = pipes["generator/qwen"]["config"]
    assert generator["mode"] == "edit"
    assert generator["fit"] == "crop"
    assert generator["resolution"] == pipes["image_fit"]["config"]["resolution"] == "832x1216"
    assert pipes["image_fit"]["config"]["fit"] == "crop"


def test_the_lora_recommendation_is_pinned_to_the_published_file():
    form = yaml.safe_load((PRESET_DIR / "modes/control/form.yml").read_text(encoding="utf-8"))
    assert form["requirements"][0]["field"] == "control_lora"
    tab = yaml.safe_load((PRESET_DIR / "modes/control/tabs/generation.yml").read_text(encoding="utf-8"))
    section = next(s for s in tab["fields"] if s.get("label") == "Structure LoRA")
    assert section["configuration"]["experimental"] is True
    field = next(c for c in section["children"] if c["name"] == "control_lora")
    rec = field["configuration"]["recommendations"][0]
    assert rec["sha256"] == LORA_SHA256
    assert rec["name"] == "qwen_image_in_context_control_union.safetensors"
    assert field["required"] is True


class _Generator:
    def __init__(self):
        self.encoded = []
        self.sampled = None

    def snap_resolution(self, width, height):
        return (width // 16 * 16, height // 16 * 16)

    def encode_image(self, array):
        self.encoded.append(array.shape)
        return f"latent{len(self.encoded)}"

    def latent_shape_for(self, width, height):
        return (1, 16, height // 8, width // 8)

    def sample(self, conditioning, shape, **kwargs):
        self.sampled = (conditioning, shape)
        return "latent"

    def decode(self, latent):
        return np.zeros((1, 64, 64, 3), dtype=np.uint8)


def test_the_guide_is_passed_as_the_clean_reference_latent_at_the_canvas_size(monkeypatch):
    monkeypatch.setattr(qwen_edit, "native_step_hooks", lambda *a, **k: None)
    gen = _Generator()
    guide = Image.new("RGB", (832, 1216), "white")
    cond = SimpleNamespace(embeds={"c": 1}, n_embeds={"c": 0})
    ctx = SimpleNamespace(
        quantity=1, is_cancelled=lambda: False,
        extra={
            "mode": "edit", "images": [guide], "generator": gen, "conditioning": [cond], "steps": 4,
            "guidance": 4.0, "sampler": "euler", "width": 832, "height": 1216,
        },
    )
    pipe = GeneratorQwenPipe({**GeneratorQwenPipe.get_default_config(), "fit": "crop"})
    output = pipe.maybe_edit(ctx, 0, 7, Mock())
    assert output is not None
    assert gen.encoded == [(1216, 832, 3)]
    conditioning, shape = gen.sampled
    assert conditioning.cond["ref_latents"] == ["latent1"]
    assert conditioning.uncond["ref_latents"] == ["latent1"]
    assert shape == (1, 16, 152, 104)
