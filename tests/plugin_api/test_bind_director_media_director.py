from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.features.generation.dto import GenerationRequest
from src.features.video_director.normalize import normalize_video_director
from src.plugin_api.presets import DirectorBindingError, bind_director_media

LIMITS = {"default_duration": 10, "default_fps": 24, "max_duration": 30}

TIMELINE = {"modes": {"t2v": {}, "i2v": {}, "flf": {}, "director": {"max_keyframes": 2}}, "limits": LIMITS}

CHAIN = {
    "segment_routing": True,
    "modes": {"t2v": {}, "i2v": {}, "flf": {}, "director": {"keyframes": "anywhere", "max_frames_per_segment": 345}},
    "limits": LIMITS,
}

FIRST_ONLY = {"segment_routing": True, "modes": {"director": {"keyframes": "first_only"}}, "limits": LIMITS}

IMAGE = {"value": "uploads/new.png", "media_type": "image", "original_filename": "new.png", "origin": None}
OTHER = {**IMAGE, "value": "uploads/other.png"}
CLIP = {"value": "uploads/clip.mp4", "media_type": "video", "original_filename": "clip.mp4", "origin": None}


def timeline_doc():
    return {
        "schema_version": 1,
        "mode": "director",
        "settings": {"fps": 24, "duration": 10.0, "resolution": "", "seed": 3},
        "segments": [
            {"id": "s1", "prompt": "a", "negative_prompt": "", "start": 0.0, "end": 4.0},
            {"id": "s2", "prompt": "b", "negative_prompt": "", "start": 4.0, "end": 10.0},
        ],
        "media": [],
        "audio": [],
        "ic_lora": [],
    }


def chain_doc(**second):
    return {
        "schema_version": 1,
        "mode": "director",
        "settings": {"fps": 24, "resolution": "", "seed": 3},
        "segments": [
            {"id": "seg-0", "prompt": "a", "negative_prompt": "", "frames": 48},
            {"id": "seg-1", "prompt": "b", "negative_prompt": "", "frames": 72, **second},
        ],
        "media": [],
        "audio": [],
        "ic_lora": [],
    }


def single_doc():
    return {"schema_version": 1, "mode": "t2v", "segments": [{"id": "s1", "prompt": "a"}], "media": []}


def request_with(doc, mode="video", **form):
    return GenerationRequest(preset_id="preset-1", mode=mode, form_data={**form, "video_director": doc})


def bind(request, role, media, segment_id=None, caps=TIMELINE, **options):
    return bind_director_media(request, role, media, segment_id, capabilities=caps, **options)


def with_preset(template):
    container = SimpleNamespace(file_preset_repository=SimpleNamespace(find_preset_by_id=lambda preset_id: template))
    return patch("src.plugin_api.presets.get_container", return_value=container)


@pytest.fixture
def storage(tmp_path):
    (tmp_path / "uploads").mkdir()
    for name in ("new.png", "other.png", "clip.mp4"):
        (tmp_path / "uploads" / name).write_bytes(b"media")
    return tmp_path


def test_a_keyframe_lands_at_its_segment_offset_and_passes_the_normalizer(storage):
    bound = bind(request_with(timeline_doc()), "keyframe", IMAGE, "s2", at=1.5)

    doc = bound.form_data["video_director"]
    assert doc["media"] == [{
        "id": "bound-keyframe-s2-5500",
        "role": "keyframe",
        "segment_id": None,
        "at": 5.5,
        "strength": 1.0,
        "media": {"path": "uploads/new.png", "relative_path": "uploads/new.png", "type": "image"},
    }]
    normalized = normalize_video_director(doc, TIMELINE, str(storage))
    assert [(entry["role"], entry["at"]) for entry in normalized["media"]] == [("keyframe", 5.5)]


def test_a_keyframe_at_the_very_end_of_its_segment_is_kept(storage):
    bound = bind(request_with(timeline_doc()), "keyframe", IMAGE, "s1", at=4.0 + 5e-7)

    assert bound.form_data["video_director"]["media"][0]["at"] == 4.0


