"""Tests for src/core/automation/expr.py - get_path, operator table, jinja sandbox."""

import unittest

from src.features.automation.expr import (
    ExpressionError,
    OPERATORS,
    apply_operator,
    eval_expression,
    get_path,
    render_template,
)


class TestGetPath(unittest.TestCase):

    def test_dict_path(self):
        data = {"event": {"path": "/models/loras/krea.safetensors"}}
        self.assertEqual(get_path(data, "event.path"), "/models/loras/krea.safetensors")

    def test_list_index_dotted(self):
        data = {"parts": ["models", "loras", "krea.safetensors"]}
        self.assertEqual(get_path(data, "parts.1"), "loras")

    def test_list_index_bracket_form(self):
        data = {"event": {"tags": ["a", "b", "c"]}}
        self.assertEqual(get_path(data, "event.tags[2]"), "c")

    def test_negative_list_index(self):
        data = {"parts": ["a", "b", "c"]}
        self.assertEqual(get_path(data, "parts.-1"), "c")

    def test_missing_key_returns_default(self):
        self.assertIsNone(get_path({"a": 1}, "b.c"))
        self.assertEqual(get_path({"a": 1}, "b.c", default="fallback"), "fallback")

    def test_missing_intermediate_returns_default(self):
        self.assertIsNone(get_path({"a": None}, "a.b"))

    def test_out_of_range_index_returns_default(self):
        self.assertIsNone(get_path({"a": [1, 2]}, "a.5"))

    def test_object_attribute_access(self):
        class Obj:
            def __init__(self):
                self.foo = "bar"

        self.assertEqual(get_path({"obj": Obj()}, "obj.foo"), "bar")

    def test_empty_path_returns_default(self):
        self.assertIsNone(get_path({"a": 1}, ""))


class TestOperators(unittest.TestCase):

    def test_equals(self):
        self.assertTrue(apply_operator("equals", "a", "a"))
        self.assertFalse(apply_operator("equals", "a", "b"))

    def test_not_equals(self):
        self.assertTrue(apply_operator("not_equals", "a", "b"))
        self.assertFalse(apply_operator("not_equals", "a", "a"))

    def test_contains(self):
        self.assertTrue(apply_operator("contains", "krea_lora.safetensors", "krea"))
        self.assertFalse(apply_operator("contains", "other.safetensors", "krea"))
        self.assertTrue(apply_operator("contains", ["a", "b"], "b"))

    def test_contains_handles_non_iterable(self):
        self.assertFalse(apply_operator("contains", None, "x"))
        self.assertFalse(apply_operator("contains", 42, "x"))

    def test_not_contains(self):
        self.assertTrue(apply_operator("not_contains", "abc", "z"))
        self.assertFalse(apply_operator("not_contains", "abc", "a"))

    def test_gt_gte_lt_lte(self):
        self.assertTrue(apply_operator("gt", 5, 3))
        self.assertFalse(apply_operator("gt", 3, 5))
        self.assertTrue(apply_operator("gte", 3, 3))
        self.assertTrue(apply_operator("lt", 2, 3))
        self.assertTrue(apply_operator("lte", 3, 3))

    def test_numeric_operators_non_numeric_input_is_false_not_raise(self):
        self.assertFalse(apply_operator("gt", "not-a-number", 3))
        self.assertFalse(apply_operator("gt", None, 3))

    def test_starts_with_ends_with(self):
        self.assertTrue(apply_operator("starts_with", "krea_v2.safetensors", "krea"))
        self.assertTrue(apply_operator("ends_with", "krea_v2.safetensors", ".safetensors"))
        self.assertFalse(apply_operator("starts_with", "other.safetensors", "krea"))

    def test_regex(self):
        self.assertTrue(apply_operator("regex", "krea_v2.safetensors", r"krea_v\d"))
        self.assertFalse(apply_operator("regex", "other.safetensors", r"krea_v\d"))

    def test_regex_invalid_pattern_is_false_not_raise(self):
        self.assertFalse(apply_operator("regex", "abc", "("))

    def test_regex_length_capped(self):
        huge = "a" * 5000
        self.assertFalse(apply_operator("regex", huge, "a"))

    def test_is_empty_is_not_empty(self):
        self.assertTrue(apply_operator("is_empty", ""))
        self.assertTrue(apply_operator("is_empty", []))
        self.assertTrue(apply_operator("is_empty", None))
        self.assertFalse(apply_operator("is_empty", "x"))
        self.assertTrue(apply_operator("is_not_empty", "x"))

    def test_unknown_operator_raises(self):
        with self.assertRaises(ExpressionError):
            apply_operator("does_not_exist", "a", "b")

    def test_operator_table_is_pure_functions_no_eval(self):
        # Every operator must be a plain callable, never a string later passed to eval().
        for name, op in OPERATORS.items():
            self.assertTrue(callable(op), f"operator '{name}' is not callable")


class TestJinjaSandbox(unittest.TestCase):

    def test_eval_simple_boolean_expression(self):
        result = eval_expression("'krea' in event.parts[2]", {"event": {"parts": ["models", "loras", "krea.safetensors"]}})
        self.assertTrue(result)

    def test_eval_arithmetic_expression(self):
        result = eval_expression("upstream.n1.size > 1000", {"upstream": {"n1": {"size": 2000}}})
        self.assertTrue(result)

    def test_invalid_expression_raises_expression_error(self):
        with self.assertRaises(ExpressionError):
            eval_expression("this is not : valid jinja &&&", {})

    def test_sandbox_blocks_attribute_escape(self):
        # Sandboxed environments block access to unsafe dunder/class attributes.
        with self.assertRaises((ExpressionError, Exception)):
            eval_expression("event.__class__.__mro__", {"event": {}})

    def test_render_template_with_context(self):
        rendered = render_template("{{ event.path }}", {"event": {"path": "/models/loras/x.safetensors"}})
        self.assertEqual(rendered, "/models/loras/x.safetensors")

    def test_render_template_invalid_raises(self):
        with self.assertRaises(ExpressionError):
            render_template("{{ unterminated", {})


if __name__ == '__main__':
    unittest.main()
