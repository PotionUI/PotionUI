from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from src.features.presets import PresetTemplateLoader
from src.features.presets.loader import read_mode_naming
from src.features.presets.mode_labels import mode_display_icon, mode_display_name
from src.features.presets.processor import PresetProcessor
from src.pipelines.pipes.prompt_encoder.main import PromptEncoderPipe
from src.platform.templating.processor import TemplateProcessor

from ._bound_form import bound_form_data

BASE = {
    "diffusion_model": "/models/dit.safetensors",
    "text_encoder": "/models/te.safetensors",
    "vae": "/models/vae.safetensors",
    "source_image": "/uploads/photo.png",
}
STYLE = "Restyle the whole image as watercolour. Keep the composition, subjects and layout unchanged."
BACKGROUND = "Replace the background with a white studio. Keep the subject, its pose and its lighting unchanged."
LIGHTING = "Change the lighting to golden hour. Keep the composition, subjects and colours of objects unchanged."
COLOURISE = "Colourise this black and white photo with natural, realistic colours."
PHOTO = "Turn this image into a realistic photograph. Keep the composition, subjects, layout and colours unchanged."
RESTORE = (
    "Restore this old photo: remove scratches, dust and fading, repair damage and sharpen detail, "
    "keeping the people and scene exactly as they are."
)


@pytest.fixture(scope="module")
def template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    found = next((p for p in loader.presets if "marketplace/QwenImage-2.1" in Path(p.path).as_posix()), None)
    if found is None:
        pytest.fail("marketplace/QwenImage-2.1 preset not present")
    return found


def _render(template, prompt="", prompts=None, **over):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()), settings=Mock(), preset_template_loader=Mock(),
    )
    form = bound_form_data(template, "restyle", {**BASE, **over})
    pairs = prompts or [{"positive": prompt, "negative": ""}]
    pipes = processor.process(template, {"prompts": pairs, "mode": "restyle", "form_data": form})
    return {p.get("id") or p["name"]: p for p in pipes}


def _prompt(pipes):
    return pipes["prompt_encoder"]["config"]["p_prompt"]["output"]


def _emitted(pipes, name):
    parameters = pipes["param_emitter"]["config"]["parameters"]
    flat = [entry for item in parameters for entry in (item if item and isinstance(item[0], list) else [item])]
    return [entry[1] for entry in flat if entry and entry[0] == name]


def _final_prompts(pipes, count):
    config = pipes["prompt_encoder"]["config"]
    pipe = PromptEncoderPipe({**PromptEncoderPipe.get_default_config(), **config})
    return [
        pipe._final_prompt_pair(
            config["p_prompt"]["output"], config["p_prompt"]["input"],
            config["n_prompt"]["output"], config["n_prompt"]["input"], config["pairs"], index,
        )
        for index in range(count)
    ]


def test_mode_follows_text_is_named_restyle_and_takes_the_standard_prompt(template):
    assert list(template.modes)[:5] == ["txt2img", "edit", "combine", "text", "restyle"]
    naming = read_mode_naming(Path("content/presets/marketplace/QwenImage-2.1/modes/restyle"))
    assert mode_display_name("restyle", naming["mode_label"]) == "Restyle"
    assert mode_display_icon("restyle") == "mode-restyle"
    assert all(isinstance(e, dict) and e["mode"] != "restyle" for e in template.vars["promptless_modes"])
    entry = next(e for e in template.vars["optional_prompt_modes"] if isinstance(e, dict) and e["mode"] == "restyle")
    assert entry["when"] == {"field": "restyle_task", "in": ["colourise", "restore", "photo"]}


def test_the_description_field_is_gone(template):
    stack = list(template.modes["restyle"].forms[0].fields)
    names = set()
    while stack:
        node = stack.pop()
        if node.name:
            names.add(node.name)
        if isinstance(node.children, list):
            stack.extend(node.children)
    assert "restyle_text" not in names
    assert "restyle_task" in names


