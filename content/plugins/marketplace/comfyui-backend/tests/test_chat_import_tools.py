"""Tests for the comfyui-import chat mode's tools
(`backend.chat.tools.GetWorkflowInputsTool`, `.ProposeFormChangesTool`)."""

import asyncio
import json

import pytest

from src.plugin_api import ToolContext

from backend.chat.tools import GetWorkflowInputsTool, ProposeFormChangesTool


def _wiz(**overrides):
    base = {
        "workflow_name": "My Workflow",
        "format": "api",
        "node_count": 3,
        "candidates": [
            {
                "node_id": "3", "class_type": "KSampler", "node_title": "Sampler",
                "input_name": "cfg", "current_value": 7.5, "value_type": "float",
                "suggested_field_type": "float", "role": None, "locked": False,
            },
            {
                "node_id": "3", "class_type": "KSampler", "node_title": "Sampler",
                "input_name": "seed", "current_value": 42, "value_type": "int",
                "suggested_field_type": "seed", "role": "seed", "locked": False,
            },
            {
                "node_id": "6", "class_type": "CLIPTextEncode", "node_title": "Positive",
                "input_name": "text", "current_value": "a cat", "value_type": "str",
                "suggested_field_type": None, "role": "prompt_positive", "locked": True,
            },
        ],
        "form": {
            "tabs": [
                {
                    "id": "generation",
                    "label": "Generation",
                    "items": [
                        {
                            "kind": "field",
                            "field_name": "seed",
                            "field_type": "seed",
                            "label": "Seed",
                            "mappings": [{"node_id": "3", "input_name": "seed", "transform": "seed"}],
                        }
                    ],
                }
            ]
        },
        "mapped": [{"field_name": "seed", "node_id": "3", "input_name": "seed", "transform": "seed"}],
    }
    base.update(overrides)
    return base


def _context(wiz):
    return ToolContext(user_id="u1", session_metadata={"comfyui_import": wiz})


def run(coro):
    return asyncio.run(coro)


# --- GetWorkflowInputsTool -------------------------------------------------

def test_get_workflow_inputs_without_wizard_state_errors():
    tool = GetWorkflowInputsTool()
    result = run(tool.execute(ToolContext(user_id="u1"), filter=None, node_id=None))
    assert result.success is False
    assert "comfyui_import" in result.error


def test_get_workflow_inputs_filters_by_text_and_excludes_mapped_by_default():
    tool = GetWorkflowInputsTool()
    result = run(tool.execute(_context(_wiz()), filter="cfg", node_id=None))
    assert result.success is True
    payload = json.loads(result.data)
    names = [c["input_name"] for c in payload["candidates"]]
    assert names == ["cfg"]


def test_get_workflow_inputs_excludes_mapped_unless_included():
    tool = GetWorkflowInputsTool()
    result = run(tool.execute(_context(_wiz()), filter=None, node_id="3"))
    payload = json.loads(result.data)
    names = {c["input_name"] for c in payload["candidates"]}
    assert names == {"cfg"}  # seed is already mapped, excluded by default

    result_incl = run(tool.execute(_context(_wiz()), filter=None, node_id="3", include_mapped=True))
    payload_incl = json.loads(result_incl.data)
    names_incl = {c["input_name"] for c in payload_incl["candidates"]}
    assert names_incl == {"cfg", "seed"}


# --- ProposeFormChangesTool -------------------------------------------------

def test_propose_form_changes_valid_ops_returns_preview():
    tool = ProposeFormChangesTool()
    ops = [
        {"op": "add_tab", "label": "Sampling"},
        {
            "op": "add_field", "tab": "sampling", "field_type": "float",
            "field_name": "cfg_scale", "label": "CFG Scale",
            "mappings": [{"node_id": "3", "input_name": "cfg"}],
        },
    ]
    result = run(tool.execute(_context(_wiz()), ops=ops))
    assert result.success is True, result.error
    assert result.preview is not None
    assert result.preview.action == "Apply form changes"
    assert any('tab "Sampling"' in row for row in result.preview.items)
    assert any("cfg_scale" in row and "3.inputs.cfg" in row for row in result.preview.items)


def test_propose_form_changes_rejects_locked_candidate():
    tool = ProposeFormChangesTool()
    ops = [{"op": "map", "field_name": "seed", "node_id": "6", "input_name": "text"}]
    result = run(tool.execute(_context(_wiz()), ops=ops))
    assert result.success is False
    assert "locked" in result.error


def test_propose_form_changes_rejects_already_mapped_candidate():
    tool = ProposeFormChangesTool()
    # "3.inputs.seed" is already mapped to field "seed" (see _wiz's `mapped`);
    # add_field tries to claim it for a different field.
    ops = [
        {
            "op": "add_field", "tab": "generation", "field_type": "seed",
            "field_name": "seed2", "label": "Seed 2",
            "mappings": [{"node_id": "3", "input_name": "seed"}],
        }
    ]
    result = run(tool.execute(_context(_wiz()), ops=ops))
    assert result.success is False
    assert "already mapped" in result.error


def test_propose_form_changes_rejects_unknown_field():
    tool = ProposeFormChangesTool()
    ops = [{"op": "map", "field_name": "does_not_exist", "node_id": "3", "input_name": "cfg"}]
    result = run(tool.execute(_context(_wiz()), ops=ops))
    assert result.success is False
    assert "unknown field" in result.error


def test_propose_form_changes_all_or_nothing():
    """One bad op in the batch rejects the whole batch - the good op is not applied."""
    tool = ProposeFormChangesTool()
    ops = [
        {
            "op": "add_field", "tab": "generation", "field_type": "float",
            "field_name": "cfg_scale", "label": "CFG",
            "mappings": [{"node_id": "3", "input_name": "cfg"}],
        },
        {"op": "map", "field_name": "does_not_exist", "node_id": "3", "input_name": "cfg"},
    ]
    result = run(tool.execute(_context(_wiz()), ops=ops))
    assert result.success is False
    assert result.preview is None


def test_execute_confirmed_returns_apply_payload_with_defaulted_transform():
    tool = ProposeFormChangesTool()
    ops = [
        {"op": "add_tab", "label": "Sampling"},
        {
            "op": "add_field", "tab": "sampling", "field_type": "float",
            "field_name": "cfg_scale", "label": "CFG Scale",
            "mappings": [{"node_id": "3", "input_name": "cfg"}],
        },
    ]
    result = run(tool.execute_confirmed(_context(_wiz()), ops=ops))
    assert result.success is True, result.error
    payload = json.loads(result.data)
    assert payload["action"] == "apply_import_form_changes"

    add_tab_op = next(o for o in payload["ops"] if o["op"] == "add_tab")
    assert add_tab_op["id"] == "sampling"

    add_field_op = next(o for o in payload["ops"] if o["op"] == "add_field")
    assert add_field_op["mappings"] == [{"node_id": "3", "input_name": "cfg", "transform": "none"}]
