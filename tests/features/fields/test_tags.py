from unittest.mock import Mock

import pytest

from src.features.fields.tags import Tags
from src.features.presets.templates import PresetTemplate, ModeTemplate, FormTemplate, FieldTemplate


def _category(key, label=None, multi=False, allow_custom=None, tags=None):
    entry = {"key": key, "label": label or key, "multi": multi, "tags": tags or []}
    if allow_custom is not None:
        entry["allow_custom"] = allow_custom
    return entry


class TestOutputSchema:
    def test_inline_categories_are_resolved_in_declared_order(self):
        field = FieldTemplate(
            type="tags",
            name="style",
            label="Style tags",
            required=True,
            configuration={
                "separator": ", ",
                "categories": [
                    _category("colour", tags=["red", "blue"]),
                    _category("shape", multi=True, tags=["round", "square"]),
                ],
            },
        )
        schema = Tags(None).output(field)

        assert schema["type"] == "tags"
        assert schema["separator"] == ", "
        assert schema["allow_custom"] is True
        assert [c["key"] for c in schema["categories"]] == ["colour", "shape"]
        assert schema["categories"][0]["multi"] is False
        assert schema["categories"][0]["allow_custom"] is True
        assert schema["categories"][1]["tags"] == ["round", "square"]

    def test_category_level_allow_custom_overrides_field_default(self):
        field = FieldTemplate(
            type="tags",
            name="style",
            configuration={
                "allow_custom": True,
                "categories": [_category("colour", allow_custom=False, tags=["red"])],
            },
        )
        schema = Tags(None).output(field)
        assert schema["categories"][0]["allow_custom"] is False

    def test_max_tags_only_emitted_when_set(self):
        field = FieldTemplate(type="tags", name="style", configuration={"categories": []})
        schema = Tags(None).output(field)
        assert "max_tags" not in schema

        field.configuration["max_tags"] = 5
        schema = Tags(None).output(field)
        assert schema["max_tags"] == 5

    def test_config_indirection_resolves_against_preset_configuration(self, monkeypatch):
        from src.features.presets import repository as presets_repository

        monkeypatch.setattr(
            presets_repository.preset_repo,
            "get_preset_configuration",
            lambda preset_id: {"style_categories": [_category("mood", multi=True, tags=["dark", "playful"])]},
        )

        preset = PresetTemplate(
            id="preset_1", name="P", version="1.0.0", path="/x",
            modes={"txt2img": ModeTemplate(forms=[], pipes=[])},
            configuration={"style_categories": {"type": "tag_categories"}},
        )
        loader = Mock()
        loader.presets = [preset]

        field = FieldTemplate(type="tags", name="style", configuration={"categories": "@config:style_categories"})
        schema = Tags(loader).output(field, preset_id="preset_1")
        assert schema["categories"][0]["key"] == "mood"

    def test_config_indirection_falls_back_to_declared_default(self, monkeypatch):
        from src.features.presets import repository as presets_repository

        monkeypatch.setattr(presets_repository.preset_repo, "get_preset_configuration", lambda preset_id: {})

        default_categories = [_category("language", tags=["English"])]
        preset = PresetTemplate(
            id="preset_1", name="P", version="1.0.0", path="/x",
            modes={"txt2img": ModeTemplate(forms=[], pipes=[])},
            configuration={"style_categories": {"type": "tag_categories", "default": default_categories}},
        )
        loader = Mock()
        loader.presets = [preset]

        field = FieldTemplate(type="tags", name="style", configuration={"categories": "@config:style_categories"})
        schema = Tags(loader).output(field, preset_id="preset_1")
        assert schema["categories"][0]["key"] == "language"


class TestInputStructuredMap:
    def test_valid_map_is_cleaned_and_deduplicated(self):
        config = {"categories": [_category("colour", tags=["red", "blue"])]}
        result = Tags(None).input("style", {"colour": ["Red", " red ", "RED"]}, config)
        assert result == {"colour": ["Red"]}

    def test_unknown_category_key_is_an_error(self):
        config = {"categories": [_category("colour", tags=["red"])]}
        with pytest.raises(ValueError, match="unknown categor"):
            Tags(None).input("style", {"nope": ["x"]}, config)

    def test_multi_false_category_rejects_more_than_one_tag(self):
        config = {"categories": [_category("colour", multi=False, tags=["red", "blue"])]}
        with pytest.raises(ValueError, match="allows only one tag"):
            Tags(None).input("style", {"colour": ["red", "blue"]}, config)

    def test_allow_custom_false_rejects_tags_outside_the_declared_list(self):
        config = {"categories": [_category("colour", allow_custom=False, tags=["red", "blue"])]}
        with pytest.raises(ValueError, match="does not allow custom tags"):
            Tags(None).input("style", {"colour": ["purple"]}, config)

    def test_allow_custom_true_accepts_any_tag(self):
        config = {"categories": [_category("colour", allow_custom=True, tags=["red"])]}
        result = Tags(None).input("style", {"colour": ["purple"]}, config)
        assert result == {"colour": ["purple"]}

    def test_max_tags_across_categories_combined(self):
        config = {
            "categories": [_category("a", multi=True, tags=[]), _category("b", multi=True, tags=[])],
            "max_tags": 2,
        }
        with pytest.raises(ValueError, match="too many tags"):
            Tags(None).input("style", {"a": ["x", "y"], "b": ["z"]}, config)

    def test_no_categories_returns_empty_map(self):
        assert Tags(None).input("style", {"colour": ["red"]}, {"categories": []}) == {}

    def test_none_value_returns_empty_map(self):
        config = {"categories": [_category("colour", tags=["red"])]}
        assert Tags(None).input("style", None, config) == {"colour": []}


class TestInputStringSplitBack:
    def test_tokens_map_to_first_matching_category_case_insensitively(self):
        config = {
            "separator": ", ",
            "categories": [
                _category("colour", tags=["red", "blue"]),
                _category("more", multi=True, tags=[]),
            ],
        }
        result = Tags(None).input("style", "RED, mystery", config)
        assert result == {"colour": ["RED"], "more": ["mystery"]}

    def test_single_category_overflow_goes_to_the_tail(self):
        config = {
            "separator": ", ",
            "categories": [
                _category("colour", multi=False, tags=["red", "blue"]),
                _category("more", multi=True, tags=[]),
            ],
        }
        result = Tags(None).input("style", "red, blue", config)
        assert result == {"colour": ["red"], "more": ["blue"]}

    def test_unmatched_token_goes_to_the_tail(self):
        config = {
            "separator": ", ",
            "categories": [_category("colour", tags=["red"]), _category("more", multi=True, tags=[])],
        }
        result = Tags(None).input("style", "campfire vibes", config)
        assert result == {"colour": [], "more": ["campfire vibes"]}


class TestJoin:
    def test_joins_categories_in_declared_order(self):
        config = {"categories": [_category("a"), _category("b")], "separator": ", "}
        joined = Tags.join({"b": ["y"], "a": ["x"]}, config)
        assert joined == "x, y"

    def test_drops_blank_and_duplicate_tags(self):
        config = {"categories": [_category("a")], "separator": ", "}
        joined = Tags.join({"a": ["x", "", "  ", "X"]}, config)
        assert joined == "x"

    def test_non_dict_input_joins_to_empty_string(self):
        config = {"categories": [_category("a")], "separator": ", "}
        assert Tags.join(None, config) == ""
        assert Tags.join("not a dict", config) == ""


class TestCanHandle:
    def test_can_handle_tags(self):
        assert Tags(None).can_handle("tags") is True
        assert Tags(None).can_handle("checkbox_group") is False