@pytest.mark.parametrize("task, text, expected", [
    ("style", "watercolour", STYLE),
    ("background", "a white studio", BACKGROUND),
    ("lighting", "golden hour", LIGHTING),
    ("colourise", "", COLOURISE),
    ("restore", "", RESTORE),
    ("photo", "", PHOTO),
    ("photo", " a knight in a field ", PHOTO.replace("photograph.", "photograph. It shows a knight in a field.")),
    ("colourise", "  warm tones ", COLOURISE + " Details: warm tones."),
    ("restore", "faces first", RESTORE + " Details: faces first."),
])
def test_each_task_builds_its_documented_prompt(template, task, text, expected):
    pipes = _render(template, prompt=text, restyle_task=task)
    assert _prompt(pipes) == expected
    assert _final_prompts(pipes, 1)[0][0] == expected


def test_default_task_is_style(template):
    assert _prompt(_render(template, prompt="watercolour")) == STYLE


def test_history_records_the_same_prompt(template):
    pipes = _render(template, prompt="golden hour", restyle_task="lighting")
    assert _emitted(pipes, "positive_prompt") == [LIGHTING]


@pytest.mark.parametrize("task", ["style", "background", "lighting", "photo", "colourise", "restore"])
def test_no_description_is_asked_for_by_the_form(template, task):
    bound_form_data(template, "restyle", {**BASE, "restyle_task": task})


def test_every_image_gets_its_own_wrapped_prompt(template):
    prompts = [{"positive": "watercolour", "negative": "blurry"}, {"positive": "oil painting", "negative": "noisy"}]
    pipes = _render(template, prompts=prompts, quantity=2)
    first, second = _final_prompts(pipes, 2)
    assert first == (STYLE, "blurry")
    assert second == (STYLE.replace("watercolour", "oil painting"), "noisy")
    assert _emitted(pipes, "positive_prompt") == [first[0], second[0]]
    assert _emitted(pipes, "negative_prompt") == [["blurry", "noisy"]]


def test_a_prompt_that_repeats_the_wrapper_words_still_lands_in_its_slot(template):
    prompts = [{"positive": "the whole image", "negative": ""}, {"positive": "composition", "negative": ""}]
    first, second = _final_prompts(_render(template, prompts=prompts, quantity=2), 2)
    assert first[0] == STYLE.replace("watercolour", "the whole image")
    assert second[0] == STYLE.replace("watercolour", "composition")


def test_the_flow_is_a_plain_edit_for_every_task(template):
    for task in ("style", "background", "lighting", "photo", "colourise", "restore"):
        pipes = _render(template, restyle_task=task, restyle_text="x")
        assert not {"matting", "subject_composite"} & set(pipes)
        gallery = next(i for i in pipes["gallery"]["input"] if i["name"] == "image")
        assert gallery["provider"] == "generator/qwen_image21"
        assert pipes["generator/qwen_image21"]["config"]["mode"] == "edit"


def test_restyle_has_no_matting_fields(template):
    stack = list(template.modes["restyle"].forms[0].fields)
    names = set()
    while stack:
        node = stack.pop()
        if node.name:
            names.add(node.name)
        if isinstance(node.children, list):
            stack.extend(node.children)
    assert not {"keep_subject", "matting_model"} & names


def test_every_restyle_reaction_is_a_single_condition(template):
    stack = list(template.modes["restyle"].forms[0].fields)
    seen = 0
    while stack:
        node = stack.pop()
        for reaction in node.reactions or []:
            seen += 1
            assert isinstance(reaction["when"], dict) and "field" in reaction["when"]
        if isinstance(node.children, list):
            stack.extend(node.children)
    assert seen


def test_generator_resolves_the_edit_profile_defaults(template):
    cfg = _render(template, restyle_text="x")["generator/qwen_image21"]["config"]
    assert int(cfg["steps"]) == 40
    assert float(cfg["guidance"]) == 4.0
