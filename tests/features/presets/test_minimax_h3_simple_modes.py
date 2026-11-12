from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from src.features.forms.binding import bind_form
from src.features.generation.orchestrator import _prepare_director_form_data
from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.pipeline_source import resolve_mode_pipeline_file
from src.features.presets.processor import PresetProcessor
from src.features.video_director.normalize import VideoDirectorValidationError
from src.platform.templating.processor import TemplateProcessor

PROMPT = "a paper boat drifting down a rain-filled gutter"
SEED = 4242

MODEL_FIELDS = {
    "model": "/models/minimax_h3.safetensors",
    "text_encoder": "/models/qwen3_vl.safetensors",
    "video_vae": "/models/h3_video_vae.safetensors",
    "audio_vae": "/models/h3_audio_vae.safetensors",
}

CASES = [
    pytest.param("txt2video", "t2v", 5, None, None, id="text-to-video"),
    pytest.param("img2video", "i2v", 7.5, "first.png", None, id="image-to-video"),
    pytest.param("first_last", "flf", 3, "first.png", "last.png", id="first-to-last"),
]


@pytest.fixture(scope="module")
def h3_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    return next(p for p in loader.presets if p.id == "01KX47H3MINIMAX000000000VA")


@pytest.fixture
def storage(tmp_path):
    for name in ("first.png", "last.png"):
        (tmp_path / name).write_bytes(b"")
    settings = Mock()
    settings.get_file_storage_directory.return_value = str(tmp_path)
    return tmp_path, settings


def editor_document(composition, duration, first, last):
    media = []
    if first:
        media.append({"id": "m-1", "role": "first", "segment_id": "seg-1", "at": 0, "strength": 1, "media": first})
    if last:
        media.append({"id": "m-2", "role": "last", "segment_id": "seg-1", "at": duration, "strength": 1, "media": last})
    return {
        "schema_version": 1,
        "mode": composition,
        "settings": {"fps": 24, "duration": duration, "seed": -1},
        "segments": [{
            "id": "seg-1", "prompt": PROMPT, "negative_prompt": "", "start": 0, "end": duration,
            "frames": None, "seed": None, "steps": None, "cfg": None, "loras": None,
        }],
        "media": media,
        "audio": [],
        "ic_lora": [],
    }


def prepare(template, mode, raw_form, settings):
    bound = bind_form(template, mode, form_name=None, raw_form_data=raw_form, user_id=None, storage_dir=None)
    return _prepare_director_form_data(
        template, mode, dict(bound.values), None, settings, prompt_pair=(PROMPT, ""),
    )


def render(template, mode, form_data):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()), settings=Mock(), preset_template_loader=Mock(),
    )
    return processor.process(template, {
        "prompts": [{"positive": PROMPT, "negative": ""}], "mode": mode, "form_data": form_data,
    })


def canonical(value):
    return json.dumps(value, sort_keys=True, default=str)


def run_pair(template, storage, mode, composition, duration, first, last):
    root, settings = storage
    first_ref = {"path": str(root / first), "type": "image"} if first else None
    last_ref = {"path": str(root / last), "type": "image"} if last else None
    common = {**MODEL_FIELDS, "resolution": "832x480", "seed": SEED, "speed_profile": "turbo"}
    director_form = {**common, "video_director": editor_document(composition, duration, first_ref, last_ref)}
    simple_form = {**common, "duration": duration}
    if first:
        simple_form["start_image"] = first_ref["path"]
    if last:
        simple_form["end_image"] = last_ref["path"]
    director = prepare(template, "video", director_form, settings)
    simple = prepare(template, mode, simple_form, settings)
    return director, simple


@pytest.mark.parametrize("mode,composition,duration,first,last", CASES)
def test_the_adapter_builds_the_document_the_director_editor_sends(h3_template, storage, mode, composition, duration, first, last):
    director, simple = run_pair(h3_template, storage, mode, composition, duration, first, last)
    assert canonical(simple["video_director"]) == canonical(director["video_director"])


