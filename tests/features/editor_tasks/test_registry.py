import pytest
import yaml
from pydantic import ValidationError

from src.features.editor_tasks.registry import (
    CORE_TASK_IDS,
    EXPAND_PROMPT,
    REMOVE_OBJECT_PROMPT,
    TaskDefinition,
    TaskRegistrationError,
    TaskRegistry,
    register_core_tasks,
)
from src.platform.plugins.manifest import PluginManifestSchema
from src.platform.plugins.registry import PluginRegistry

BASE = {"id": "shiny", "name": "Shiny", "version": "1.0.0", "description": "d", "author": "a", "type": "backend-only"}
ENTRY = {"id": "shiny.colorize", "label": "Colorize a region", "region": "required", "prompt": "optional"}


def test_core_tasks_are_present():
    registry = register_core_tasks(TaskRegistry())

    image = {"edit_region", "remove_object", "replace_background", "find_subject", "expand", "fix_faces"}
    assert image <= {task.id for task in registry.list("image")}
    assert CORE_TASK_IDS >= image | {task.id for task in registry.list("video")}
    assert registry.get("remove_object").fallback == ("edit_region", REMOVE_OBJECT_PROMPT)
    assert registry.get("remove_object").prompt == "none"
    assert registry.get("edit_region").fallback is None
    assert registry.get("expand").grows and registry.get("expand").region == "none"
    assert registry.get("expand").fallback == ("edit_region", EXPAND_PROMPT)
    assert not registry.get("edit_region").grows


def test_a_listing_filters_by_editor():
    registry = register_core_tasks(TaskRegistry())
    registry.register(TaskDefinition(id="shiny.clip", label="Clip", editors=("video",), source="shiny"))

    assert "shiny.clip" not in {task.id for task in registry.list("image")}
    assert "shiny.clip" in {task.id for task in registry.list("video")}
    assert "shiny.clip" in {task.id for task in registry.list()}


def test_a_plugin_cannot_use_a_core_id():
    registry = register_core_tasks(TaskRegistry())

    with pytest.raises(TaskRegistrationError):
        registry.register(TaskDefinition(id="edit_region", label="Mine", source="shiny"))

    assert registry.get("edit_region").source == "core"


def test_a_plugin_cannot_use_another_plugins_namespace():
    with pytest.raises(TaskRegistrationError):
        TaskRegistry().register(TaskDefinition(id="other.colorize", label="Mine", source="shiny"))


def test_core_ids_have_no_dot():
    with pytest.raises(TaskRegistrationError):
        TaskRegistry().register(TaskDefinition(id="core.thing", label="Mine"))


def test_a_duplicate_from_another_source_is_refused():
    registry = TaskRegistry()
    registry.register(TaskDefinition(id="a.b.c", label="A", source="a"))

    with pytest.raises(TaskRegistrationError):
        registry.register(TaskDefinition(id="a.b.c", label="B", source="a.b"))

    assert registry.get("a.b.c").source == "a"


def test_core_tasks_survive_unregistering_a_source():
    registry = register_core_tasks(TaskRegistry())
    registry.register(TaskDefinition(id="shiny.colorize", label="A", source="shiny"))

    assert registry.unregister_source("shiny") == 1
    assert registry.unregister_source("core") == 0
    assert registry.get("shiny.colorize") is None
    assert registry.get("edit_region") is not None


def test_a_plugin_task_can_declare_that_it_grows_the_canvas(plugins):
    registry, tasks, marketplace = plugins
    write_plugin(marketplace, [{**ENTRY, "grows": True}])
    registry.discover_plugins()

    assert registry.enable_plugin("shiny") is True
    assert tasks.get("shiny.colorize").grows is True


def test_the_base_manifest_entry_validates():
    manifest = PluginManifestSchema.model_validate({**BASE, "editor_tasks": [ENTRY]})

    assert [item.id for item in manifest.editor_tasks] == ["shiny.colorize"]


def test_the_manifest_takes_grow_feather_matte_and_internal_with_old_defaults():
    plain = PluginManifestSchema.model_validate({**BASE, "editor_tasks": [ENTRY]}).editor_tasks[0]
    full = PluginManifestSchema.model_validate(
        {**BASE, "editor_tasks": [{**ENTRY, "produces": "matte", "grow": 0.05, "feather": 2, "matte": True, "internal": True}]}
    ).editor_tasks[0]

    assert (plain.grow, plain.feather, plain.matte, plain.internal) == (0.0, 1.0, False, False)
    assert (full.produces, full.grow, full.feather, full.matte, full.internal) == ("matte", 0.05, 2.0, True, True)
    with pytest.raises(ValidationError):
        PluginManifestSchema.model_validate({**BASE, "editor_tasks": [{**ENTRY, "grow": -1}]})


@pytest.mark.parametrize(
    "entries, message",
    [
        ([{**ENTRY, "id": "other.colorize"}], "must start with 'shiny.'"),
        ([{**ENTRY, "id": "edit_region"}], "String should match pattern"),
        ([ENTRY, ENTRY], "is declared twice"),
        ([{**ENTRY, "colour": "red"}], "Extra inputs are not permitted"),
    ],
)
def test_a_manifest_entry_that_breaks_a_rule_is_refused(entries, message):
    with pytest.raises(ValidationError, match=message):
        PluginManifestSchema.model_validate({**BASE, "editor_tasks": entries})


@pytest.fixture
def plugins(tmp_path):
    marketplace = tmp_path / "marketplace"
    local = tmp_path / "local"
    marketplace.mkdir()
    local.mkdir()
    tasks = register_core_tasks(TaskRegistry())
    registry = PluginRegistry(str(marketplace), str(local), editor_task_registry=tasks)
    return registry, tasks, marketplace


def write_plugin(marketplace, entries, plugin_id="shiny"):
    folder = marketplace / plugin_id
    folder.mkdir()
    manifest = {**BASE, "id": plugin_id, "editor_tasks": entries}
    (folder / "manifest.yml").write_text(yaml.safe_dump(manifest), encoding="utf-8")


def test_a_plugin_task_is_registered_on_enable_and_removed_on_disable(plugins):
    registry, tasks, marketplace = plugins
    write_plugin(marketplace, [{**ENTRY, "fallback": {"task": "edit_region", "prompt": "Colorize it."}}])
    registry.discover_plugins()

    assert registry.enable_plugin("shiny") is True
    task = tasks.get("shiny.colorize")
    assert task.source == "shiny"
    assert task.region == "required"
    assert task.prompt == "optional"
    assert task.fallback == ("edit_region", "Colorize it.")
    assert task.grows is False

    registry.disable_plugin("shiny")

    assert tasks.get("shiny.colorize") is None
    assert tasks.get("edit_region") is not None


def test_a_plugin_task_in_the_core_namespace_does_not_load(plugins):
    registry, tasks, marketplace = plugins
    write_plugin(marketplace, [{**ENTRY, "id": "edit_region"}])
    registry.discover_plugins()

    assert registry.enable_plugin("shiny") is False
    assert tasks.get("edit_region").source == "core"
