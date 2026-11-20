import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.fields.builtin import register_builtin_fields
from src.features.fields.number_box import NumberBox
from src.features.fields.routes import FieldController, build_router
from src.platform.plugins.field_types import FieldTypeRegistry
from src.platform.security.current_user import get_current_active_user


def registry():
    built = FieldTypeRegistry()
    register_builtin_fields(built)
    return built


class TestRegistration(unittest.TestCase):

    def test_number_box_is_registered_with_its_class_and_component(self):
        definition = registry().get("number_box")

        self.assertIs(definition.schema_cls, NumberBox)
        self.assertEqual(definition.frontend_component, "core:NumberBoxField")
        self.assertTrue(definition.shareable)
        self.assertFalse(definition.container)

    def test_the_manifest_lists_its_configuration_keys(self):
        manifest = {entry["type"]: entry for entry in registry().frontend_manifest()}

        self.assertEqual(
            [spec["name"] for spec in manifest["number_box"]["configuration_schema"]],
            ["integer", "min", "max", "unit", "placeholder", "tooltip"],
        )

    def test_get_api_fields_types_serves_it(self):
        container = SimpleNamespace(field_controller=FieldController(registry()))
        app = FastAPI()
        app.include_router(build_router(container))
        app.dependency_overrides[get_current_active_user] = lambda: object()

        response = TestClient(app).get("/api/fields/types")

        by_type = {entry["type"]: entry for entry in response.json()["data"]}
        self.assertEqual(by_type["number_box"]["component"], "core:NumberBoxField")
        self.assertEqual(by_type["number_box"]["source"], "core")

    def test_it_documents_itself(self):
        self.assertTrue(NumberBox.description().strip())
        self.assertTrue(NumberBox.examples())
        for example in NumberBox.examples():
            self.assertEqual(example.rendered_output["type"], "number_box")
            self.assertTrue(example.yaml_config)


class TestNumberBox(unittest.TestCase):

    def setUp(self):
        self.field = NumberBox(Mock())

    def test_output_carries_the_declared_keys(self):
        schema = self.field.output({
            "type": "number_box",
            "name": "width",
            "label": "Width",
            "configuration": {
                "integer": True, "min": 1, "max": 8192, "unit": "px", "placeholder": "Auto", "tooltip": "Crop width",
            },
        })

        self.assertEqual(schema["type"], "number_box")
        self.assertEqual(schema["title"], "Width")
        self.assertIs(schema["integer"], True)
        self.assertEqual((schema["minimum"], schema["maximum"]), (1, 8192))
        self.assertEqual(schema["unit"], "px")
        self.assertEqual(schema["placeholder"], "Auto")
        self.assertEqual(schema["tooltip"], "Crop width")

    def test_output_omits_what_is_not_declared(self):
        schema = self.field.output({"type": "number_box", "name": "x", "configuration": {}})

        self.assertIs(schema["integer"], False)
        for key in ("minimum", "maximum", "unit", "placeholder"):
            self.assertNotIn(key, schema)

    def test_output_ignores_a_non_numeric_bound(self):
        schema = self.field.output({
            "type": "number_box", "name": "x", "configuration": {"min": "low", "max": True},
        })

        self.assertNotIn("minimum", schema)
        self.assertNotIn("maximum", schema)

    def test_input_accepts_numbers_and_numeric_text(self):
        self.assertEqual(self.field.input("x", 12.5, {}), 12.5)
        self.assertEqual(self.field.input("x", " 3.25 ", {}), 3.25)

    def test_input_with_integer_demands_a_whole_number(self):
        rules = {"integer": True}

        self.assertEqual(self.field.input("x", "7", rules), 7)
        self.assertEqual(self.field.input("x", 7.0, rules), 7)
        with self.assertRaises(ValueError):
            self.field.input("x", 7.5, rules)

    def test_input_enforces_the_range(self):
        rules = {"min": 1, "max": 10}

        self.assertEqual(self.field.input("x", 10, rules), 10.0)
        with self.assertRaises(ValueError):
            self.field.input("x", 0, rules)
        with self.assertRaises(ValueError):
            self.field.input("x", 11, rules)

    def test_input_rejects_what_is_not_a_number(self):
        for bad in ("abc", True, ["1"], float("inf")):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.field.input("x", bad, {})

    def test_input_passes_a_blank_through(self):
        self.assertIsNone(self.field.input("x", None, {}))
        self.assertEqual(self.field.input("x", "  ", {}), "  ")


if __name__ == "__main__":
    unittest.main()
