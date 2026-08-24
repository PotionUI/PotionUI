"""Tests for src/core/automation/nodes/conditions.py's execute() implementations, focused on condition.switch
and condition.path_match's `has_dir` operator."""

import unittest

from src.features.automation.context import AutomationServices, NodeExecutionContext, RunContext
from src.features.automation.nodes.conditions import _execute_path_match, _execute_switch


def _ctx(config: dict, event: dict, upstream: dict = None, node_type: str = "condition.switch") -> NodeExecutionContext:
    run = RunContext(automation_id="auto1", run_id="run1", event=event, services=AutomationServices())
    run.upstream = upstream or {}
    return NodeExecutionContext(run=run, node_id="n1", node_type=node_type, config=config)


class TestExecuteSwitch(unittest.IsolatedAsyncioTestCase):

    async def test_matching_case_routes_to_that_case_branch(self):
        result = await _execute_switch(_ctx(
            config={"field": "event.model_type", "cases": "loras, checkpoints, vae"},
            event={"model_type": "loras"},
        ))

        self.assertEqual(result.branch, "loras")
        self.assertTrue(result.output["matched"])
        self.assertEqual(result.output["value"], "loras")

    async def test_no_match_routes_to_default(self):
        result = await _execute_switch(_ctx(
            config={"field": "event.model_type", "cases": "loras, checkpoints, vae"},
            event={"model_type": "embeddings"},
        ))

        self.assertEqual(result.branch, "default")
        self.assertFalse(result.output["matched"])

    async def test_missing_field_value_routes_to_default(self):
        result = await _execute_switch(_ctx(
            config={"field": "event.does_not_exist", "cases": "a, b"},
            event={},
        ))

        self.assertEqual(result.branch, "default")
        self.assertEqual(result.output["value"], "")

    async def test_cases_are_whitespace_trimmed(self):
        result = await _execute_switch(_ctx(
            config={"field": "event.kind", "cases": "  loras ,checkpoints,  vae  "},
            event={"kind": "checkpoints"},
        ))

        self.assertEqual(result.branch, "checkpoints")
        self.assertTrue(result.output["matched"])

    async def test_actual_value_is_trimmed_before_matching(self):
        result = await _execute_switch(_ctx(
            config={"field": "event.kind", "cases": "loras, checkpoints"},
            event={"kind": "  loras  "},
        ))

        self.assertEqual(result.branch, "loras")

    async def test_numeric_payload_is_str_cast_before_matching(self):
        result = await _execute_switch(_ctx(
            config={"field": "event.status_code", "cases": "200, 404, 500"},
            event={"status_code": 404},
        ))

        self.assertEqual(result.branch, "404")
        self.assertTrue(result.output["matched"])

    async def test_float_payload_is_str_cast(self):
        result = await _execute_switch(_ctx(
            config={"field": "event.score", "cases": "1.5, 2.0"},
            event={"score": 1.5},
        ))

        self.assertEqual(result.branch, "1.5")

    async def test_empty_cases_always_routes_to_default(self):
        result = await _execute_switch(_ctx(
            config={"field": "event.kind", "cases": ""},
            event={"kind": "anything"},
        ))

        self.assertEqual(result.branch, "default")

    async def test_resolves_against_upstream_too(self):
        result = await _execute_switch(_ctx(
            config={"field": "upstream.n_prev.model_type", "cases": "lora, checkpoint"},
            event={},
            upstream={"n_prev": {"model_type": "lora"}},
        ))

        self.assertEqual(result.branch, "lora")


class TestExecutePathMatchHasDir(unittest.IsolatedAsyncioTestCase):

    async def test_matches_a_whole_path_segment(self):
        result = await _execute_path_match(_ctx(
            config={"field": "event.path", "match_type": "has_dir", "value": "krea2"},
            event={"path": "/models/loras/krea2/style.safetensors"},
            node_type="condition.path_match",
        ))

        self.assertTrue(result.output["passed"])
        self.assertEqual(result.branch, "true")

    async def test_does_not_match_a_longer_segment_sharing_a_prefix(self):
        """'krea2' must not match 'krea25' - a substring match would be wrong here."""
        result = await _execute_path_match(_ctx(
            config={"field": "event.path", "match_type": "has_dir", "value": "krea2"},
            event={"path": "/models/loras/krea25/style.safetensors"},
            node_type="condition.path_match",
        ))

        self.assertFalse(result.output["passed"])
        self.assertEqual(result.branch, "false")

    async def test_matches_any_of_several_comma_separated_names(self):
        result = await _execute_path_match(_ctx(
            config={"field": "event.path", "match_type": "has_dir", "value": "vae, krea2, checkpoints"},
            event={"path": "/models/loras/krea2/style.safetensors"},
            node_type="condition.path_match",
        ))

        self.assertTrue(result.output["passed"])

    async def test_matches_against_a_list_field_like_rel_parts(self):
        result = await _execute_path_match(_ctx(
            config={"field": "event.rel_parts", "match_type": "has_dir", "value": "krea2"},
            event={"rel_parts": ["krea2", "style.safetensors"]},
            node_type="condition.path_match",
        ))

        self.assertTrue(result.output["passed"])

    async def test_empty_value_never_matches(self):
        result = await _execute_path_match(_ctx(
            config={"field": "event.path", "match_type": "has_dir", "value": ""},
            event={"path": "/models/loras/krea2/style.safetensors"},
            node_type="condition.path_match",
        ))

        self.assertFalse(result.output["passed"])


if __name__ == '__main__':
    unittest.main()
