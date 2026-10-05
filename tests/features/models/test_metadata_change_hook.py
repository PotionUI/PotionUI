from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.features.models.attributes.records import ModelAttributeDefinition
from src.features.models.exceptions import ModelNotFoundException
from src.features.models.hooks import MODEL_INDEX_HOOKS
from src.features.models.metadata_editor import ModelMetadataEditor


@pytest.fixture
def plugins():
    registry = Mock()
    registry.calls = []

    def execute(hook, initial_data=None):
        registry.calls.append((hook, dict(initial_data or {})))
        return SimpleNamespace(data=dict(initial_data or {})), []

    registry.execute_hook.side_effect = execute
    return registry


@pytest.fixture
def editor(plugins):
    repo = Mock()
    repo.get_by_id.return_value = SimpleNamespace(
        id="m1", model_type="lora", to_dict=lambda **kwargs: {"id": "m1"},
    )
    repo.update_description.return_value = True
    repo.update_model_metadata.return_value = True
    definitions = Mock()
    definitions.for_model_type.return_value = [ModelAttributeDefinition(key="notes", label="Notes", field_type="text")]
    return ModelMetadataEditor(repo, Mock(), plugins, Mock(), attribute_definition_repository=definitions)


def metadata_events(plugins):
    return [data for hook, data in plugins.calls if hook == MODEL_INDEX_HOOKS.after_update_metadata]


def test_description_edits_announce_the_change(editor, plugins):
    editor.update_model_description("m1", "New text")
    assert metadata_events(plugins) == [{"model_id": "m1", "user_id": None, "fields": ["description"]}]


def test_attribute_edits_announce_the_change(editor, plugins):
    editor.update_model_metadata("m1", {"notes": "low cfg"})
    assert metadata_events(plugins) == [{"model_id": "m1", "user_id": None, "fields": ["attributes"]}]


def test_failed_edits_announce_nothing(editor, plugins):
    editor.model_repo.update_model_metadata.return_value = False
    with pytest.raises(ModelNotFoundException):
        editor.update_model_metadata("m1", {"notes": "x"})
    assert metadata_events(plugins) == []


def test_a_failing_listener_never_breaks_the_save(editor, plugins):
    plugins.execute_hook.side_effect = RuntimeError("listener broke")
    assert editor.update_model_description("m1", "New text")["model"] == {"id": "m1"}


def test_per_user_changes_carry_the_user(editor, plugins):
    editor.notify_metadata_changed("m1", ["name"], "u1")
    assert metadata_events(plugins) == [{"model_id": "m1", "user_id": "u1", "fields": ["name"]}]