def test_first_and_last_frames_anchor_to_the_segment_edges(storage):
    request = bind(request_with(timeline_doc()), "first", IMAGE, "s2")
    bound = bind(request, "last", OTHER, "s2")

    doc = bound.form_data["video_director"]
    assert [(entry["role"], entry["segment_id"], entry["at"]) for entry in doc["media"]] == [
        ("first", "s2", 4.0),
        ("last", "s2", 10.0),
    ]
    normalized = normalize_video_director(doc, TIMELINE, str(storage))
    assert sorted(entry["role"] for entry in normalized["media"]) == ["first", "last"]


def test_binding_the_same_place_again_replaces_the_media():
    request = bind(request_with(timeline_doc()), "keyframe", IMAGE, "s1", at=2)
    request = bind(request, "keyframe", OTHER, "s1", at=2.0)
    request = bind(request, "first", IMAGE, "s1")
    bound = bind(request, "first", OTHER, "s1")

    media = bound.form_data["video_director"]["media"]
    assert len(media) == 2
    assert {entry["role"]: entry["media"]["path"] for entry in media} == {
        "keyframe": "uploads/other.png",
        "first": "uploads/other.png",
    }


def test_the_keyframe_cap_of_the_director_mode_is_kept():
    request = bind(request_with(timeline_doc()), "keyframe", IMAGE, "s1", at=0)
    request = bind(request, "keyframe", IMAGE, "s1", at=1)

    with pytest.raises(DirectorBindingError) as refused:
        bind(request, "keyframe", IMAGE, "s2", at=1)

    assert refused.value.code == "too_many_keyframes"


def test_a_chain_keyframe_is_placed_on_the_chain_timeline(storage):
    bound = bind(request_with(chain_doc()), "keyframe", IMAGE, "seg-1", caps=CHAIN, at=1.0)

    doc = bound.form_data["video_director"]
    assert doc["media"][0]["at"] == 3.0
    normalized = normalize_video_director(doc, CHAIN, str(storage))
    assert normalized["media"][0]["at"] == 3.0


def test_a_chain_keyframe_without_a_frame_rate_uses_the_presets_default(storage):
    doc = chain_doc()
    del doc["settings"]["fps"]

    bound = bind(request_with(doc), "keyframe", IMAGE, "seg-1", caps={**CHAIN, "limits": {**LIMITS, "default_fps": 16}}, at=0)

    assert bound.form_data["video_director"]["media"][0]["at"] == 3.0


def test_a_chain_last_frame_needs_the_first_frame_of_its_segment(storage):
    with pytest.raises(DirectorBindingError) as refused:
        bind(request_with(chain_doc()), "last", OTHER, "seg-1", caps=CHAIN)
    assert refused.value.code == "first_frame_required"

    request = bind(request_with(chain_doc()), "first", IMAGE, "seg-1", caps=CHAIN)
    bound = bind(request, "last", OTHER, "seg-1", caps=CHAIN)

    normalized = normalize_video_director(bound.form_data["video_director"], CHAIN, str(storage))
    assert [segment["sub_type"] for segment in normalized["segments"]] == ["t2v", "flf"]


