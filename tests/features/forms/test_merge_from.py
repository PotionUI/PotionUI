from types import SimpleNamespace

import pytest

from src.features.forms.binding import FormBindingError, bind_form
from src.features.forms.merge_from import apply_merge_from, mode_merge_from_aliases, rewrite_merged_markers
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PresetTemplate


def _spec(name="media_inputs", merge_from=("old_clips", "old_tracks")):
    return SimpleNamespace(name=name, merge_from=list(merge_from) if merge_from else None)


class TestApplyMergeFrom:
    def test_own_value_then_listed_keys_in_listed_order(self):
        data = {"media_inputs": ["i1", "i2"], "old_tracks": ["a1"], "old_clips": ["v1", "v2"]}

        result = apply_merge_from([_spec()], data)

        assert result == {"media_inputs": ["i1", "i2", "v1", "v2", "a1"]}

    def test_absent_listed_keys_leave_data_untouched(self):
        data = {"media_inputs": "single.png", "other": 1}

        result = apply_merge_from([_spec()], data)

        assert result == data
        assert result["media_inputs"] == "single.png"

    def test_listed_keys_are_removed(self):
        result = apply_merge_from([_spec()], {"old_clips": ["v1"], "keep": 1})

        assert "old_clips" not in result
        assert result == {"keep": 1, "media_inputs": ["v1"]}

    def test_scalars_become_one_item_lists_and_empties_vanish(self):
        data = {"media_inputs": "a.png", "old_clips": "b.mp4", "old_tracks": ""}

        assert apply_merge_from([_spec()], data)["media_inputs"] == ["a.png", "b.mp4"]
        assert apply_merge_from([_spec()], {"media_inputs": None, "old_clips": []})["media_inputs"] == []

    def test_dict_scalar_counts_as_one_item(self):
        item = {"path": "v.mp4", "type": "video"}

        assert apply_merge_from([_spec()], {"old_clips": item})["media_inputs"] == [item]

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


class TestMarkerRewrite:
    def test_markers_of_merged_keys_take_the_field_name_and_keep_the_item_key(self):
        data = {"notes": "a @[old_clips:uploads/b.mp4] and @[old_tracks:uploads/c.mp3] by @[media_inputs:uploads/a.png]"}

        result = apply_merge_from([_spec()], data)

        assert result["notes"] == (
            "a @[media_inputs:uploads/b.mp4] and @[media_inputs:uploads/c.mp3] by @[media_inputs:uploads/a.png]"
        )

    def test_markers_are_rewritten_even_without_old_values(self):
        result = apply_merge_from([_spec()], {"media_inputs": [], "notes": "@[old_clips:x.mp4]"})

        assert result["notes"] == "@[media_inputs:x.mp4]"

    def test_nested_strings_and_resource_refs_are_rewritten(self):
        data = {"doc": {
            "segments": [{
                "prompt": "walks like @[old_clips:v.mp4]",
                "prompt_segments": [{
                    "content": "@[old_clips:v.mp4]",
                    "resources": {"m1": {"field": "old_clips", "item_key": "v.mp4"}},
                }],
            }],
        }}

        segment = apply_merge_from([_spec()], data)["doc"]["segments"][0]

        assert segment["prompt"] == "walks like @[media_inputs:v.mp4]"
        assert segment["prompt_segments"][0]["content"] == "@[media_inputs:v.mp4]"
        assert segment["prompt_segments"][0]["resources"] == {"m1": {"field": "media_inputs", "item_key": "v.mp4"}}

    def test_unrelated_markers_and_dicts_are_left_alone(self):
        data = {"notes": "@[other:x.png] @[old_clipsy:y.mp4]", "ref": {"field": "old_clips"}}

        assert apply_merge_from([_spec()], data) == data

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
