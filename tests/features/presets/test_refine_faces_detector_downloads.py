from pathlib import Path

import pytest
import yaml

PRESETS = Path("content/presets/marketplace")
REFINE_FACES_PRESETS = ["MiniMax-H3", "MiniMax-H3-Fast", "LTX-2.5"]
LANDMARKER_LINKS = {
    "face_detector_model": "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
    "hand_detector_model": "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
}


def _field(node, name):
    if isinstance(node, dict):
        if node.get("name") == name and node.get("type") == "model":
            return node
        for value in node.values():
            found = _field(value, name)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _field(item, name)
            if found is not None:
                return found
    return None


def _detailer_config(preset, mode):
    pipeline = yaml.safe_load((PRESETS / preset / "modes" / mode / "pipeline.yml").read_text(encoding="utf-8"))
    return next(p for p in pipeline["pipeline"] if p["name"].startswith("detailer/video_"))["configuration"]


@pytest.mark.parametrize("preset", REFINE_FACES_PRESETS)
@pytest.mark.parametrize("field_name", sorted(LANDMARKER_LINKS))
def test_refine_faces_offers_the_mediapipe_landmarker_download(preset, field_name):
    tab = yaml.safe_load(
        (PRESETS / preset / "modes/refine_faces/tabs/generation.yml").read_text(encoding="utf-8"))
    field = _field(tab, field_name)
    assert field is not None
    links = [rec["link"] for rec in field["configuration"]["recommendations"]]
    assert links == [LANDMARKER_LINKS[field_name]]


@pytest.mark.parametrize("preset", REFINE_FACES_PRESETS)
def test_refine_faces_requires_its_detectors(preset):
    assert _detailer_config(preset, "refine_faces")["require_detectors"] is True


@pytest.mark.parametrize("preset,mode", [
    ("LTX-2.5", "txt2video"),
    ("LTX-2.5", "video"),
    ("LTX-2.5", "upscale"),
    ("LTX-2", "video"),
    ("LTX-2", "upscale"),
])
def test_the_optional_refine_pass_of_other_modes_still_skips(preset, mode):
    assert "require_detectors" not in _detailer_config(preset, mode)
