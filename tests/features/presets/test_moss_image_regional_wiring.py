from pathlib import Path

import pytest

from src.features.presets import PresetTemplateLoader


@pytest.fixture(scope="module")
def txt2img_pipes():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if "marketplace/MOSS-Image" in Path(p.path).as_posix()), None)
    if template is None:
        pytest.fail("marketplace/MOSS-Image preset not present")
    return template.modes["txt2img"].pipes


def _index(pipes, name):
    return next(i for i, p in enumerate(pipes) if p.name == name)


def test_regional_stage_runs_before_the_prompt_encoder(txt2img_pipes):
    assert _index(txt2img_pipes, "regional_prompt/moss_image") < _index(txt2img_pipes, "prompt_encoder")


def test_regional_stage_compiles_the_already_expanded_pairs(txt2img_pipes):
    stage = txt2img_pipes[_index(txt2img_pipes, "regional_prompt/moss_image")]
    assert stage.configuration["pairs"] == "{{ generation.prompts.pairs }}"


def test_prompt_encoder_takes_its_pairs_from_the_regional_stage(txt2img_pipes):
    stage = txt2img_pipes[_index(txt2img_pipes, "regional_prompt/moss_image")]
    encoder = txt2img_pipes[_index(txt2img_pipes, "prompt_encoder")]
    assert ["pairs", stage.id, "pairs"] in [list(i) for i in encoder.input]
