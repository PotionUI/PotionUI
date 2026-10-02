from pathlib import Path

import pytest
import yaml
from PIL import Image

from src.features.cloud.director import director_overlay
from src.features.cloud.testing.fake import fake_director_specs
from src.features.video_director import (
    VideoDirectorValidationError,
    apply_model_overlay,
    apply_preset_mode_overlay,
    normalize_video_director,
)

PRESET = Path("content/plugins/marketplace/openrouter-provider/presets/VideoGeneration/standard/preset.yml")
FULL, START, TEXT = fake_director_specs()


def preset_block():
    return yaml.safe_load(PRESET.read_text(encoding="utf-8"))["vars"]["video_director"]


def effective(preset_mode, spec):
    return apply_model_overlay(apply_preset_mode_overlay(preset_block(), preset_mode), director_overlay(spec))


@pytest.fixture
def storage(tmp_path):
    root = tmp_path / "storage"
    root.mkdir()
    for name in ("start.png", "end.png"):
        Image.new("RGB", (8, 8)).save(root / name)
    return root


def picture(role, segment_id, name):
    return {"id": f"{role}-{segment_id}", "role": role, "segment_id": segment_id, "media": {"path": name, "type": "image"}}


def single(mode, duration, media=()):
    return {
        "schema_version": 1, "mode": mode, "settings": {"fps": 24, "duration": duration, "seed": 3},
        "segments": [{"id": "seg-0", "prompt": "a"}], "media": list(media),
    }


def film(*frames, media=(), continuation=None):
    return {
        "schema_version": 1, "mode": "director",
        "settings": {"fps": 24, "seed": 3, "continuation": continuation},
        "segments": [{"id": f"s{i}", "prompt": f"shot {i}", "frames": count} for i, count in enumerate(frames)],
        "media": list(media),
    }


def problems(document, capabilities, storage):
    with pytest.raises(VideoDirectorValidationError) as raised:
        normalize_video_director(document, capabilities, str(storage))
    return raised.value.errors


def test_the_preset_offers_text_shots_and_films_for_text_to_video_and_pictures_for_image_to_video():
    assert set(apply_preset_mode_overlay(preset_block(), "txt2video")["modes"]) == {"t2v", "director"}
    assert set(apply_preset_mode_overlay(preset_block(), "img2video")["modes"]) == {"i2v", "flf", "director"}


def test_a_single_shot_must_use_a_length_the_model_makes(storage):
    caps = effective("txt2video", FULL)

    assert normalize_video_director(single("t2v", 4), caps, str(storage))["settings"]["duration"] == 4
    (problem,) = problems(single("t2v", 5), caps, str(storage))
    assert problem == "settings.duration: 5s is not a length this model makes -- choose 2, 4 or 6 seconds"


def test_every_shot_of_a_film_must_use_a_length_the_model_makes(storage):
    caps = effective("txt2video", FULL)

    normalized = normalize_video_director(film(48, 144), caps, str(storage))
    assert [segment["frames"] for segment in normalized["segments"]] == [48, 144]
    (problem,) = problems(film(48, 60), caps, str(storage))
    assert problem == "segments[1]: 2.5s (60 frames at 24 fps) is not a length this model makes -- choose 2, 4 or 6 seconds"


def test_a_film_is_capped_at_the_shots_the_model_allows(storage):
    caps = effective("txt2video", FULL)

    (problem,) = problems(film(48, 48, 48, 48, 48), caps, str(storage))
    assert "at most 4 segments" in problem


def test_a_later_prompt_only_shot_continues_from_the_shot_before(storage):
    normalized = normalize_video_director(film(48, 48), effective("txt2video", FULL), str(storage))

    assert [segment["sub_type"] for segment in normalized["segments"]] == ["t2v", "chain"]


def test_a_text_only_model_cuts_every_shot_and_takes_no_pictures(storage):
    caps = effective("txt2video", TEXT)

    normalized = normalize_video_director(film(120, 120), caps, str(storage))
    assert [segment["sub_type"] for segment in normalized["segments"]] == ["t2v", "t2v"]
    errors = problems(film(120, media=[picture("first", "s0", "start.png")]), caps, str(storage))
    assert any("first" in error for error in errors)
    errors = problems(film(120, 120, continuation={"source": "last_frame", "overlap_frames": 0, "stitch": True}), caps, str(storage))
    assert any("settings.continuation" in error for error in errors)


