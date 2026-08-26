"""Tests for register_builtin_fields (src/core/fields/builtin.py)."""

import re
import unittest
from pathlib import Path
from unittest.mock import Mock

from src.features.fields.builtin import register_builtin_fields
from src.features.forms.operations import get_checkbox_options, get_model_database_options
from src.platform.plugins.field_types import FieldTypeRegistry


class TestRegisterBuiltinFields(unittest.TestCase):

    def test_registers_without_form_manager(self):
        registry = FieldTypeRegistry()
        register_builtin_fields(registry)

        select_def = registry.get('select')
        self.assertIsNotNone(select_def.schema_cls)
        self.assertIsNone(select_def.options_provider)

    def test_registers_options_providers_when_template_processor_given(self):
        registry = FieldTypeRegistry()
        template_processor = Mock()
        template_processor.process_template.return_value = "/does/not/exist.yml"

        register_builtin_fields(registry, template_processor=template_processor)

        # `select`'s loader needs `template_processor` to resolve a templated
        # `file.path`/`files.in` - bound via `functools.partial` so the
        # registry keeps a single-arg `(config) -> options` callable.
        select_def = registry.get('select')
        self.assertEqual(
            select_def.options_provider({"file": {"path": "x.yml"}}),
            [],  # a missing options file is logged and skipped, not an error
        )
        template_processor.process_template.assert_called_once_with("x.yml", {})

        # `model`/`checkbox_group` need no collaborator - wired to the bare functions.
        model_def = registry.get('model')
        self.assertIs(model_def.options_provider, get_model_database_options)

        checkbox_group_def = registry.get('checkbox_group')
        self.assertIs(checkbox_group_def.options_provider, get_checkbox_options)

    def test_lora_picker_registered(self):
        registry = FieldTypeRegistry()
        register_builtin_fields(registry)

        lora_picker_def = registry.get('lora_picker')
        self.assertIsNotNone(lora_picker_def.schema_cls)
        self.assertEqual(lora_picker_def.frontend_component, 'core:LoraPickerField')
        self.assertIsNone(lora_picker_def.options_provider)

    def test_container_types_share_container_schema_class(self):
        registry = FieldTypeRegistry()
        register_builtin_fields(registry)

        container_types = ['tabs', 'tab', 'row', 'group', 'accordion']
        schema_classes = {registry.get(t).schema_cls for t in container_types}
        self.assertEqual(len(schema_classes), 1)
        for t in container_types:
            self.assertTrue(registry.get(t).container)

    def test_no_frontend_rendered_type_is_unregistered(self):
        """Every field type branch in FormField.svelte must exist in the
        registry, so the future frontend registry (A4) can drive itself off
        `frontend_manifest()` instead of the hardcoded branches."""
        registry = FieldTypeRegistry()
        register_builtin_fields(registry)

        svelte_path = (
            Path(__file__).resolve().parents[3]
            / "frontend/src/lib/components/form-fields/FormField.svelte"
        )
        source = svelte_path.read_text()
        rendered_types = set(re.findall(r"fieldType === '([a-z_]+)'", source))

        self.assertTrue(rendered_types, "expected to find rendered field types in FormField.svelte")

        registered_types = {d.type_name for d in registry.all()}
        missing = rendered_types - registered_types
        self.assertEqual(missing, set(), f"types rendered by the frontend but missing from the registry: {missing}")


if __name__ == '__main__':
    unittest.main()
