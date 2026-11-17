import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.platform.plugins.editor_schema import EditorSchemaError, read_tool_schema, validate_tool_schema
from src.platform.plugins.field_types import FieldTypeDefinition, FieldTypeRegistry
from src.platform.plugins.manifest import PluginManifestSchema
from src.platform.plugins.registry import PluginRegistry

TOOL = {"id": "acme.form", "editor": "image", "group": "look", "label": "Form", "schema": "editor/form.schema.json"}
KNOWN = {"slider", "checkbox", "row", "select"}


def manifest(**tool):
    return PluginManifestSchema.model_validate(
        {
            "id": "acme",
            "name": "Acme",
            "version": "1.0.0",
            "description": "d",
            "author": "a",
            "type": "frontend-only",
            "editor_tools": [{**TOOL, **tool}],
        }
    )


def write_schema(plugin_dir: Path, payload, name="form.schema.json"):
    target = plugin_dir / "frontend" / "dist" / "editor"
    target.mkdir(parents=True, exist_ok=True)
    (target / name).write_text(json.dumps(payload), encoding="utf-8")


class TestManifestKey:
    def test_a_tool_may_declare_a_schema_file_and_it_stays_optional(self):
        assert manifest().editor_tools[0].schema_file == "editor/form.schema.json"
        bare = {k: v for k, v in TOOL.items() if k != "schema"}
        assert manifest(**{"schema": None}).editor_tools[0].schema_file is None
        assert PluginManifestSchema.model_validate(
            {"id": "acme", "name": "A", "version": "1.0.0", "description": "d", "author": "a", "type": "frontend-only", "editor_tools": [bare]}
        ).editor_tools[0].schema_file is None

    @pytest.mark.parametrize("path", ["/abs.json", "../up.json", "editor\\x.json", "c:/x.json", "editor/form.js", "editor/form"])
    def test_the_path_must_be_a_json_file_inside_the_bundle(self, path):
        with pytest.raises(ValidationError):
            manifest(schema=path)

    def test_a_schema_and_an_options_component_may_be_declared_together(self):
        assert manifest(options="editor/Options.js").editor_tools[0].options == "editor/Options.js"


class TestReadToolSchema:
    def test_reads_a_list_of_fields(self, tmp_path):
        write_schema(tmp_path, [{"type": "slider", "name": "a"}])
        assert read_tool_schema(tmp_path, "editor/form.schema.json") == [{"type": "slider", "name": "a"}]

    def test_reads_a_fields_key_too(self, tmp_path):
        write_schema(tmp_path, {"fields": [{"type": "slider", "name": "a"}]})
        assert read_tool_schema(tmp_path, "editor/form.schema.json")[0]["name"] == "a"

    def test_refuses_a_missing_file_bad_json_and_a_non_list(self, tmp_path):
        with pytest.raises(EditorSchemaError, match="was not found"):
            read_tool_schema(tmp_path, "editor/form.schema.json")
        target = tmp_path / "frontend" / "dist" / "editor"
        target.mkdir(parents=True)
        (target / "form.schema.json").write_text("{nope", encoding="utf-8")
        with pytest.raises(EditorSchemaError, match="could not be read"):
            read_tool_schema(tmp_path, "editor/form.schema.json")
        write_schema(tmp_path, {"a": 1})
        with pytest.raises(EditorSchemaError, match="list of fields"):
            read_tool_schema(tmp_path, "editor/form.schema.json")

    def test_refuses_a_path_that_leaves_the_bundle(self, tmp_path):
        with pytest.raises(EditorSchemaError, match="outside"):
            read_tool_schema(tmp_path, "../../manifest.yml")


class TestValidateToolSchema:
    def test_accepts_known_types_and_nested_children(self):
        validate_tool_schema([{"type": "row", "name": "r", "children": [{"type": "slider", "name": "a"}]}, {"type": "checkbox", "name": "b"}], KNOWN, "tool")

    def test_names_the_field_with_an_unknown_type(self):
        with pytest.raises(EditorSchemaError, match=r"tool: field 'amount' has unknown field type 'wheel'"):
            validate_tool_schema([{"type": "slider", "name": "ok"}, {"type": "wheel", "name": "amount"}], KNOWN, "tool")

    def test_names_a_nested_field_with_an_unknown_type(self):
        with pytest.raises(EditorSchemaError, match="field 'deep' has unknown field type 'dial'"):
            validate_tool_schema([{"type": "row", "name": "r", "children": [{"type": "dial", "name": "deep"}]}], KNOWN, "tool")

    def test_refuses_a_field_without_a_type_and_a_repeated_name(self):
        with pytest.raises(EditorSchemaError, match="needs a 'type'"):
            validate_tool_schema([{"name": "a"}], KNOWN, "tool")
        with pytest.raises(EditorSchemaError, match="declared twice"):
            validate_tool_schema([{"type": "slider", "name": "a"}, {"type": "select", "name": "a"}], KNOWN, "tool")

    def test_skips_the_type_check_when_no_registry_is_wired(self):
        validate_tool_schema([{"type": "anything", "name": "a"}], None, "tool")


class TestEnableStage:
    def registry(self, tmp_path, types):
        fields = FieldTypeRegistry()
        for name in types:
            fields.register(FieldTypeDefinition(type_name=name))
        reg = PluginRegistry(marketplace_dir=str(tmp_path / "m"), local_dir=str(tmp_path / "l"))
        reg.field_registry = fields
        return reg

    def plugin(self, tmp_path):
        return SimpleNamespace(id="acme", plugin_dir=tmp_path, editor_tools=[{"id": "acme.form", "schema_file": "editor/form.schema.json"}])

    def test_passes_when_every_type_is_registered(self, tmp_path):
        write_schema(tmp_path, [{"type": "slider", "name": "a"}])
        assert self.registry(tmp_path, ["slider"])._validate_editor_tool_schemas(self.plugin(tmp_path)) is None

    def test_fails_naming_the_tool_and_the_field_for_an_unregistered_type(self, tmp_path):
        write_schema(tmp_path, [{"type": "wheel", "name": "amount"}])
        error = self.registry(tmp_path, ["slider"])._validate_editor_tool_schemas(self.plugin(tmp_path))
        assert "acme.form" in error and "amount" in error and "wheel" in error

    def test_a_plugin_without_schemas_is_untouched(self, tmp_path):
        bare = SimpleNamespace(id="acme", plugin_dir=tmp_path, editor_tools=[{"id": "acme.t", "schema_file": None}])
        assert self.registry(tmp_path, [])._validate_editor_tool_schemas(bare) is None
