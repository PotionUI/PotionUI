import re
from pathlib import Path

import pytest

from tests.features.presets.test_ltx25_ic_lora_captions import ltx25_template, process

PRESET_ROOTS = [Path("content/presets"), *Path("content/plugins").glob("*/*/presets")]
RAW_PROMPT_FALLBACK = re.compile(
    r"(?:prompts\.first|\bitem|\bprompt)\.(?:positive|negative)\)?\s+or\s+['\"][^'\"]"
)
BLANKS = ["   ", "\n\n", " \n\t "]
FALLBACK_MODES = [
    pytest.param("remove_object", {}, id="remove_object"),
    pytest.param("layout_to_render", {}, id="layout_to_render"),
    pytest.param("day_to_night", {}, id="day_to_night"),
    pytest.param("cinemagraph", {}, id="cinemagraph"),
    pytest.param("colorize", {}, id="colorize"),
    pytest.param("add_water", {}, id="add_water"),
    pytest.param("restore", {"restore_task": "restore"}, id="restore"),
    pytest.param("restore", {"restore_task": "deblur"}, id="deblur"),
    pytest.param("restore", {"restore_task": "decompress"}, id="decompress"),
]


def shipped_pipeline_files():
    for root in PRESET_ROOTS:
        yield from sorted(root.rglob("*.yml"))


def encoder_prompts(template, mode, prompt, over):
    config = process(template, mode, prompt, **{"input_image": "/media/frame.png", "style_image": "/media/style.png", "upscale_model": "/models/up.safetensors", **over})["prompt_encoder"]["config"]
    return config["p_prompt"]["output"], config["n_prompt"]["output"]


def test_no_shipped_preset_falls_back_with_a_bare_or_on_the_prompt():
    offenders = [
        f"{path.as_posix()}:{number}"
        for path in shipped_pipeline_files()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if RAW_PROMPT_FALLBACK.search(line)
    ]

    assert offenders == []


@pytest.mark.parametrize("blank", BLANKS)
@pytest.mark.parametrize("mode, over", FALLBACK_MODES)
def test_a_whitespace_only_prompt_renders_the_same_as_an_empty_one(ltx25_template, mode, over, blank):
    assert encoder_prompts(ltx25_template, mode, blank, over) == encoder_prompts(ltx25_template, mode, "", over)
