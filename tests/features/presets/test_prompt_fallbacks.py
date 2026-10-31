import re
from pathlib import Path

import pytest

from tests.features.presets.test_ltx25_ic_lora_captions import ltx25_template, process, process_images

REPO_ROOT = Path(__file__).resolve().parents[3]
PRESET_ROOTS = [REPO_ROOT / "content" / "presets", *(REPO_ROOT / "content" / "plugins").glob("*/*/presets")]
RAW_PROMPT_FALLBACK = re.compile(r"\.(?:positive|negative)[\s)]*\bor\s+['\"][^'\"]")
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
    files = list(shipped_pipeline_files())
    assert any(path.name == "pipeline.yml" for path in files)
    offenders = [
        f"{path.relative_to(REPO_ROOT).as_posix()}:{number}"
        for path in files
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if RAW_PROMPT_FALLBACK.search(line)
    ]

    assert offenders == []


@pytest.mark.parametrize("blank", BLANKS)
@pytest.mark.parametrize("mode, over", FALLBACK_MODES)
def test_a_whitespace_only_prompt_renders_the_same_as_an_empty_one(ltx25_template, mode, over, blank):
    assert encoder_prompts(ltx25_template, mode, blank, over) == encoder_prompts(ltx25_template, mode, "", over)


@pytest.mark.parametrize("blank", BLANKS)
@pytest.mark.parametrize("mode", ["day_to_night", "cinemagraph"])
def test_a_whitespace_only_negative_prompt_uses_the_default_negative(ltx25_template, mode, blank):
    base = {"input_image": "/media/frame.png"}
    blanked = process_images(ltx25_template, mode, [{"positive": "a dog", "negative": blank}], **base)
    empty = process_images(ltx25_template, mode, [{"positive": "a dog", "negative": ""}], **base)

    negative = blanked["prompt_encoder"]["config"]["n_prompt"]["output"]
    assert negative.strip()
    assert negative == empty["prompt_encoder"]["config"]["n_prompt"]["output"]