@pytest.mark.parametrize(
    "doc, role, segment_id, caps, options, code",
    [
        (timeline_doc(), "first", None, TIMELINE, {}, "segment_required"),
        (timeline_doc(), "first", "s9", TIMELINE, {}, "unknown_segment"),
        (timeline_doc(), "keyframe", "s1", TIMELINE, {}, "invalid_at"),
        (timeline_doc(), "keyframe", "s1", TIMELINE, {"at": -1}, "invalid_at"),
        (timeline_doc(), "keyframe", "s1", TIMELINE, {"at": 4.5}, "invalid_at"),
        (timeline_doc(), "keyframe", "s1", TIMELINE, {"at": True}, "invalid_at"),
        (timeline_doc(), "keyframe", "s1", TIMELINE, {"at": float("nan")}, "invalid_at"),
        (timeline_doc(), "first", "s1", TIMELINE, {"at": 1}, "invalid_at"),
        (timeline_doc(), "middle", "s1", TIMELINE, {}, "invalid_role"),
        (timeline_doc(), "first", "s1", {"modes": {"t2v": {}}}, {}, "unsupported_mode"),
        (timeline_doc(), "first", "s1", {}, {}, "unsupported_mode"),
        (chain_doc(), "keyframe", "seg-0", FIRST_ONLY, {"at": 0}, "role_unsupported"),
        (chain_doc(), "last", "seg-0", FIRST_ONLY, {}, "role_unsupported"),
        (chain_doc(), "keyframe", "seg-1", {**CHAIN, "modes": {"director": {}}}, {"at": 0}, "role_unsupported"),
        (chain_doc(sub_type="chain"), "first", "seg-1", CHAIN, {}, "role_unsupported"),
        (chain_doc(frames=None), "keyframe", "seg-1", CHAIN, {"at": 0}, "invalid_at"),
        (timeline_doc(), "first", "s1", TIMELINE, {"media": CLIP}, "invalid_media"),
    ],
)
def test_director_bindings_the_preset_cannot_take_are_refused(doc, role, segment_id, caps, options, code):
    media = options.pop("media", IMAGE)
    with pytest.raises(DirectorBindingError) as refused:
        bind(request_with(doc), role, media, segment_id, caps=caps, **options)

    assert refused.value.code == code


def _untimed(doc, segment=0, settings=None, **fields):
    doc["segments"][segment].update(fields)
    doc["settings"].update(settings or {})
    return doc


def _with_first(doc, segment_id):
    doc["media"].append({"id": "f", "role": "first", "segment_id": segment_id, "at": 0.0, "strength": 1.0, "media": {}})
    return doc


@pytest.mark.parametrize(
    "doc, role, segment_id, caps",
    [
        (_untimed(chain_doc(), frames=None), "first", "seg-1", CHAIN),
        (_untimed(chain_doc(), frames=None), "keyframe", "seg-1", CHAIN),
        (_untimed(chain_doc(), frames=0), "first", "seg-1", FIRST_ONLY),
        (_untimed(chain_doc(), segment=1, frames=None), "first", "seg-1", CHAIN),
        (_with_first(_untimed(chain_doc(), segment=1, frames=None), "seg-1"), "last", "seg-1", CHAIN),
        (_with_first(_untimed(chain_doc(), frames=None), "seg-1"), "first", "seg-1", CHAIN),
        (_untimed(chain_doc(), settings={"fps": -24}), "first", "seg-0", CHAIN),
        (_untimed(chain_doc(), settings={"fps": "24"}), "first", "seg-0", CHAIN),
        (_untimed(timeline_doc(), end=None), "first", "s1", TIMELINE),
        (_untimed(timeline_doc(), start=4.0), "last", "s1", TIMELINE),
    ],
)
def test_a_first_or_last_frame_whose_segment_has_no_known_window_is_refused_like_a_keyframe(doc, role, segment_id, caps):
    options = {"at": 0} if role == "keyframe" else {}
    with pytest.raises(DirectorBindingError) as refused:
        bind(request_with(doc), role, IMAGE, segment_id, caps=caps, **options)

    assert refused.value.code == "invalid_at"
    assert "no timing" in str(refused.value)


def test_a_first_only_chain_still_takes_a_first_frame():
    bound = bind(request_with(chain_doc()), "first", IMAGE, "seg-1", caps=FIRST_ONLY)

    assert bound.form_data["video_director"]["media"][0]["segment_id"] == "seg-1"


def test_a_plain_request_dict_is_bound_and_returned_as_a_dict():
    request = request_with(timeline_doc()).model_dump()

    bound = bind(request, "first", IMAGE, "s1")

    assert isinstance(bound, dict)
    assert bound["form_data"]["video_director"]["media"][0]["role"] == "first"
    assert request["form_data"]["video_director"]["media"] == []


def test_the_capabilities_default_to_the_presets_own_for_the_request_mode():
    template = SimpleNamespace(vars={"video_director": {**TIMELINE, "preset_mode_overrides": {"refs": FIRST_ONLY}}})

    with with_preset(template):
        bound = bind_director_media(request_with(timeline_doc()), "keyframe", IMAGE, "s1", at=1)
        with pytest.raises(DirectorBindingError) as refused:
            bind_director_media(request_with(chain_doc(), mode="refs"), "keyframe", IMAGE, "seg-1", at=0)

    assert bound.form_data["video_director"]["media"][0]["at"] == 1.0
    assert refused.value.code == "role_unsupported"


