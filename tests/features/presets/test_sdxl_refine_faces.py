import pytest

from tests.features.presets._sdxl_modes import (
    is_enabled,
    load_sdxl,
    mode_fields,
    mode_form,
    pipe,
    preset_manifest,
    provider,
    render,
)

MODE = "refine_faces"


@pytest.fixture(scope="module")
def sdxl():
    return load_sdxl()


def test_the_mode_is_registered_and_the_prompt_is_optional():
    assert MODE in preset_manifest()["modes"]
    form = mode_form(MODE)
    assert form["prompt"] == "optional"
    assert form["mode_label"] == "Fix faces & hands"


def test_both_runs_faces_then_hands_on_the_same_picture(sdxl):
    pipes = render(sdxl, MODE, fix_target="both")
    faces, hands = pipe(pipes, "face_detailer"), pipe(pipes, "hand_detailer")
    assert is_enabled(faces) and is_enabled(hands)
    assert provider(faces, "image") == "media_loader"
    assert provider(hands, "image") == "face_detailer"
    assert provider(faces, "conditioning") == "face_encoder"
    assert provider(hands, "conditioning") == "hand_encoder"
    assert provider(pipe(pipes, "gallery"), "image") == "hand_detailer"


def test_faces_only_never_touches_the_hand_chain(sdxl):
    pipes = render(sdxl, MODE, fix_target="faces")
    assert is_enabled(pipe(pipes, "face_detailer"))
    assert not is_enabled(pipe(pipes, "hand_detailer"))
    assert not is_enabled(pipe(pipes, "hand_encoder"))
    assert provider(pipe(pipes, "gallery"), "image") == "face_detailer"


def test_hands_only_reads_the_source_picture_directly(sdxl):
    pipes = render(sdxl, MODE, fix_target="hands")
    assert not is_enabled(pipe(pipes, "face_detailer"))
    assert not is_enabled(pipe(pipes, "face_encoder"))
    hands = pipe(pipes, "hand_detailer")
    assert is_enabled(hands)
    assert provider(hands, "image") == "media_loader"
    assert provider(pipe(pipes, "gallery"), "image") == "hand_detailer"


def test_each_detailer_only_looks_for_its_own_target(sdxl):
    pipes = render(sdxl, MODE)
    assert pipe(pipes, "face_detailer")["config"]["detect"] == ["face"]
    assert pipe(pipes, "hand_detailer")["config"]["detect"] == ["hand"]


def test_strength_and_detector_settings_reach_both_detections(sdxl):
    pipes = render(sdxl, MODE, fix_strength=0.55, fix_confidence=0.2, fix_padding=48)
    face = pipe(pipes, "face_detailer")["config"]["detections"]["face"]
    hand = pipe(pipes, "hand_detailer")["config"]["detections"]["hand"]
    for detection in (face, hand):
        assert detection["strength"] == 0.55
        assert detection["confidence"] == 0.2
        assert detection["padding"] == 48
        assert detection["type"] == "yolo"
    assert face["model"].endswith("face_detector_model.safetensors")
    assert hand["model"].endswith("hand_detector_model.safetensors")


def test_the_prompt_box_is_the_face_prompt_with_a_quality_tail(sdxl):
    pipes = render(sdxl, MODE, positive="smiling")
    assert pipe(pipes, "face_encoder")["config"]["p_prompt"]["output"] == "smiling, detailed face, detailed eyes, best quality"


def test_an_empty_prompt_still_gives_the_face_a_usable_prompt(sdxl):
    pipes = render(sdxl, MODE, positive="")
    assert pipe(pipes, "face_encoder")["config"]["p_prompt"]["output"] == "detailed face, detailed eyes, best quality"


def test_hands_use_their_own_prompt_not_the_face_prompt(sdxl):
    pipes = render(sdxl, MODE, positive="smiling", hand_prompt="gloved hands")
    assert pipe(pipes, "hand_encoder")["config"]["p_prompt"]["output"] == "gloved hands"


def test_the_detector_pickers_follow_the_target_and_recommend_hashed_files():
    fields = mode_fields(MODE, "generation")
    for name in ("face_detector_model", "hand_detector_model"):
        recs = fields[name]["configuration"]["recommendations"]
        assert fields[name]["required"] is True
        assert fields[name]["configuration"]["model_type"] == "detection_bbox"
        for rec in recs:
            assert rec["name"].endswith(".pt")
            assert len(rec["sha256"]) == 64
            assert rec["link"].startswith("https://huggingface.co/")
    face_when = {r["when"]["field"]: r for r in fields["face_detector_model"]["reactions"]}
    assert set(face_when) == {"fix_target"}
    assert fields["face_detector_model"]["reactions"][0]["when"]["equals"] == "hands"
    assert fields["hand_detector_model"]["reactions"][0]["when"]["equals"] == "faces"
