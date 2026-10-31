from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.features.forms.binding import bind_form
from src.features.presets.processor import PresetProcessor
from src.pipelines.pipes.prompt_encoder.main import PromptEncoderPipe
from src.platform.templating.processor import TemplateProcessor

_MODELS = {
    "model": "/models/minimax_h3.safetensors",
    "text_encoder": "/models/qwen3_vl.safetensors",
    "video_vae": "/models/h3_video_vae.safetensors",
    "audio_vae": "/models/h3_audio_vae.safetensors",
}



def _pipe(pipes, pid):
    return next(p for p in pipes if p.get("id") == pid or p["name"] == pid)


def _inputs(pipe):
    return {edge["name"]: (edge["provider"], edge["output_var"]) for edge in pipe["input"]}


def _process(template, mode, form, prompt="", prompts=None):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()), settings=Mock(), preset_template_loader=Mock(),
    )
    bound = bind_form(
        template, mode, form_name=None, raw_form_data={**_MODELS, **form}, user_id=None, storage_dir=None,
    )
    return processor.process(template, {
        "prompts": prompts or [{"positive": prompt, "negative": ""}], "mode": mode, "form_data": dict(bound.values),
    })


def _document(pipes):
    encoder = next(p for p in pipes if p["name"] == "prompt_encoder")
    assert encoder["config"]["p_prompt"]["input"] == encoder["config"]["p_prompt"]["output"]
    return encoder["config"]["p_prompt"]["output"]


def _final_prompts(pipes, count):
    config = next(p for p in pipes if p["name"] == "prompt_encoder")["config"]
    pipe = PromptEncoderPipe({**PromptEncoderPipe.get_default_config(), **config})
    p_prompt, n_prompt = config["p_prompt"], config.get("n_prompt", {})
    return [
        pipe._final_prompt_pair(
            p_prompt["output"], p_prompt["input"], n_prompt.get("output", ""), n_prompt.get("input", ""),
            config["pairs"], index,
        )[0]
        for index in range(count)
    ]


def test_the_task_modes_take_the_standard_prompt(h3_template):
    assert not {"remix", "talk"} & set(h3_template.vars.get("promptless_modes", []))
    assert h3_template.vars["optional_prompt_modes"] == ["talk", "upscale", "motion_transfer"]


def test_upscale_without_a_prompt_encodes_an_empty_prompt(h3_template):
    pipes = _process(h3_template, "upscale", {
        "input_video": "/media/clip.mp4", "upscale_model": "/models/h3_upscaler.safetensors",
    })

    assert _document(pipes) == ""


@pytest.mark.parametrize("mode, field", [("remix", "instruction"), ("talk", "scene")])
def test_the_free_text_direction_fields_are_gone(h3_template, mode, field):
    stack = list(h3_template.modes[mode].forms[0].fields)
    names = set()
    while stack:
        node = stack.pop()
        if node.name:
            names.add(node.name)
        if isinstance(node.children, list):
            stack.extend(node.children)
    assert field not in names


@pytest.mark.parametrize("mode", ["remix", "talk"])
def test_every_task_mode_is_declared(h3_template, mode):
    assert mode in h3_template.modes


def test_remix_without_pictures_skips_the_picture_loader(h3_template):
    pipes = _process(h3_template, "remix", {"source_video": "/media/clip.mp4"}, prompt="it is snowing")
    assert _pipe(pipes, "references")["enabled"] is False
    document = _document(pipes)
    assert "[video editing] Re-create <Video 1> with this change: it is snowing retention_analysis" in document
    assert "[Shot 1] it is snowing overall_soundscape" in document
    assert "<Picture" not in document


def test_remix_with_pictures_cites_them(h3_template):
    pipes = _process(h3_template, "remix", {
        "source_video": "/media/clip.mp4", "pictures": ["/media/coat.png"],
    }, prompt="wear this")
    assert _pipe(pipes, "references")["enabled"] is True
    document = _document(pipes)
    assert "+ [reference generation]" in document
    assert "<Picture 1> (appears in [Shot 1]): attribute_transfer" in document


def test_talk_reuses_the_voice_and_muxes_it(h3_template):
    pipes = _process(h3_template, "talk", {
        "portrait": "/media/face.png", "voice": "/media/voice.wav", "words": "hello there",
    })
    generator = _pipe(pipes, "generator")
    assert _inputs(generator)["reference_audios"] == ("reference_audios", "audio")
    assert _inputs(generator)["audio"] == ("reference_audios", "audio")
    assert generator["config"]["audio_source"] == "file"
    assert generator["config"]["length_from"] == "reference_audio"
    document = _document(pipes)
    assert "[audio reuse]" in document
    assert '<Subject 1> (S1) says, "<d>hello there</d>"' in document


def test_talk_without_words_or_a_prompt_uses_the_defaults(h3_template):
    document = _document(_process(h3_template, "talk", {"portrait": "/media/face.png", "voice": "/media/v.wav"}))
    assert "speaks the words of <Audio 1>" in document
    assert "A medium close-up of <Subject 1>" in document


def test_talk_puts_the_prompt_in_the_scene_slot_and_keeps_the_words_verbatim(h3_template):
    pipes = _process(h3_template, "talk", {
        "portrait": "/media/face.png", "voice": "/media/voice.wav", "words": "{hi|bye} ${name}",
    }, prompt="talking to the camera in a cafe")
    document = _document(pipes)
    assert "[Shot 1] talking to the camera in a cafe <Subject 1> (S1)" in document
    assert "A medium close-up" not in document
    assert '<d>{hi|bye} ${name}</d>' in document


_CLIPS = {
    "remix": (
        {"source_video": "/media/clip.mp4"},
        ["with this change: {} retention_analysis", "[Shot 1] {} overall_soundscape"],
    ),
    "talk": ({"portrait": "/media/face.png", "voice": "/media/voice.wav"}, ["[Shot 1] {} <Subject 1> (S1)"]),
}


@pytest.mark.parametrize("mode", list(_CLIPS))
@pytest.mark.parametrize("texts", [("the wind rises", "a bell rings"), ("camera", "hair")])
def test_every_clip_gets_its_own_prompt_inside_the_document(h3_template, mode, texts):
    form, slots = _CLIPS[mode]
    prompts = [{"positive": text, "negative": ""} for text in texts]
    pipes = _process(h3_template, mode, {**form, "quantity": 2}, prompts=prompts)
    first, second = _final_prompts(pipes, 2)
    expected = first
    for slot in slots:
        assert slot.format(texts[0]) in expected
        expected = expected.replace(slot.format(texts[0]), slot.format(texts[1]))
    assert second == expected


def test_talk_without_a_prompt_gives_every_clip_the_default_scene(h3_template):
    form, _ = _CLIPS["talk"]
    prompts = [{"positive": "", "negative": ""}] * 2
    first, second = _final_prompts(_process(h3_template, "talk", {**form, "quantity": 2}, prompts=prompts), 2)
    assert first == second
    assert "[Shot 1] A medium close-up of <Subject 1>" in first