def test_capabilities_given_by_the_caller_win_over_the_presets():
    template = SimpleNamespace(vars={"video_director": FIRST_ONLY})

    with with_preset(template):
        bound = bind_director_media(request_with(chain_doc()), "keyframe", IMAGE, "seg-1", at=0, capabilities=CHAIN)

    assert bound.form_data["video_director"]["media"][0]["at"] == 2.0


@pytest.mark.parametrize("template", [None, SimpleNamespace(vars={}), SimpleNamespace(vars=None)])
@pytest.mark.parametrize("role, options", [("first", {}), ("keyframe", {"at": 0})])
def test_a_director_document_of_a_preset_without_director_capabilities_is_refused(template, role, options):
    with with_preset(template):
        with pytest.raises(DirectorBindingError) as refused:
            bind_director_media(request_with(timeline_doc()), role, IMAGE, "s1", **options)

    assert refused.value.code == "unsupported_mode"


def test_a_single_shot_document_binds_without_reading_any_preset():
    with patch("src.plugin_api.presets.get_container", side_effect=AssertionError("no preset read")):
        bound = bind_director_media(request_with(single_doc()), "first", IMAGE, promote=True)

    assert bound.form_data["video_director"]["mode"] == "i2v"


@pytest.mark.parametrize(
    "doc, role, segment_id, options",
    [
        (timeline_doc(), "first", "s1", {}),
        (timeline_doc(), "keyframe", "s1", {"at": 1}),
        (chain_doc(), "reference", "seg-0", {}),
        (single_doc(), "reference", None, {}),
    ],
)
def test_without_the_application_a_binding_that_reads_the_preset_lets_the_container_error_through(
    doc, role, segment_id, options,
):
    with patch("src.plugin_api.presets.get_container", side_effect=RuntimeError("no container")):
        with pytest.raises(RuntimeError, match="no container"):
            bind_director_media(request_with(doc), role, IMAGE, segment_id, **options)


def test_without_the_application_given_capabilities_bind_without_reading_any_preset():
    with patch("src.plugin_api.presets.get_container", side_effect=RuntimeError("no container")):
        bound = bind_director_media(request_with(timeline_doc()), "first", IMAGE, "s1", capabilities=TIMELINE)
        pooled = bind_director_media(request_with(chain_doc()), "reference", IMAGE, capabilities=REFS)

    assert bound.form_data["video_director"]["media"][0]["role"] == "first"
    assert pooled.form_data["references"][0]["relative_path"] == "uploads/new.png"


REFS = {
    "segment_routing": True,
    "references": "per_shot",
    "reference_fields": ["references", "extra"],
    "modes": {"t2v": {}, "i2v": {}, "flf": {}, "director": {"keyframes": None, "continuation": None}},
    "limits": LIMITS,
}

REFS_PROMPT_RESOURCES = [
    {"field": "references", "kind": "image", "token": "<Picture @>"},
    {"field": "references", "kind": "video", "token": "<Video @>"},
    {"field": "references", "kind": "audio", "token": "<Audio @>"},
]


def test_references_join_the_pool_once_and_are_cited_by_the_segment(storage):
    pool = [{"relative_path": "uploads/old.png", "label": "Old"}]
    request = request_with(chain_doc(), references=pool)

    request = bind(request, "reference", [IMAGE, CLIP], "seg-1", caps=REFS)
    bound = bind(request, "reference", IMAGE, "seg-1", caps=REFS)

    form = bound.form_data
    assert [item.get("relative_path") for item in form["references"]] == [
        "uploads/old.png", "uploads/new.png", "uploads/clip.mp4",
    ]
    assert form["references"][1] == {
        "path": "uploads/new.png", "relative_path": "uploads/new.png", "type": "image", "name": "new.png",
    }
    prompt = form["video_director"]["segments"][1]["prompt"]
    assert prompt == "b @[references:uploads/new.png] @[references:uploads/clip.mp4]"
    assert form["video_director"]["segments"][0]["prompt"] == "a"
    normalized = normalize_video_director(form["video_director"], REFS, str(storage), form, REFS_PROMPT_RESOURCES)
    assert normalized["segments"][1]["reference_indices"] == [1, 2]
    assert normalized["segments"][1]["prompt"] == "b <Picture 1> <Video 1>"