@pytest.mark.parametrize("mode,composition,duration,first,last", CASES)
def test_the_generator_receives_byte_identical_inputs_from_both_paths(h3_template, storage, mode, composition, duration, first, last):
    director, simple = run_pair(h3_template, storage, mode, composition, duration, first, last)
    director_pipes = render(h3_template, "video", director)
    simple_pipes = render(h3_template, mode, simple)

    def node(pipes, name, pipe_id):
        return next(p for p in pipes if p["name"] == name and p.get("id") == pipe_id)

    assert canonical(node(simple_pipes, "generator/video_minimax_h3", "generator_stage1")) == canonical(
        node(director_pipes, "generator/video_minimax_h3", "generator_stage1")
    )
    assert canonical(node(simple_pipes, "prompt_encoder", None)) == canonical(
        node(director_pipes, "prompt_encoder", None)
    )
    assert canonical(node(simple_pipes, "media_loader", "keyframes")) == canonical(
        node(director_pipes, "media_loader", "keyframes")
    )
    assert canonical(node(simple_pipes, "param_emitter", "param_emitter")) == canonical(
        node(director_pipes, "param_emitter", "param_emitter")
    )
    assert canonical(simple_pipes) == canonical(director_pipes)


@pytest.mark.parametrize("mode,composition,duration,first,last", CASES)
def test_the_generator_gets_the_clip_length_and_the_anchors(h3_template, storage, mode, composition, duration, first, last):
    _, simple = run_pair(h3_template, storage, mode, composition, duration, first, last)
    generator = next(
        p for p in render(h3_template, mode, simple) if p["name"] == "generator/video_minimax_h3" and p.get("id") == "generator_stage1"
    )
    assert generator["config"]["frames"] == int(duration * 24)
    assert generator["config"]["keyframe_anchors"] == [r for r, ref in (("first", first), ("last", last)) if ref]
    assert generator["config"]["document"]["mode"] == composition


@pytest.mark.parametrize("mode,missing", [
    ("img2video", {"start_image"}),
    ("first_last", {"end_image"}),
    ("first_last", {"start_image", "end_image"}),
])
def test_a_missing_picture_is_refused_by_name(h3_template, storage, mode, missing):
    root, settings = storage
    form = {**MODEL_FIELDS, "resolution": "832x480", "seed": SEED, "duration": 5}
    pictures = {"start_image": str(root / "first.png"), "end_image": str(root / "last.png")}
    form.update({name: path for name, path in pictures.items() if name not in missing})
    needed = {"img2video": {"start_image"}, "first_last": {"start_image", "end_image"}}[mode]
    form = {k: v for k, v in form.items() if k not in needed or k not in missing}
    with pytest.raises(Exception) as caught:
        prepare(h3_template, mode, form, settings)
    text = str(caught.value)
    assert all(name in text for name in missing)


def test_a_smuggled_director_document_is_replaced_by_the_one_the_mode_builds(h3_template, storage):
    root, settings = storage
    form = {
        **MODEL_FIELDS, "resolution": "832x480", "seed": SEED, "duration": 5,
        "video_director": editor_document("director", 5, None, None),
    }
    prepared = prepare(h3_template, "txt2video", form, settings)
    assert prepared["video_director"]["mode"] == "t2v"


def test_a_picture_in_the_wrong_shape_for_the_mode_is_rejected_by_the_capability_overlay(h3_template, storage):
    root, settings = storage
    from src.features.video_director import normalize_video_director
    from src.platform.util.path_resolution import apply_preset_mode_overlay

    capabilities = apply_preset_mode_overlay((h3_template.vars or {})["video_director"], "txt2video")
    assert set(capabilities["modes"]) == {"t2v"}
    wrong = editor_document("i2v", 5, {"path": str(root / "first.png"), "type": "image"}, None)
    with pytest.raises(VideoDirectorValidationError):
        normalize_video_director(wrong, capabilities, str(root))


