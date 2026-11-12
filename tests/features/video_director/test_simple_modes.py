from __future__ import annotations

import pytest

from src.features.video_director.normalize import VideoDirectorValidationError
from src.features.video_director.simple_modes import (
    build_simple_mode_document,
    is_director_backed,
    simple_mode_problems,
    simple_mode_spec,
)

CAPABILITIES = {
    "limits": {"default_fps": 24, "max_duration": 15},
    "simple_modes": {
        "txt2video": {"composition": "t2v"},
        "img2video": {"composition": "i2v", "first": "start_image"},
        "first_last": {"composition": "flf", "first": "start_image", "last": "end_image"},
    },
}


def test_a_mode_is_director_backed_when_it_is_video_or_a_simple_mode():
    assert is_director_backed(CAPABILITIES, "video")
    assert is_director_backed(CAPABILITIES, "first_last")
    assert not is_director_backed(CAPABILITIES, "loop")
    assert not is_director_backed(None, "loop")
    assert not is_director_backed(CAPABILITIES, None)


def test_the_spec_lookup_ignores_modes_without_one():
    assert simple_mode_spec(CAPABILITIES, "img2video") == {"composition": "i2v", "first": "start_image"}
    assert simple_mode_spec(CAPABILITIES, "video") is None
    assert simple_mode_spec({}, "img2video") is None


def test_a_text_document_has_one_segment_carrying_the_prompt_and_no_media():
    spec = CAPABILITIES["simple_modes"]["txt2video"]
    document = build_simple_mode_document(spec, CAPABILITIES, {"duration": 6}, "a boat", "blur")
    assert document["mode"] == "t2v"
    assert document["settings"] == {"fps": 24, "duration": 6, "seed": -1}
    assert document["media"] == [] and document["audio"] == [] and document["ic_lora"] == []
    (segment,) = document["segments"]
    assert segment["id"] == "seg-1" and segment["prompt"] == "a boat" and segment["negative_prompt"] == "blur"
    assert (segment["start"], segment["end"]) == (0, 6)


def test_a_path_string_becomes_an_image_reference_and_a_dict_is_passed_through():
    spec = CAPABILITIES["simple_modes"]["img2video"]
    from_string = build_simple_mode_document(spec, CAPABILITIES, {"duration": 5, "start_image": "/s/a.png"}, "x")
    assert from_string["media"] == [
        {"id": "m-1", "role": "first", "segment_id": "seg-1", "at": 0, "strength": 1,
         "media": {"path": "/s/a.png", "type": "image"}}
    ]
    ref = {"path": "/s/a.png", "relative_path": "a.png", "name": "a.png", "type": "image"}
    from_dict = build_simple_mode_document(spec, CAPABILITIES, {"duration": 5, "start_image": ref}, "x")
    assert from_dict["media"][0]["media"] == ref


def test_the_last_picture_sits_at_the_end_of_the_clip():
    spec = CAPABILITIES["simple_modes"]["first_last"]
    document = build_simple_mode_document(
        spec, CAPABILITIES, {"duration": 4.5, "start_image": "/s/a.png", "end_image": "/s/b.png"}, "x",
    )
    assert [(m["id"], m["role"], m["at"]) for m in document["media"]] == [("m-1", "first", 0), ("m-2", "last", 4.5)]


@pytest.mark.parametrize("form,names", [
    ({"duration": 5}, ["start_image", "end_image"]),
    ({"duration": 5, "start_image": "/s/a.png"}, ["end_image"]),
    ({"duration": 5, "start_image": "", "end_image": "/s/b.png"}, ["start_image"]),
    ({"duration": 5, "start_image": {"name": "no path"}, "end_image": "/s/b.png"}, ["start_image"]),
])
def test_every_missing_picture_is_named(form, names):
    with pytest.raises(VideoDirectorValidationError) as caught:
        build_simple_mode_document(CAPABILITIES["simple_modes"]["first_last"], CAPABILITIES, form, "x")
    assert [e.split(":")[0] for e in caught.value.errors] == names


@pytest.mark.parametrize("duration", [None, 0, -3, "5", True])
def test_a_length_that_is_not_a_positive_number_is_refused(duration):
    with pytest.raises(VideoDirectorValidationError, match="duration"):
        build_simple_mode_document(CAPABILITIES["simple_modes"]["txt2video"], CAPABILITIES, {"duration": duration}, "x")


def test_the_frame_rate_comes_from_the_capability_limits():
    capabilities = {**CAPABILITIES, "limits": {"default_fps": 16}}
    document = build_simple_mode_document(CAPABILITIES["simple_modes"]["txt2video"], capabilities, {"duration": 5}, "x")
    assert document["settings"]["fps"] == 16


@pytest.mark.parametrize("table,fragment", [
    ("nope", "mapping"),
    ({"a": "x"}, "must be a mapping"),
    ({"a": {"composition": "director"}}, "composition"),
    ({"a": {"composition": "i2v"}}, "must name the picture field"),
    ({"a": {"composition": "t2v", "first": "start_image"}}, "not used"),
    ({"a": {"composition": "flf", "first": "s"}}, "last must name"),
    ({"a": {"composition": "t2v", "duration_field": 4}}, "duration_field"),
])
def test_a_malformed_table_is_described(table, fragment):
    assert any(fragment in problem for problem in simple_mode_problems(table))


def test_the_shipped_table_is_well_formed():
    assert simple_mode_problems(CAPABILITIES["simple_modes"]) == []


def test_the_request_prompt_pair_is_read_defensively():
    from types import SimpleNamespace

    from src.features.generation.orchestrator import _request_prompt_pair

    pair = SimpleNamespace(positive="a boat", negative="blur")
    assert _request_prompt_pair(SimpleNamespace(prompts=[pair])) == ("a boat", "blur")
    assert _request_prompt_pair(SimpleNamespace(prompts=[])) == ("", "")
    assert _request_prompt_pair(SimpleNamespace(prompts=None)) == ("", "")
    assert _request_prompt_pair(SimpleNamespace()) == ("", "")
    assert _request_prompt_pair(SimpleNamespace(prompts=[SimpleNamespace(positive=3, negative=None)])) == ("", "")