def test_a_reference_already_in_the_pool_is_only_cited():
    request = request_with(chain_doc(), references="uploads/new.png")

    bound = bind(request, "reference", IMAGE, "seg-0", caps=REFS)

    assert bound.form_data["references"] == ["uploads/new.png"]
    assert bound.form_data["video_director"]["segments"][0]["prompt"] == "a @[references:uploads/new.png]"


def test_references_without_a_segment_only_fill_the_pool_of_the_named_field():
    request = request_with(chain_doc())

    bound = bind(request, "reference", CLIP, caps=REFS, field="extra")

    assert bound.form_data["extra"] == [{"path": "uploads/clip.mp4", "relative_path": "uploads/clip.mp4", "type": "video", "name": "clip.mp4"}]
    assert "references" not in bound.form_data
    assert bound.form_data["video_director"] == request.form_data["video_director"]


def test_a_single_shot_document_takes_references_into_its_pool():
    bound = bind(request_with(single_doc()), "reference", IMAGE, caps={**REFS, "references": "whole"})

    assert bound.form_data["references"][0]["relative_path"] == "uploads/new.png"


@pytest.mark.parametrize(
    "media, segment_id, caps, options, code",
    [
        (IMAGE, None, TIMELINE, {}, "references_unsupported"),
        (IMAGE, None, {**REFS, "reference_fields": []}, {}, "references_unsupported"),
        (IMAGE, None, {}, {}, "references_unsupported"),
        (IMAGE, None, REFS, {"field": "pictures"}, "unknown_field"),
        ({**IMAGE, "media_type": "model"}, None, REFS, {}, "invalid_media"),
        ({**IMAGE, "value": ""}, None, REFS, {}, "invalid_media"),
        ([], None, REFS, {}, "invalid_media"),
        ({**IMAGE, "value": "uploads/a]b.png"}, "seg-1", REFS, {}, "invalid_media"),
        (IMAGE, "seg-1", {**REFS, "references": "whole"}, {}, "unsupported_mode"),
        (IMAGE, "seg-9", REFS, {}, "unknown_segment"),
        (IMAGE, None, REFS, {"at": 1}, "invalid_at"),
    ],
)
def test_references_the_preset_cannot_take_are_refused(media, segment_id, caps, options, code):
    with patch("src.plugin_api.presets.get_container", side_effect=RuntimeError("no container")):
        with pytest.raises(DirectorBindingError) as refused:
            bind_director_media(request_with(chain_doc()), "reference", media, segment_id, capabilities=caps, **options)

    assert refused.value.code == code


def test_a_reference_is_cited_per_segment_only_in_a_director_document():
    with pytest.raises(DirectorBindingError) as refused:
        bind(request_with(single_doc()), "reference", IMAGE, "s1", caps=REFS)

    assert refused.value.code == "unsupported_mode"


def test_the_reference_pool_defaults_to_the_presets_own_for_the_request_mode():
    template = SimpleNamespace(vars={"video_director": {**TIMELINE, "preset_mode_overrides": {"refs": REFS}}})
    request = request_with(chain_doc(), mode="refs")

    with with_preset(template):
        bound = bind_director_media(request, "reference", IMAGE, "seg-0")
        with pytest.raises(DirectorBindingError) as refused:
            bind_director_media(request_with(single_doc()), "reference", IMAGE)

    assert bound.form_data["references"][0]["relative_path"] == "uploads/new.png"
    assert refused.value.code == "references_unsupported"


def test_a_single_shot_document_takes_references_into_the_pool_its_preset_declares():
    template = SimpleNamespace(vars={"video_director": {**REFS, "references": "whole"}})

    with with_preset(template):
        bound = bind_director_media(request_with(single_doc()), "reference", CLIP)

    assert bound.form_data["references"][0]["relative_path"] == "uploads/clip.mp4"
