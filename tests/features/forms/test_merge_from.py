import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.features.forms.binding import FormBindingError, bind_form
from src.features.forms.merge_from import apply_merge_from, mode_merge_from_aliases, rewrite_merged_markers
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PresetTemplate


CASES = json.loads((Path(__file__).resolve().parents[2] / "fixtures" / "merge_from_cases.json").read_text())["cases"]


def _spec(name="media_inputs", merge_from=("old_clips", "old_tracks")):
    return SimpleNamespace(name=name, merge_from=list(merge_from) if merge_from else None)


class TestSharedCases:
    @pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
    def test_apply_merge_from_matches_the_shared_case(self, case):
        fields = [SimpleNamespace(name=f.get("name"), merge_from=f.get("merge_from")) for f in case["fields"]]

        assert apply_merge_from(fields, case["data"]) == case["expected"]


class TestApplyMergeFrom:
    def test_listed_keys_are_removed(self):
        result = apply_merge_from([_spec()], {"old_clips": ["v1"], "keep": 1})

        assert "old_clips" not in result
        assert result == {"keep": 1, "media_inputs": ["v1"]}

    def test_input_is_not_mutated(self):
        own = ["i1"]
        data = {"media_inputs": own, "old_clips": ["v1"]}

        apply_merge_from([_spec()], data)

        assert data == {"media_inputs": ["i1"], "old_clips": ["v1"]}
        assert own == ["i1"]

    def test_fields_without_merge_from_are_ignored(self):
        data = {"a": 1}

        assert apply_merge_from([_spec("a", None), SimpleNamespace(name=None, merge_from=["x"])], data) == data


def _field(name, type_="media", configuration=None, merge_from=None):
    return FieldTemplate(type=type_, name=name, configuration=configuration, merge_from=merge_from)


def _preset(fields):
    return PresetTemplate(
        id="preset_merge_from",
        name="Merge From",
        version="1.0.0",
        path="/presets/preset_merge_from",
        modes={"txt2img": ModeTemplate(
            forms=[FormTemplate(name="custom", fields=fields, default=True, order=0)],
            pipes=[],
        )},
    )


class TestBindFormMergeFrom:
    def _preset(self, config=None):
        return _preset([_field(
            "media_inputs",
            configuration=config or {"multi": True, "accepted_types": ["image", "video", "audio"]},
            merge_from=["old_clips", "old_tracks"],
        )])

    def test_old_keys_bind_into_the_merged_field(self, tmp_path):
        form = {
            "media_inputs": [{"path": "uploads/a.png", "relative_path": "uploads/a.png", "type": "image"}],
            "old_clips": [{"path": "uploads/b.mp4", "relative_path": "uploads/b.mp4", "type": "video"}],
            "old_tracks": ["uploads/c.mp3"],
        }

        bound = bind_form(self._preset(), "txt2img", None, form, "user_1", storage_dir=str(tmp_path))

        assert [item["path"] if isinstance(item, dict) else item for item in bound.values["media_inputs"]] == [
            "uploads/a.png", "uploads/b.mp4", "uploads/c.mp3",
        ]
        assert "old_clips" not in bound.values
        assert "old_tracks" not in bound.values

    def test_merged_list_goes_through_per_kind_caps(self, tmp_path):
        config = {"multi": True, "max_items_by_kind": {"video": 1}}
        form = {"old_clips": ["uploads/a.mp4", "uploads/b.mp4"]}

        with pytest.raises(FormBindingError) as exc:
            bind_form(self._preset(config), "txt2img", None, form, "user_1", storage_dir=str(tmp_path))

        assert "Too many video items" in str(exc.value)

    def test_untyped_item_with_an_unknown_extension_is_rejected_plainly(self, tmp_path):
        config = {"multi": True, "accepted_types": ["image", "video", "audio"]}
        form = {"old_clips": [{"path": "uploads/clip.xyz", "relative_path": "uploads/clip.xyz"}]}

        with pytest.raises(FormBindingError) as exc:
            bind_form(self._preset(config), "txt2img", None, form, "user_1", storage_dir=str(tmp_path))

        assert "could not tell" in str(exc.value)


class TestMarkerRewrite:
    def test_input_is_not_mutated_by_the_rewrite(self):
        data = {"doc": {"text": "@[old_clips:v.mp4]"}}

        apply_merge_from([_spec()], data)

        assert data == {"doc": {"text": "@[old_clips:v.mp4]"}}

    def test_aliases_come_from_nested_fields_of_every_form(self):
        nested = _field("tabs", type_="tabs")
        nested.children = [_field("media_inputs", merge_from=["old_clips"])]
        preset = _preset([nested])
        preset.modes["txt2img"].forms.append(
            FormTemplate(name="other", fields=[_field("gallery", merge_from=["old_tracks"])], order=1)
        )

        assert mode_merge_from_aliases(preset, "txt2img") == {"old_clips": "media_inputs", "old_tracks": "gallery"}
        assert mode_merge_from_aliases(preset, "missing") == {}

    def test_rewrite_without_aliases_returns_the_value(self):
        value = {"a": "@[old_clips:v.mp4]"}

        assert rewrite_merged_markers(value, {}) is value


class TestBindFormMarkerRewrite:
    def test_a_saved_text_value_binds_with_rewritten_markers(self, tmp_path):
        preset = _preset([
            _field("media_inputs", configuration={"multi": True}, merge_from=["old_clips"]),
            _field("notes", type_="string"),
        ])
        form = {"old_clips": ["uploads/v.mp4"], "notes": "like @[old_clips:uploads/v.mp4]"}

        bound = bind_form(preset, "txt2img", None, form, "user_1", storage_dir=str(tmp_path))

        assert bound.values["notes"] == "like @[media_inputs:uploads/v.mp4]"
        assert bound.values["media_inputs"] == ["uploads/v.mp4"]
