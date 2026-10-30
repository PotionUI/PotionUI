from pathlib import Path
from unittest.mock import Mock

import pytest

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.pipelines.pipes.prompt_encoder.main import PromptEncoderPipe
from src.platform.templating.processor import TemplateProcessor

from ._bound_form import bound_form_data

WATER_CARD = (
    "Reference shows the original scene. Edited shows the same scene with water added. "
    "ADD WATER {water}. "
    "Subject identity, clothing, framing, and background geometry are identical to the reference; "
    "only water-related elements differ between reference and edited."
)
COLORIZE_CARD = (
    "Reference shows the same scene in grayscale. Edited shows the same scene with natural colors restored. "
    "COLORIZE {color}. "
    "Subject identity, framing, and background geometry are identical to the reference; "
    "only color information differs between reference and edited."
)
DEBLUR_CARD = (
    "Reference shows {scene}, heavily out of focus with soft defocused blur and no fine detail. "
    "Edited shows the same scene in sharp focus with crisp detail and clean edges. "
    "DEBLUR {scene}. "
    "Subject identity, framing, and background geometry are identical to the reference; "
    "only focus and sharpness differ between reference and edited."
)
DECOMPRESS_CARD = (
    "Reference shows {scene}, heavily compressed with visible macroblocking, chroma bleed, and ringing artifacts. "
    "Edited shows the same scene restored to high quality with sharp detail, clean edges, and no compression artifacts. "
    "ENHANCE QUALITY {scene}. "
    "Subject identity, framing, and background geometry are identical to the reference; "
    "only compression artifacts and image quality differ between reference and edited."
)

DEFAULT_WATER = "a clear shallow stream of water flowing across the ground and splashing around everything that moves"
SCENE = "A man walks a dog down a street"


def water(text):
    return WATER_CARD.format(water=text or DEFAULT_WATER)


DEFAULT_COLORS = (
    "the scene in rich natural colors, with warm skin tones, blue sky, green foliage "
    "and clothing and objects in clearly different hues"
)


def colorize(text):
    return COLORIZE_CARD.format(color=text or DEFAULT_COLORS)


def deblur(text):
    return DEBLUR_CARD.format(scene=text or "the scene")


def decompress(text):
    return DECOMPRESS_CARD.format(scene=text or "the scene")


WRAPPERS = [
    pytest.param("add_water", {}, water, id="add_water"),
    pytest.param("colorize", {}, colorize, id="colorize"),
    pytest.param("restore", {"restore_task": "deblur"}, deblur, id="deblur"),
    pytest.param("restore", {"restore_task": "decompress"}, decompress, id="decompress"),
]

PROMPTS = [
    pytest.param(f"{SCENE}.", SCENE, id="trailing_period"),
    pytest.param(f"  {SCENE} ...  ", SCENE, id="padded_ellipsis"),
    pytest.param(f"{SCENE}. .", SCENE, id="spaced_periods"),
    pytest.param(SCENE, SCENE, id="no_period"),
    pytest.param("", "", id="empty"),
    pytest.param("  . ", "", id="only_punctuation"),
    pytest.param("a {dog} runs {0}", "a {dog} runs {0}", id="braces_stay_literal"),
]


@pytest.fixture(scope="module")
def ltx25_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if Path(p.path).as_posix().endswith("marketplace/LTX-2.5")), None)
    if template is None:
        pytest.fail("marketplace/LTX-2.5 preset not present")
    return template


def process(template, mode, prompt, **over):
    return process_images(template, mode, [{"positive": prompt, "negative": ""}], **over)


def process_images(template, mode, prompts, **over):
    form_data = bound_form_data(template, mode, {
        "input_video": "/media/clip.mp4",
        "quantity": len(prompts),
        "model": "/models/ltx25_dit.safetensors",
        "text_encoder": "/models/gemma4.safetensors",
        "video_vae": "/models/ltx25_video_vae.safetensors",
        "effect_lora": "/models/effect.safetensors",
        "restore_lora": "/models/restore.safetensors",
        "deblur_lora": "/models/deblur.safetensors",
        "decompress_lora": "/models/decompress.safetensors",
        "manual_sigmas": "",
        **over,
    })
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()), settings=Mock(), preset_template_loader=Mock(),
    )
    pipes = processor.process(template, {"prompts": prompts, "mode": mode, "form_data": form_data})
    return {pipe.get("id") or pipe["name"]: pipe for pipe in pipes}


