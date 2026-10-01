import pytest

from src.features.generation.dto import GenerationRequest
from src.features.video_director.binding import DirectorBindingError, bind_director_media
from src.features.video_director.normalize import normalize_video_director
import src.plugin_api as plugin_api
from src.plugin_api import presets as presets_api

CAPS = {
    "preset_modes": ["video"],
    "modes": {"t2v": {}, "i2v": {}, "flf": {}, "director": {"max_keyframes": 8}},
    "limits": {"default_duration": 5, "default_fps": 24, "max_duration": 30},
}

IMAGE = {"value": "uploads/new.png", "media_type": "image", "original_filename": "new.png", "origin": None}


def document(mode, media=None, segments=None):
    return {
        "schema_version": 1,
        "mode": mode,
        "settings": {"fps": 24, "duration": 5.0, "resolution": "", "seed": -1},
        "segments": segments or [{"id": "shot-1", "prompt": "orbit left", "negative_prompt": ""}],
        "media": media or [],
        "audio": [],
        "ic_lora": [],
    }


def request_with(doc, **form):
    return GenerationRequest(preset_id="preset-1", mode="video", form_data={"steps": 8, **form, "video_director": doc})


def frame(role, path, strength=0.8):
    return {"id": f"saved-{role}", "role": role, "segment_id": "shot-1", "at": 0, "strength": strength, "media": {"path": path, "type": "image"}}


@pytest.fixture
def storage(tmp_path):
    (tmp_path / "uploads").mkdir()
    for name in ("new.png", "saved-first.png", "saved-last.png"):
        (tmp_path / "uploads" / name).write_bytes(b"image")
    return tmp_path


def test_a_first_frame_replaces_the_saved_one_and_keeps_its_strength():
    doc = document("i2v", media=[frame("first", "uploads/saved-first.png")])

    bound = bind_director_media(request_with(doc), "first", IMAGE)

    media = bound.form_data["video_director"]["media"]
    assert len(media) == 1
    assert media[0]["id"] == "saved-first"
    assert media[0]["strength"] == 0.8
    assert media[0]["media"] == {"path": "uploads/new.png", "relative_path": "uploads/new.png", "type": "image"}
    assert bound.form_data["steps"] == 8


def test_binding_the_first_frame_of_an_flf_shot_keeps_the_saved_last_frame():
    doc = document("flf", media=[frame("first", "uploads/saved-first.png"), frame("last", "uploads/saved-last.png", 1.0)])

    bound = bind_director_media(request_with(doc), "first", IMAGE)

    by_role = {entry["role"]: entry for entry in bound.form_data["video_director"]["media"]}
    assert by_role["first"]["media"]["path"] == "uploads/new.png"
    assert by_role["last"]["media"]["path"] == "uploads/saved-last.png"


def test_a_missing_frame_is_added_with_default_strength():
    doc = document("i2v")

    bound = bind_director_media(request_with(doc), "first", IMAGE)

    assert bound.form_data["video_director"]["media"] == [
        {
            "id": "bound-first-shot-1",
            "role": "first",
            "segment_id": "shot-1",
            "at": 0,
            "strength": 1.0,
            "media": {"path": "uploads/new.png", "relative_path": "uploads/new.png", "type": "image"},
        }
    ]


def test_the_bound_document_passes_the_server_normalizer(storage):
    doc = document("flf", media=[frame("first", "uploads/saved-first.png"), frame("last", "uploads/saved-last.png")])

    bound = bind_director_media(request_with(doc), "first", IMAGE)
    normalized = normalize_video_director(bound.form_data["video_director"], CAPS, str(storage))

    by_role = {entry["role"]: entry for entry in normalized["media"]}
    assert by_role["first"]["media"]["path"] == str(storage / "uploads" / "new.png")
    assert by_role["last"]["media"]["path"] == str(storage / "uploads" / "saved-last.png")


def test_the_original_request_is_not_changed():
    doc = document("i2v", media=[frame("first", "uploads/saved-first.png")])
    request = request_with(doc)

    bind_director_media(request, "first", IMAGE)

    assert request.form_data["video_director"]["media"][0]["media"]["path"] == "uploads/saved-first.png"


def test_a_plain_dict_request_comes_back_as_a_dict():
    raw = {"preset_id": "preset-1", "mode": "video", "form_data": {"video_director": document("i2v")}}

    bound = bind_director_media(raw, "first", IMAGE)

    assert isinstance(bound, dict)
    assert bound["form_data"]["video_director"]["media"][0]["role"] == "first"
    assert raw["form_data"]["video_director"]["media"] == []


def test_a_dict_request_with_a_saved_frame_is_not_changed():
    raw = {"preset_id": "preset-1", "form_data": {"video_director": document("i2v", media=[frame("first", "uploads/saved-first.png")])}}

    bind_director_media(raw, "first", IMAGE)

    assert raw["form_data"]["video_director"]["media"][0]["media"]["path"] == "uploads/saved-first.png"


