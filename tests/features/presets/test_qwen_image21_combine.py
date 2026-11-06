from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from src.features.forms.binding import FormBindingError
from src.features.generation.dto import PromptPair, SegmentInput
from src.features.presets import PresetTemplateLoader
from src.features.presets.loader import read_mode_naming
from src.features.presets.mode_labels import mode_display_name
from src.features.presets.processor import PresetProcessor
from src.features.prompt.resources import resolve_generation_prompts
from src.platform.templating.processor import TemplateProcessor

from ._bound_form import bound_form_data


@pytest.fixture(scope="module")
def template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    found = next((p for p in loader.presets if "marketplace/QwenImage-2.1" in Path(p.path).as_posix()), None)
    if found is None:
        pytest.fail("marketplace/QwenImage-2.1 preset not present")
    return found


def _render(template, **over):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()), settings=Mock(), preset_template_loader=Mock(),
    )
    form = {
        "diffusion_model": "/models/dit.safetensors",
        "text_encoder": "/models/te.safetensors",
        "vae": "/models/vae.safetensors",
        "images": ["/uploads/base.png"],
        **over,
    }
    form = bound_form_data(template, "combine", form)
    pipes = processor.process(template, {"prompts": [], "mode": "combine", "form_data": form})
    return {p.get("id") or p["name"]: p for p in pipes}


def _paths(pipes):
    return [m["path"] for m in pipes["media_loader"]["config"]["media"]]


def test_mode_follows_edit_and_is_named_combine_images(template):
    assert list(template.modes)[:3] == ["txt2img", "edit", "combine"]
    naming = read_mode_naming(Path("content/presets/marketplace/QwenImage-2.1/modes/combine"))
    assert mode_display_name("combine", naming["mode_label"]) == "Combine images"


def test_images_load_in_the_order_the_user_gave_them(template):
    pipes = _render(template, images=["/u/base.png", "/u/c.png", "/u/a.png", "/u/b.png"])
    assert _paths(pipes) == ["/u/base.png", "/u/c.png", "/u/a.png", "/u/b.png"]


def test_object_items_are_loaded_by_their_path(template):
    items = [{"path": "/u/x.png", "type": "image"}, {"path": "/u/y.png", "type": "image"}]
    assert _paths(_render(template, images=items)) == ["/u/x.png", "/u/y.png"]


def test_ten_images_reach_the_encoder_and_generator(template):
    pipes = _render(template, images=[f"/u/r{i}.png" for i in range(10)])
    assert len(_paths(pipes)) == 10
    for name in ("prompt_encoder", "generator/qwen_image21"):
        image = next(i for i in pipes[name]["input"] if i["name"] == "image")
        assert (image["provider"], image["output_var"]) == ("image_fit", "image")
    assert pipes["generator/qwen_image21"]["config"]["mode"] == "edit"
    assert pipes["model_loader/qwen_image21"]["config"]["vision"] is True


def test_more_than_ten_images_are_rejected(template):
    with pytest.raises(FormBindingError):
        _render(template, images=[f"/u/r{i}.png" for i in range(11)])


def test_the_prompt_is_passed_through_untouched(template):
    pipes = _render(template)
    assert "Image 1 is" not in str(pipes["prompt_encoder"]["config"])


def test_there_is_no_region_machinery(template):
    pipes = _render(template)
    assert not {"mask_loader", "inpaint_region_crop", "inpaint_region_restore"} & set(pipes)
    gallery = next(i for i in pipes["gallery"]["input"] if i["name"] == "image")
    assert gallery["provider"] == "generator/qwen_image21"


def test_the_generator_resolves_the_edit_profile_defaults(template):
    cfg = _render(template)["generator/qwen_image21"]["config"]
    assert int(cfg["steps"]) == 40
    assert float(cfg["guidance"]) == 4.0


def test_the_images_field_is_mapped_to_the_encoder_token(template):
    assert template.prompt_resources["combine"] == [
        {"field": "images", "kind": "image", "label": "Images", "token": "<image@>"},
    ]
    assert "edit" not in template.prompt_resources


def test_at_markers_become_the_encoders_image_labels_by_position(template):
    form = {"images": ["storage/uploads/base.png", "storage/uploads/jacket.png", "storage/uploads/hat.png"]}
    pairs = [PromptPair(
        positive="put @[images:storage/uploads/jacket.png] and @[images:storage/uploads/hat.png] on @[images:storage/uploads/base.png]",
    )]
    segments = [SegmentInput(text="@[images:storage/uploads/hat.png]")]
    resolve_generation_prompts(template, "combine", pairs, segments, form)
    assert pairs[0].positive == "put <image2> and <image3> on <image1>"
    assert segments[0].text == "<image3>"


def test_reordering_the_images_renumbers_the_prompt(template):
    pairs = [PromptPair(positive="@[images:storage/uploads/hat.png]")]
    resolve_generation_prompts(
        template, "combine", pairs, None, {"images": ["storage/uploads/hat.png", "storage/uploads/base.png"]},
    )
    assert pairs[0].positive == "<image1>"


def test_a_removed_image_blocks_the_generation(template):
    with pytest.raises(FormBindingError) as exc:
        resolve_generation_prompts(
            template, "combine", [PromptPair(positive="@[images:storage/uploads/gone.png]")], None,
            {"images": ["storage/uploads/base.png"]},
        )
    assert list(exc.value.field_errors) == ["images"]
