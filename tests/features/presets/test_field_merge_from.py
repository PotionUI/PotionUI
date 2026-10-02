import pytest
from pydantic import ValidationError

from src.features.fields.media import Media
from src.features.presets.schema import FieldSpec
from src.features.presets.templates import FieldTemplate


def _spec(**overrides):
    return FieldSpec(**{"type": "media", "name": "media_inputs", **overrides})


class TestFieldSpecMergeFrom:
    def test_valid_list_is_accepted(self):
        assert _spec(merge_from=["old_clips", "old_tracks"]).merge_from == [
            "old_clips", "old_tracks",
        ]

    def test_defaults_to_none(self):
        assert _spec().merge_from is None

    @pytest.mark.parametrize("value", [[""], ["  "], ["media_inputs"], ["a", "a"]])
    def test_invalid_entries_are_rejected(self, value):
        with pytest.raises(ValidationError):
            _spec(merge_from=value)

    def test_non_string_entry_is_rejected(self):
        with pytest.raises(ValidationError):
            _spec(merge_from=[3])


class TestMergeFromSchemaEmission:
    def test_emitted_from_template_when_set(self):
        field = FieldTemplate(type="media", name="media_inputs", merge_from=["old_clips"])

        assert Media(None).output(field)["merge_from"] == ["old_clips"]

    def test_emitted_from_dict_when_set(self):
        field = {"type": "media", "name": "media_inputs", "merge_from": ["old_clips"]}

        assert Media(None).output(field)["merge_from"] == ["old_clips"]

    def test_absent_when_unset(self):
        field = FieldTemplate(type="media", name="media_inputs")

        assert "merge_from" not in Media(None).output(field)

    def test_media_output_with_configuration_keeps_it(self):
        field = FieldTemplate(
            type="media", name="media_inputs", merge_from=["old_clips"],
            configuration={"multi": True},
        )

        assert Media(None).output(field)["merge_from"] == ["old_clips"]