def test_a_named_segment_is_used():
    doc = document("i2v", segments=[{"id": "shot-a", "prompt": "a"}])

    bound = bind_director_media(request_with(doc), "first", IMAGE, segment_id="shot-a")

    assert bound.form_data["video_director"]["media"][0]["segment_id"] == "shot-a"


@pytest.mark.parametrize(
    "doc, role, media, segment_id, code",
    [
        (document("t2v"), "first", IMAGE, None, "unsupported_mode"),
        (document("i2v"), "last", IMAGE, None, "unsupported_mode"),
        (document("director"), "first", IMAGE, None, "unsupported_mode"),
        (document("i2v"), "keyframe", IMAGE, None, "invalid_role"),
        (document("i2v", segments=[{"id": "a"}, {"id": "b"}]), "first", IMAGE, None, "segment_required"),
        (document("i2v"), "first", IMAGE, "shot-9", "unknown_segment"),
        (document("i2v"), "first", {**IMAGE, "media_type": "video"}, None, "invalid_media"),
        (document("i2v"), "first", {**IMAGE, "value": ""}, None, "invalid_media"),
    ],
)
def test_unsupported_bindings_are_refused_with_a_code(doc, role, media, segment_id, code):
    with pytest.raises(DirectorBindingError) as refused:
        bind_director_media(request_with(doc), role, media, segment_id=segment_id)

    assert refused.value.code == code


def test_a_request_without_a_director_document_is_refused():
    with pytest.raises(DirectorBindingError) as refused:
        bind_director_media(GenerationRequest(preset_id="p", form_data={"steps": 8}), "first", IMAGE)

    assert refused.value.code == "no_director"


def test_exported_from_the_plugin_api():
    for name in ("bind_director_media", "DirectorBindingError"):
        assert name in presets_api.__all__ and name in plugin_api.__all__
        assert getattr(plugin_api, name) is getattr(presets_api, name)


def test_a_first_frame_turns_a_text_only_shot_into_a_start_image_shot_when_asked(storage):
    bound = bind_director_media(request_with(document("t2v")), "first", IMAGE, promote=True)

    doc = bound.form_data["video_director"]
    assert doc["mode"] == "i2v"
    normalized = normalize_video_director(doc, CAPS, str(storage))
    assert normalized["mode"] == "i2v"
    assert [entry["role"] for entry in normalized["media"]] == ["first"]


def test_a_last_frame_turns_a_start_image_shot_into_a_first_and_last_shot_when_asked(storage):
    doc = document("i2v", media=[frame("first", "uploads/saved-first.png")])

    bound = bind_director_media(request_with(doc), "last", IMAGE, promote=True)

    promoted = bound.form_data["video_director"]
    assert promoted["mode"] == "flf"
    normalized = normalize_video_director(promoted, CAPS, str(storage))
    by_role = {entry["role"]: entry for entry in normalized["media"]}
    assert by_role["first"]["media"]["path"] == str(storage / "uploads" / "saved-first.png")
    assert by_role["last"]["media"]["path"] == str(storage / "uploads" / "new.png")
    assert by_role["last"]["at"] == 5.0


def test_first_then_last_turn_a_text_only_shot_into_a_first_and_last_shot(storage):
    request = bind_director_media(request_with(document("t2v")), "first", IMAGE, promote=True)
    last = {**IMAGE, "value": "uploads/saved-last.png"}

    bound = bind_director_media(request, "last", last, promote=True)

    doc = bound.form_data["video_director"]
    assert doc["mode"] == "flf"
    normalized = normalize_video_director(doc, CAPS, str(storage))
    assert sorted(entry["role"] for entry in normalized["media"]) == ["first", "last"]


@pytest.mark.parametrize(
    "doc, role, code",
    [
        (document("t2v", segments=[{"id": "a", "prompt": "a"}, {"id": "b", "prompt": "b"}]), "first", "segment_required"),
        (document("director"), "first", "unsupported_mode"),
        (document("t2v"), "last", "unsupported_mode"),
        (document("i2v"), "last", "first_frame_required"),
    ],
)
def test_promoting_still_refuses_what_cannot_become_a_single_shot(doc, role, code):
    with pytest.raises(DirectorBindingError) as refused:
        bind_director_media(request_with(doc), role, IMAGE, promote=True)

    assert refused.value.code == code


def test_without_promote_a_text_only_shot_is_still_refused_and_left_alone():
    request = request_with(document("t2v"))

    with pytest.raises(DirectorBindingError):
        bind_director_media(request, "first", IMAGE)

    assert request.form_data["video_director"]["mode"] == "t2v"
