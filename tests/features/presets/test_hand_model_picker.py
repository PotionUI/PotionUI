from pathlib import Path

import pytest
import yaml

from src.features.presets.configuration import resolve_filter_tags

MARKETPLACE = Path(__file__).resolve().parents[3] / "content" / "presets" / "marketplace"


def _hand_fields():
    found = []
    for path in sorted(MARKETPLACE.glob("*/modes/*/tabs/*.yml")):
        text = path.read_text(encoding="utf-8")
        if "face_detailer_hand_model" not in text:
            continue
        stack = [yaml.safe_load(text)]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if node.get("name") == "face_detailer_hand_model":
                    found.append((path, node))
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    return found


HAND_FIELDS = _hand_fields()


def test_every_shipped_hand_model_field_is_found():
    assert len(HAND_FIELDS) == 10


@pytest.mark.parametrize("path, field", HAND_FIELDS, ids=lambda value: getattr(value, "name", ""))
def test_hand_pickers_filter_by_the_hand_tags_and_the_preset_declares_them(path, field):
    assert field["configuration"]["filter_tags"] == "@config:mediapipe_hand_tags"
    preset = yaml.safe_load((path.parents[3] / "preset.yml").read_text(encoding="utf-8"))
    assert preset["configuration"]["mediapipe_hand_tags"]["type"] == "model_tags"


def test_an_untagged_install_lists_every_mediapipe_model_in_the_hand_picker():
    assert resolve_filter_tags("@config:mediapipe_hand_tags", {}) is None
    assert resolve_filter_tags("@config:mediapipe_hand_tags", {"mediapipe_hand_tags": []}) is None
    assert resolve_filter_tags("@config:mediapipe_hand_tags", {"mediapipe_hand_tags": ["t1"]}) == ["t1"]
