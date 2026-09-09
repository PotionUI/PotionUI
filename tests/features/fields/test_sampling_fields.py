"""Tests for SamplerField / ScheduleField (`src/features/fields/sampling_fields.py`)."""

import unittest
from unittest.mock import Mock, patch

from src.features.fields.sampling_fields import SamplerField, ScheduleField
from src.features.fields.field_factory import FieldFactory
from src.platform.plugins.field_types import FieldTypeRegistry
from src.platform.runtime.native.sampling.registry import (
    SamplingRegistry,
    SamplerDefinition,
    ScheduleDefinition,
)


def _samplers(*entries):
    reg = SamplingRegistry("sampler")
    for key, families in entries:
        reg.register(SamplerDefinition(key=key, sample=lambda *a, **kw: None, label=key.title(), families=families))
    return reg


def _schedules(*entries):
    reg = SamplingRegistry("schedule")
    for key, families in entries:
        reg.register(ScheduleDefinition(key=key, build=lambda ctx: None, label=key.title(), families=families))
    return reg


class TestSamplerField(unittest.TestCase):
    def setUp(self):
        self.preset_loader = Mock()
        self.sampler_field = SamplerField(self.preset_loader)

    def test_can_handle(self):
        self.assertTrue(self.sampler_field.can_handle("sampler"))
        self.assertFalse(self.sampler_field.can_handle("schedule"))

    def test_options_come_from_registry_family_filter(self):
        registry = _samplers(
            ("euler", ("*",)),
            ("krea2_only", ("krea2",)),
            ("flux_only", ("flux",)),
        )
        field = {
            "type": "sampler",
            "name": "sampler",
            "label": "Sampler",
            "configuration": {"family": "krea2"},
        }
        with patch("src.features.fields.sampling_fields.sampler_registry", registry):
            schema = self.sampler_field.output(field)

        values = {o["value"] for o in schema["options"]}
        self.assertEqual(values, {"euler", "krea2_only"})
        self.assertNotIn("flux_only", values)

    def test_include_narrows_and_preserves_order(self):
        registry = _samplers(
            ("a", ("*",)),
            ("b", ("*",)),
            ("c", ("*",)),
        )
        field = {
            "type": "sampler",
            "name": "sampler",
            "configuration": {"include": ["c", "a"]},
        }
        with patch("src.features.fields.sampling_fields.sampler_registry", registry):
            schema = self.sampler_field.output(field)

        self.assertEqual([o["value"] for o in schema["options"]], ["c", "a"])

    def test_exclude_drops_keys(self):
        registry = _samplers(("a", ("*",)), ("b", ("*",)))
        field = {
            "type": "sampler",
            "name": "sampler",
            "configuration": {"exclude": ["b"]},
        }
        with patch("src.features.fields.sampling_fields.sampler_registry", registry):
            schema = self.sampler_field.output(field)

        self.assertEqual([o["value"] for o in schema["options"]], ["a"])

    def test_allow_empty_passthrough(self):
        registry = _samplers(("a", ("*",)))
        field = {
            "type": "sampler",
            "name": "sampler",
            "configuration": {"allow_empty": True},
        }
        with patch("src.features.fields.sampling_fields.sampler_registry", registry):
            schema = self.sampler_field.output(field)

        self.assertEqual(schema["configuration"], {"allow_empty": True})

    def test_allow_empty_defaults_false(self):
        registry = _samplers(("a", ("*",)))
        field = {"type": "sampler", "name": "sampler", "configuration": {}}

        with patch("src.features.fields.sampling_fields.sampler_registry", registry):
            schema = self.sampler_field.output(field)

        self.assertEqual(schema["configuration"], {"allow_empty": False})

    def test_configuration_specs(self):
        names = {spec.name for spec in SamplerField.configuration()}
        self.assertEqual(names, {"family", "include", "exclude", "allow_empty"})


class TestScheduleField(unittest.TestCase):
    def setUp(self):
        self.preset_loader = Mock()
        self.schedule_field = ScheduleField(self.preset_loader)

    def test_can_handle(self):
        self.assertTrue(self.schedule_field.can_handle("schedule"))
        self.assertFalse(self.schedule_field.can_handle("sampler"))

    def test_options_come_from_registry_family_filter(self):
        registry = _schedules(
            ("simple", ("*",)),
            ("ltx_dynamic", ("ltx",)),
        )
        field = {
            "type": "schedule",
            "name": "schedule",
            "configuration": {"family": "ltx"},
        }
        with patch("src.features.fields.sampling_fields.schedule_registry", registry):
            schema = self.schedule_field.output(field)

        values = {o["value"] for o in schema["options"]}
        self.assertEqual(values, {"simple", "ltx_dynamic"})

    def test_unknown_family_excludes_family_scoped_entries(self):
        registry = _schedules(("ltx_dynamic", ("ltx",)))
        field = {
            "type": "schedule",
            "name": "schedule",
            "configuration": {"family": "totally_unknown"},
        }
        with patch("src.features.fields.sampling_fields.schedule_registry", registry):
            schema = self.schedule_field.output(field)

        self.assertEqual(schema["options"], [])


class TestSamplingFieldsThroughFieldFactory(unittest.TestCase):
    """Form-render-level: the field factory dispatches `sampler`/`schedule`
    types to these classes exactly like it dispatches `select` (see
    tests/features/fields/test_field_factory.py::test_map_field_select)."""

    def setUp(self):
        self.preset_loader = Mock()
        registry = FieldTypeRegistry()
        from src.features.fields.builtin import register_builtin_fields
        register_builtin_fields(registry)
        self.field_factory = FieldFactory(self.preset_loader, field_registry=registry)

    def test_map_field_sampler(self):
        fixture = _samplers(("euler", ("*",)))
        field = {"type": "sampler", "name": "sampler", "configuration": {}}

        with patch("src.features.fields.sampling_fields.sampler_registry", fixture):
            schema = self.field_factory.map_field(field)

        self.assertEqual(schema["type"], "sampler")
        self.assertEqual([o["value"] for o in schema["options"]], ["euler"])

    def test_map_field_schedule(self):
        fixture = _schedules(("simple", ("*",)))
        field = {"type": "schedule", "name": "schedule", "configuration": {}}

        with patch("src.features.fields.sampling_fields.schedule_registry", fixture):
            schema = self.field_factory.map_field(field)

        self.assertEqual(schema["type"], "schedule")
        self.assertEqual([o["value"] for o in schema["options"]], ["simple"])


if __name__ == "__main__":
    unittest.main()