def test_a_model_without_an_end_frame_refuses_first_last_frame(storage):
    caps = effective("img2video", START)

    assert set(caps["modes"]) == {"i2v", "director"}
    single_start = single("i2v", 3, [picture("first", "seg-0", "start.png")])
    assert normalize_video_director(single_start, caps, str(storage))["mode"] == "i2v"
    both = single("flf", 3, [picture("first", "seg-0", "start.png"), picture("last", "seg-0", "end.png")])
    (problem,) = problems(both, caps, str(storage))
    assert problem.startswith("unsupported mode 'flf'")


def test_a_model_with_both_frames_accepts_first_last_frame_in_image_to_video(storage):
    both = single("flf", 6, [picture("first", "seg-0", "start.png"), picture("last", "seg-0", "end.png")])

    normalized = normalize_video_director(both, effective("img2video", FULL), str(storage))

    assert [entry["role"] for entry in normalized["media"]] == ["first", "last"]


def test_a_model_overlay_cannot_add_a_shape_the_preset_mode_lacks():
    caps = apply_model_overlay(apply_preset_mode_overlay(preset_block(), "txt2video"), {"modes": {"flf": {}}})

    assert set(caps["modes"]) == {"t2v", "director"}


def test_without_a_lengths_list_any_positive_length_is_fine(storage):
    caps = apply_preset_mode_overlay(preset_block(), "txt2video")

    assert normalize_video_director(single("t2v", 7), caps, str(storage))["settings"]["duration"] == 7
    assert normalize_video_director(film(30, 53), caps, str(storage))["segments"][1]["frames"] == 53


def previous_clip(storage, colour=(0, 0, 230)):
    pytest.importorskip("cv2", reason="the previous shot's clip is written with cv2", exc_type=ImportError)
    from src.features.cloud.testing.fake import fake_video_bytes

    clip = storage / "generations" / "2026-10-02" / "gen-0" / "1_shot.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(fake_video_bytes(2, start=(230, 0, 0), end=colour))
    return "generations/2026-10-02/gen-0/1_shot.mp4"


def clip_start(segment_id, relative_path):
    return {"id": f"first-{segment_id}", "role": "first", "segment_id": segment_id, "media": {"relative_path": relative_path, "type": "video"}}


def test_a_retry_can_start_a_shot_from_the_previous_shots_clip(storage):
    relative = previous_clip(storage)
    document = film(48, 48, 48, media=[clip_start("s1", relative)])

    normalized = normalize_video_director(document, effective("txt2video", FULL), str(storage))

    (start,) = normalized["media"]
    assert start["media"]["type"] == "image" and start["media"]["path"].endswith(".png")
    assert start["media"]["source_video"] == str(storage / relative)
    colour = Image.open(start["media"]["path"]).convert("RGB").getpixel((0, 0))
    assert colour[2] > 180 and colour[0] < 60
    assert [segment["sub_type"] for segment in normalized["segments"]] == ["t2v", "i2v", "chain"]


def test_the_retried_span_compiles_from_the_shot_that_starts_on_the_clip(storage):
    from src.features.video_director import compile_shot_plan

    document = {**film(48, 48, 48, media=[clip_start("s1", previous_clip(storage))]), "render": {"scope": "shots", "shot_ids": ["s1", "s2"]}}
    normalized = normalize_video_director(document, effective("txt2video", FULL), str(storage))

    compiled = compile_shot_plan(normalized, ["s1", "s2"], family="cloud")

    assert [(segment["id"], segment["sub_type"]) for segment in compiled["segments"]] == [("s1", "i2v"), ("s2", "chain")]
    assert [entry["segment_id"] for entry in compiled["media"]] == ["s1"]


def test_a_clip_is_accepted_as_a_start_wherever_the_model_starts_from_a_picture(storage):
    block = preset_block()
    block["modes"]["director"].pop("continue_from_video")
    caps = apply_model_overlay(apply_preset_mode_overlay(block, "txt2video"), director_overlay(START))
    document = film(72, 72, media=[clip_start("s1", previous_clip(storage))])

    normalized = normalize_video_director(document, caps, str(storage))

    assert normalized["media"][0]["media"]["type"] == "image"


def test_a_clip_as_a_start_needs_the_capability(storage):
    relative = previous_clip(storage)
    caps = effective("txt2video", FULL)
    caps["modes"]["director"] = {**caps["modes"]["director"], "continue_from_video": False}

    errors = problems(film(48, 48, media=[clip_start("s1", relative)]), caps, str(storage))

    assert any("media type 'video' is not supported" in error for error in errors)


def test_a_model_without_start_pictures_cannot_start_from_a_clip(storage):
    errors = problems(film(120, 120, media=[clip_start("s1", previous_clip(storage))]), effective("txt2video", TEXT), str(storage))

    assert errors
