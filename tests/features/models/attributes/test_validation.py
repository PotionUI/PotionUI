"""`coerce_attribute_value` per `field_type` (mirrors the old
`ModelMetadataEditor._coerce_metadata_value` tests, but against the DB-backed
definition shape)."""

import unittest

from src.features.models.attributes.records import ModelAttributeDefinition
from src.features.models.attributes.validation import coerce_attribute_value
from src.features.models.exceptions import InvalidModelMetadataException


def _definition(field_type, **overrides):
    kwargs = dict(key="strength", label="Strength", field_type=field_type)
    kwargs.update(overrides)
    return ModelAttributeDefinition(**kwargs)


class TestCoerceAttributeValue(unittest.TestCase):
    def test_slider_within_range(self):
        definition = _definition("slider", config={"min": 0, "max": 2})
        self.assertEqual(coerce_attribute_value(definition, 0.8), 0.8)

    def test_slider_below_min_rejected_not_clamped(self):
        definition = _definition("slider", config={"min": 0, "max": 2})
        with self.assertRaises(InvalidModelMetadataException):
            coerce_attribute_value(definition, -1)

    def test_slider_above_max_rejected_not_clamped(self):
        definition = _definition("slider", config={"min": 0, "max": 2})
        with self.assertRaises(InvalidModelMetadataException):
            coerce_attribute_value(definition, 5.0)

    def test_number_wrong_type_rejected(self):
        definition = _definition("number")
        with self.assertRaises(InvalidModelMetadataException):
            coerce_attribute_value(definition, "not-a-number")

    def test_checkbox_requires_bool(self):
        definition = _definition("checkbox")
        self.assertIs(coerce_attribute_value(definition, True), True)
        with self.assertRaises(InvalidModelMetadataException):
            coerce_attribute_value(definition, "true")

    def test_select_requires_membership(self):
        definition = _definition("select", config={"options": [{"value": "a"}, {"value": "b"}]})
        self.assertEqual(coerce_attribute_value(definition, "a"), "a")
        with self.assertRaises(InvalidModelMetadataException):
            coerce_attribute_value(definition, "c")

    def test_tags_cleans_and_dedupes(self):
        definition = _definition("tags")
        result = coerce_attribute_value(definition, ["foo", " foo ", "", "bar", "  "])
        self.assertEqual(result, ["foo", "bar"])

    def test_tags_rejects_non_list(self):
        definition = _definition("tags")
        with self.assertRaises(InvalidModelMetadataException):
            coerce_attribute_value(definition, "foo")

    def test_tags_rejects_non_string_entries(self):
        definition = _definition("tags")
        with self.assertRaises(InvalidModelMetadataException):
            coerce_attribute_value(definition, [1, 2])

    def test_text_requires_string(self):
        definition = _definition("text")
        self.assertEqual(coerce_attribute_value(definition, "hello"), "hello")
        with self.assertRaises(InvalidModelMetadataException):
            coerce_attribute_value(definition, 5)


if __name__ == '__main__':
    unittest.main()