@pytest.mark.parametrize("prompt, text", PROMPTS)
@pytest.mark.parametrize("mode, over, card", WRAPPERS)
def test_the_wrapper_is_the_model_card_caption_around_the_users_text(ltx25_template, mode, over, card, prompt, text):
    pipes = process(ltx25_template, mode, prompt, **over)

    assert pipes["prompt_encoder"]["config"]["p_prompt"]["output"] == card(text)


@pytest.mark.parametrize("mode, over, trigger", [
    pytest.param("add_water", {}, "ADD WATER ", id="add_water"),
    pytest.param("colorize", {}, "COLORIZE ", id="colorize"),
    pytest.param("restore", {"restore_task": "deblur"}, "DEBLUR ", id="deblur"),
    pytest.param("restore", {"restore_task": "decompress"}, "ENHANCE QUALITY ", id="decompress"),
])
def test_the_trigger_is_followed_by_the_users_text_and_the_caption_closes_after_it(
    ltx25_template, mode, over, trigger,
):
    pipes = process(ltx25_template, mode, f"{SCENE}.", **over)

    output = pipes["prompt_encoder"]["config"]["p_prompt"]["output"]
    after_trigger = output.split(trigger, 1)[1]
    assert after_trigger.startswith(SCENE)
    assert ".." not in output and ".," not in output
    assert output.endswith("between reference and edited.")
    assert output.count(trigger) == 1


def test_restore_without_a_trigger_passes_the_prompt_through_untouched(ltx25_template):
    pipes = process(ltx25_template, "restore", f"{SCENE}.", restore_task="restore")

    assert pipes["prompt_encoder"]["config"]["p_prompt"]["output"] == f"{SCENE}."


@pytest.mark.parametrize("mode, over", [
    pytest.param("add_water", {}, id="add_water"),
    pytest.param("colorize", {}, id="colorize"),
    pytest.param("restore", {"restore_task": "deblur"}, id="deblur"),
    pytest.param("restore", {"restore_task": "decompress"}, id="decompress"),
])
def test_the_provenance_parameter_keeps_the_users_prompt_not_the_caption(ltx25_template, mode, over):
    pipes = process(ltx25_template, mode, f"{SCENE}.", **over)

    parameters = pipes["param_emitter"]["config"]["parameters"]
    assert ["positive_prompt", [f"{SCENE}."]] in parameters


RESTORE_DEFAULT = (
    "Restored archival footage with natural colour, sharp photographic detail, crisp faces and clothing texture, "
    "natural grain, high resolution footage"
)
NIGHT_DEFAULT = (
    "A realistic nighttime scene with photorealistic moonlight and deep natural shadows. "
    "Only the lighting changes from day to night; identical composition, subjects and camera motion."
)
NIGHT_NEGATIVE = "daylight, sunshine, bright sky, pitch black, underexposed, crushed shadows, low quality, distorted"
CLEAN_PLATE_DEFAULT = (
    "An empty clean plate of the exact same location with no people and no moving objects, only the static background."
)
LAYOUT_DEFAULT = "A cinematic, photorealistic finished shot"
CINEMAGRAPH_DEFAULT = "subtle natural motion in one part of the scene"
CINEMAGRAPH_NEGATIVE = (
    "camera movement, pan, tilt, zoom, parallax, whole image moving, background sliding, "
    "flicker on entire image, noisy texture, crawling texture, distorted, blurry, low quality"
)
LAYOUT_FORM = {"style_image": "/media/first.png", "upscale_model": "/models/upscaler.safetensors"}
CINEMAGRAPH_FORM = {"input_image": "/media/still.png"}


def trimmed(raw):
    return raw.strip().rstrip(". ")


def cinemagraph(raw):
    return (
        f"CINEMAGRAPH_MOTION {raw or CINEMAGRAPH_DEFAULT}. "
        "Tripod locked-off static camera, zero camera movement, everything else stays still, seamless natural loop."
    )


EVERY_MODE = [
    pytest.param("add_water", {}, lambda raw: water(trimmed(raw)), "", id="add_water"),
    pytest.param("colorize", {}, lambda raw: colorize(trimmed(raw)), "", id="colorize"),
    pytest.param("restore", {"restore_task": "deblur"}, lambda raw: deblur(trimmed(raw)), "", id="deblur"),
    pytest.param("restore", {"restore_task": "decompress"}, lambda raw: decompress(trimmed(raw)), "", id="decompress"),
    pytest.param("restore", {"restore_task": "restore"}, lambda raw: raw or RESTORE_DEFAULT, "", id="restore"),
    pytest.param("day_to_night", {}, lambda raw: raw or NIGHT_DEFAULT, NIGHT_NEGATIVE, id="day_to_night"),
    pytest.param("remove_object", {}, lambda raw: raw or CLEAN_PLATE_DEFAULT, "", id="remove_object"),
    pytest.param("layout_to_render", LAYOUT_FORM, lambda raw: raw or LAYOUT_DEFAULT, "", id="layout_to_render"),
    pytest.param("cinemagraph", CINEMAGRAPH_FORM, lambda raw: cinemagraph(trimmed(raw)), CINEMAGRAPH_NEGATIVE, id="cinemagraph"),
]

