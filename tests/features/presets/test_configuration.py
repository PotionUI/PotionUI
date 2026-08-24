"""Tests for src.features.presets.configuration - the admin-set `configuration:`
validation/merge/`@config:` resolution helpers (see docs/presets.md
"Configuration (admin-set)"). Pure unit tests, no database - `tag_repository`
is a Mock everywhere validation needs to check tag existence."""

from unittest.mock import Mock

from src.features.presets.configuration import (
    validate_configuration_value,
    merge_configuration_schema,
    resolve_filter_tags,
    resolve_reactions_filter_tags,
)


class TestValidateConfigurationValue:
    def test_model_tags_valid(self):
        tag_repo = Mock()
        tag_repo.get_tag_by_id.return_value = Mock()  # any truthy tag
        assert validate_configuration_value("model_tags", ["tag_1", "tag_2"], tag_repo) is None

    def test_model_tags_not_a_list(self):
        tag_repo = Mock()
        error = validate_configuration_value("model_tags", "tag_1", tag_repo)
        assert error is not None
        assert "list" in error

    def test_model_tags_non_string_entry(self):
        tag_repo = Mock()
        error = validate_configuration_value("model_tags", [123], tag_repo)
        assert error is not None

    def test_model_tags_unknown_tag_id(self):
        tag_repo = Mock()
        tag_repo.get_tag_by_id.return_value = None
        error = validate_configuration_value("model_tags", ["missing_tag"], tag_repo)
        assert error is not None
        assert "unknown tag id" in error

    def test_unsupported_type(self):
        tag_repo = Mock()
        error = validate_configuration_value("not_a_real_type", [], tag_repo)
        assert error is not None
        assert "unsupported configuration type" in error


class TestMergeConfigurationSchema:
    def test_merges_declared_schema_with_stored_values(self):
        declared = {
            "checkpoint_tags": {"type": "model_tags", "label": "Checkpoint tags", "description": "d"},
        }
        entries = merge_configuration_schema(declared, {"checkpoint_tags": ["tag_1"]})
        assert entries == [{
            "key": "checkpoint_tags",
            "type": "model_tags",
            "label": "Checkpoint tags",
            "description": "d",
            "value": ["tag_1"],
        }]

    def test_value_is_none_when_unset(self):
        declared = {"checkpoint_tags": {"type": "model_tags"}}
        entries = merge_configuration_schema(declared, {})
        assert entries[0]["value"] is None

    def test_empty_when_no_declared_schema(self):
        assert merge_configuration_schema(None, {"checkpoint_tags": ["tag_1"]}) == []
        assert merge_configuration_schema({}, {}) == []


class TestResolveFilterTags:
    def test_none_raw_means_no_filtering(self):
        assert resolve_filter_tags(None, {}) is None

    def test_literal_list_passthrough(self):
        assert resolve_filter_tags(["tag_1", "tag_2"], {}) == ["tag_1", "tag_2"]

    def test_empty_literal_list_means_no_filtering(self):
        assert resolve_filter_tags([], {}) is None

    def test_config_indirection_resolves(self):
        values = {"checkpoint_tags": ["tag_1", "tag_2"]}
        assert resolve_filter_tags("@config:checkpoint_tags", values) == ["tag_1", "tag_2"]

    def test_config_indirection_missing_key_means_no_filtering(self):
        assert resolve_filter_tags("@config:checkpoint_tags", {}) is None

    def test_config_indirection_empty_value_means_no_filtering(self):
        assert resolve_filter_tags("@config:checkpoint_tags", {"checkpoint_tags": []}) is None

    def test_non_config_string_means_no_filtering(self):
        # A plain string (not "@config:...") is not a documented shape - treat as no-op.
        assert resolve_filter_tags("some_literal_string", {}) is None


class TestResolveReactionsFilterTags:
    """`set_filter_tags` inside a reaction's `then` block is the one action key
    resolved server-side (reactions run frontend-only otherwise, see
    docs/presets.md "Reactions") - it needs the same `@config:<key>` -> concrete
    tag-ID-list resolution the field's own static `filter_tags:` gets."""

    def test_none_and_empty_reactions_pass_through(self):
        assert resolve_reactions_filter_tags(None, "preset_1") is None
        assert resolve_reactions_filter_tags([], "preset_1") == []

    def test_reactions_without_set_filter_tags_are_returned_unchanged(self):
        reactions = [
            {"when": {"field": "speed_profile", "equals": "fast"}, "then": {"set_disabled": True}}
        ]
        assert resolve_reactions_filter_tags(reactions, "preset_1") is reactions

    def test_resolves_a_literal_tag_list(self):
        reactions = [
            {"when": {"field": "speed_profile", "equals": "fast"}, "then": {"set_filter_tags": ["tag_1"]}}
        ]
        resolved = resolve_reactions_filter_tags(reactions, "preset_1")
        assert resolved[0]["then"]["set_filter_tags"] == ["tag_1"]
        assert resolved is not reactions  # a new list - the input is never mutated
        assert reactions[0]["then"]["set_filter_tags"] == ["tag_1"]  # original untouched

    def test_only_resolves_reactions_that_declare_set_filter_tags(self):
        reactions = [
            {"when": {"field": "speed_profile", "equals": "balanced"}, "then": {"set_disabled": False}},
            {"when": {"field": "speed_profile", "equals": "fast"}, "then": {"set_filter_tags": ["tag_1"]}},
        ]
        resolved = resolve_reactions_filter_tags(reactions, "preset_1")
        assert resolved[0] is reactions[0]  # untouched reaction is the same object
        assert resolved[1]["then"]["set_filter_tags"] == ["tag_1"]

    def test_resolves_config_indirection_against_stored_preset_configuration(self, monkeypatch):
        from src.features.presets import repository as presets_repository

        monkeypatch.setattr(
            presets_repository.preset_repo,
            "get_preset_configuration",
            lambda preset_id: {"checkpoint_tags_fast": ["distilled_tag"]},
        )
        reactions = [
            {
                "when": {"field": "speed_profile", "equals": "fast"},
                "then": {"set_filter_tags": "@config:checkpoint_tags_fast"},
            }
        ]
        resolved = resolve_reactions_filter_tags(reactions, "preset_1")
        assert resolved[0]["then"]["set_filter_tags"] == ["distilled_tag"]

    def test_unset_config_key_resolves_to_none_not_an_error(self, monkeypatch):
        from src.features.presets import repository as presets_repository

        monkeypatch.setattr(
            presets_repository.preset_repo, "get_preset_configuration", lambda preset_id: {}
        )
        reactions = [
            {"when": {"field": "x", "equals": "y"}, "then": {"set_filter_tags": "@config:never_set"}}
        ]
        resolved = resolve_reactions_filter_tags(reactions, "preset_1")
        assert resolved[0]["then"]["set_filter_tags"] is None

    def test_no_preset_id_resolves_to_none(self):
        reactions = [{"when": {"field": "x", "equals": "y"}, "then": {"set_filter_tags": ["tag_1"]}}]
        resolved = resolve_reactions_filter_tags(reactions, None)
        assert resolved[0]["then"]["set_filter_tags"] is None

    def test_preserves_other_keys_in_the_then_block(self):
        reactions = [
            {
                "when": {"field": "speed_profile", "equals": "fast"},
                "then": {"set_filter_tags": ["tag_1"], "set_disabled": True},
            }
        ]
        resolved = resolve_reactions_filter_tags(reactions, "preset_1")
        assert resolved[0]["then"]["set_disabled"] is True