def test_the_simple_modes_run_the_video_modes_pipeline_file(h3_template):
    base = resolve_mode_pipeline_file(__import__("pathlib").Path(h3_template.path) / "modes" / "video")
    for mode in ("txt2video", "img2video", "first_last"):
        assert resolve_mode_pipeline_file(__import__("pathlib").Path(h3_template.path) / "modes" / mode) == base


def test_the_picker_order_and_the_removed_mode(h3_template):
    assert list(h3_template.modes)[:6] == ["txt2video", "img2video", "first_last", "video", "refs", "upscale"]
    assert "long" not in h3_template.modes
    assert "loop" in h3_template.modes


def chain_document(shots):
    return {
        "schema_version": 1,
        "mode": "director",
        "settings": {"fps": 24, "seed": 1, "continuation": {"source": "tail_frames", "overlap_frames": 17, "stitch": True}},
        "segments": [{"id": f"seg-{i}", "prompt": f"shot {i}", "frames": 124} for i in range(shots)],
        "media": [],
        "audio": [],
    }


def test_the_director_admits_every_setup_long_video_could_make(h3_template, tmp_path):
    from src.features.video_director import normalize_video_director
    from src.platform.util.path_resolution import apply_preset_mode_overlay

    capabilities = apply_preset_mode_overlay((h3_template.vars or {})["video_director"], "video")
    assert capabilities["modes"]["director"]["max_segments"] >= 27
    document = normalize_video_director(chain_document(27), capabilities, str(tmp_path))
    assert len(document["segments"]) == 27
    with pytest.raises(VideoDirectorValidationError, match="at most 30 segments"):
        normalize_video_director(chain_document(31), capabilities, str(tmp_path))


def test_the_references_mode_keeps_its_six_shot_ceiling(h3_template, tmp_path):
    from src.features.video_director import normalize_video_director
    from src.platform.util.path_resolution import apply_preset_mode_overlay

    capabilities = apply_preset_mode_overlay((h3_template.vars or {})["video_director"], "refs")
    assert capabilities["modes"]["director"]["max_segments"] == 6
    with pytest.raises(VideoDirectorValidationError, match="at most 6 segments"):
        normalize_video_director(chain_document(7), capabilities, str(tmp_path))


def test_the_colour_hold_is_a_video_director_advanced_field_and_not_a_simple_mode_one(h3_template):
    from src.features.forms.binding import form_field_index

    assert "drift_correction" in form_field_index(h3_template, "video")
    for mode in ("txt2video", "img2video", "first_last"):
        assert "drift_correction" not in form_field_index(h3_template, mode)
    assert form_field_index(h3_template, "video")["drift_correction"].default == 0.0


def test_the_picker_names_the_modes_the_way_the_maintainer_asked(h3_template):
    from pathlib import Path

    from src.features.presets.loader import read_mode_naming
    from src.features.presets.mode_labels import mode_display_name

    root = Path(h3_template.path) / "modes"
    labels = {
        mode: mode_display_name(mode, read_mode_naming(root / mode)["mode_label"])
        for mode in ("txt2video", "img2video", "first_last", "video")
    }
    assert labels == {
        "txt2video": "Text to Video",
        "img2video": "Image to Video",
        "first_last": "First to Last Frame",
        "video": "Video Director",
    }
    description = (h3_template.modes["video"].forms[0].short_description or "").lower()
    assert "shot by shot" in description


def test_the_four_video_modes_show_four_different_icons(h3_template):
    from pathlib import Path

    from src.features.presets.loader import read_mode_naming
    from src.features.presets.mode_labels import mode_display_icon

    root = Path(h3_template.path) / "modes"
    icons = {
        mode: mode_display_icon(mode, read_mode_naming(root / mode)["mode_icon"])
        for mode in ("txt2video", "img2video", "first_last", "video")
    }
    assert None not in icons.values()
    assert len(set(icons.values())) == 4