IMAGE_TEXTS = [
    pytest.param(("a cat sleeps on a sofa.", "a dog runs on a beach."), id="trailing_periods"),
    pytest.param(("a cat sleeps on a sofa", "a dog runs on a beach"), id="no_periods"),
    pytest.param(("the scene", "water"), id="wrapper_words"),
    pytest.param(("water.", "the scene."), id="wrapper_words_with_periods"),
    pytest.param(("", "a dog runs on a beach."), id="empty_first"),
    pytest.param(("a cat sleeps on a sofa.", ""), id="empty_second"),
    pytest.param(("a cat sleeps on a sofa.", "a cat sleeps on a sofa."), id="same_text"),
]


def final_prompts(pipes, count):
    config = pipes["prompt_encoder"]["config"]
    pipe = PromptEncoderPipe({**PromptEncoderPipe.get_default_config(), **config})
    p_prompt, n_prompt = config["p_prompt"], config["n_prompt"]
    return [
        pipe._final_prompt_pair(
            p_prompt["output"], p_prompt["input"], n_prompt["output"], n_prompt["input"], config["pairs"], index,
        )
        for index in range(count)
    ]


@pytest.mark.parametrize("texts", IMAGE_TEXTS)
@pytest.mark.parametrize("mode, over, caption, default_negative", EVERY_MODE)
def test_every_image_gets_its_own_text_inside_its_own_caption(
    ltx25_template, mode, over, caption, default_negative, texts,
):
    prompts = [{"positive": text, "negative": ""} for text in texts]

    pipes = process_images(ltx25_template, mode, prompts, **over)

    assert [positive for positive, _ in final_prompts(pipes, 2)] == [caption(text) for text in texts]


@pytest.mark.parametrize("mode, over, caption, default_negative", EVERY_MODE)
def test_every_image_keeps_its_own_negative_prompt(ltx25_template, mode, over, caption, default_negative):
    prompts = [
        {"positive": "a cat sleeps on a sofa.", "negative": "blurry, ugly"},
        {"positive": "a dog runs on a beach.", "negative": "grainy"},
    ]

    pipes = process_images(ltx25_template, mode, prompts, **over)

    assert [negative for _, negative in final_prompts(pipes, 2)] == ["blurry, ugly", "grainy"]


@pytest.mark.parametrize("negatives", [("", "grainy"), ("grainy", ""), ("", "")])
@pytest.mark.parametrize("mode, over, caption, default_negative", EVERY_MODE)
def test_an_image_without_a_negative_prompt_gets_the_default_of_the_mode(
    ltx25_template, mode, over, caption, default_negative, negatives,
):
    prompts = [{"positive": "a cat sleeps on a sofa.", "negative": negative} for negative in negatives]

    pipes = process_images(ltx25_template, mode, prompts, **over)

    assert [negative for _, negative in final_prompts(pipes, 2)] == [
        negative or default_negative for negative in negatives
    ]


@pytest.mark.parametrize("mode, over, caption, default_negative", EVERY_MODE)
def test_the_first_image_is_captioned_the_same_with_or_without_company(
    ltx25_template, mode, over, caption, default_negative,
):
    alone = process_images(ltx25_template, mode, [{"positive": "a cat sleeps on a sofa.", "negative": ""}], **over)
    together = process_images(ltx25_template, mode, [
        {"positive": "a cat sleeps on a sofa.", "negative": ""}, {"positive": "a dog runs.", "negative": ""},
    ], **over)

    assert final_prompts(alone, 1)[0] == final_prompts(together, 2)[0]
    assert final_prompts(alone, 1)[0][0] == caption("a cat sleeps on a sofa.")

@pytest.mark.parametrize("prompt, text", PROMPTS)
def test_the_cinemagraph_caption_is_the_trigger_the_users_text_and_one_full_stop(ltx25_template, prompt, text):
    pipes = process(ltx25_template, "cinemagraph", prompt, **CINEMAGRAPH_FORM)

    output = pipes["prompt_encoder"]["config"]["p_prompt"]["output"]
    assert output == cinemagraph(text)
    assert ".." not in output
    assert output.count("CINEMAGRAPH_MOTION ") == 1
