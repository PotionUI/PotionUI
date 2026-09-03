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


def test_propose_form_changes_normalizes_live_incident_payload():
    """A live incident payload: a model nested `field`,
    `tab_id`/`id`/`type`/`default_value`/`mapping` instead of
    `tab`/`field_name`/`field_type`/`default`/`mappings`, and field types
    named after webui/ComfyUI conventions (`wh`, `number`, `float`, `string`)
    instead of this tool's own vocabulary -- must normalize into 8 valid
    add_field ops rather than reject."""
    tool = ProposeFormChangesTool()
    wiz = _wiz(
        candidates=[
            {"node_id": "4", "class_type": "CheckpointLoaderSimple", "node_title": "Load Checkpoint",
             "input_name": "ckpt_name", "current_value": "v1-5-pruned-emaonly.safetensors",
             "value_type": "str", "suggested_field_type": "model", "role": None, "locked": False},
            {"node_id": "5", "class_type": "EmptyLatentImage", "node_title": "Empty Latent Image",
             "input_name": "width", "current_value": 512, "value_type": "int",
             "suggested_field_type": "resolution", "role": "resolution_width", "locked": False},
            {"node_id": "5", "class_type": "EmptyLatentImage", "node_title": "Empty Latent Image",
             "input_name": "height", "current_value": 512, "value_type": "int",
             "suggested_field_type": "resolution", "role": "resolution_height", "locked": False},
            {"node_id": "3", "class_type": "KSampler", "node_title": "KSampler",
             "input_name": "seed", "current_value": 0, "value_type": "int",
             "suggested_field_type": "seed", "role": "seed", "locked": False},
            {"node_id": "3", "class_type": "KSampler", "node_title": "KSampler",
             "input_name": "steps", "current_value": 20, "value_type": "int",
             "suggested_field_type": "slider", "role": "steps", "locked": False},
            {"node_id": "3", "class_type": "KSampler", "node_title": "KSampler",
             "input_name": "cfg", "current_value": 7.0, "value_type": "float",
             "suggested_field_type": "slider", "role": "cfg", "locked": False},
            {"node_id": "3", "class_type": "KSampler", "node_title": "KSampler",
             "input_name": "sampler_name", "current_value": "euler", "value_type": "str",
             "suggested_field_type": "select", "role": "sampler", "locked": False},
            {"node_id": "3", "class_type": "KSampler", "node_title": "KSampler",
             "input_name": "scheduler", "current_value": "normal", "value_type": "str",
             "suggested_field_type": "select", "role": "scheduler", "locked": False},
            {"node_id": "3", "class_type": "KSampler", "node_title": "KSampler",
             "input_name": "denoise", "current_value": 1.0, "value_type": "float",
             "suggested_field_type": "slider", "role": "denoise", "locked": False},
        ],
        form={"tabs": [{"id": "main", "label": "Main", "items": []}]},
        mapped=[],
    )
    ops = [
        {"op": "add_field", "tab_id": "main", "field": {
            "id": "model", "label": "Model", "type": "model",
            "default_value": "v1-5-pruned-emaonly.safetensors",
            "mapping": [{"node_id": "4", "input_name": "ckpt_name", "transform": "strip_model_prefix"}],
        }},
        {"op": "add_field", "tab_id": "main", "field": {
            "id": "size", "label": "Size", "type": "wh", "default_value": [512, 512],
            "mapping": [
                {"node_id": "5", "input_name": "width", "transform": "split_wh_width"},
                {"node_id": "5", "input_name": "height", "transform": "split_wh_height"},
            ],
        }},
        {"op": "add_field", "tab_id": "main", "field": {
            "id": "seed", "label": "Seed", "type": "seed", "default_value": 0,
            "mapping": [{"node_id": "3", "input_name": "seed"}],
        }},
        {"op": "add_field", "tab_id": "main", "field": {
            "id": "steps", "label": "Steps", "type": "number", "default_value": 20,
            "mapping": [{"node_id": "3", "input_name": "steps"}],
        }},
        {"op": "add_field", "tab_id": "main", "field": {
            "id": "cfg_scale", "label": "CFG Scale", "type": "float", "default_value": 7.0,
            "mapping": [{"node_id": "3", "input_name": "cfg"}],
        }},
        {"op": "add_field", "tab_id": "main", "field": {
            "id": "sampler_name", "label": "Sampler", "type": "string", "default_value": "euler",
            "mapping": [{"node_id": "3", "input_name": "sampler_name"}],
        }},
        {"op": "add_field", "tab_id": "main", "field": {
            "id": "scheduler", "label": "Scheduler", "type": "string", "default_value": "normal",
            "mapping": [{"node_id": "3", "input_name": "scheduler"}],
        }},
        {"op": "add_field", "tab_id": "main", "field": {
            "id": "denoise", "label": "Denoise", "type": "float", "default_value": 1.0,
            "mapping": [{"node_id": "3", "input_name": "denoise"}],
        }},
    ]

    result = run(tool.execute(_context(wiz), ops=ops))
    assert result.success is True, result.error
    assert len(result.preview.items) == 8


def test_propose_form_changes_unknown_field_type_teaches_allowed_types():
    tool = ProposeFormChangesTool()
    ops = [{
        "op": "add_field", "tab": "generation", "field_type": "wobble",
        "field_name": "mystery", "label": "Mystery",
        "mappings": [{"node_id": "3", "input_name": "cfg"}],
    }]
    result = run(tool.execute(_context(_wiz()), ops=ops))
    assert result.success is False
    assert "unknown field_type 'wobble'" in result.error
    assert "resolution" in result.error and "select" in result.error


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


# --- ProposeFormChangesTool: lora_picker op --------------------------------


def _wiz_with_lora_chain(**overrides):
    return _wiz(
        lora_chain={
            "nodes": [
                {"node_id": "101", "class_type": "LoraLoaderModelOnly", "lora_name": "style_a.safetensors", "strength_model": 0.8},
                {"node_id": "102", "class_type": "LoraLoaderModelOnly", "lora_name": "style_b.safetensors", "strength_model": 0.6},
            ],
            "replaced": [],
            "kept": [],
        },
        **overrides,
    )


def test_lora_picker_op_converts_the_whole_chain_by_default():
    tool = ProposeFormChangesTool()
    ops = [{"op": "lora_picker", "tab": "generation"}]
    result = run(tool.execute(_context(_wiz_with_lora_chain()), ops=ops))
    assert result.success is True, result.error
    assert "+ LoRA picker (2 LoRAs seeded)" in result.preview.items[0]

    confirmed = run(tool.execute_confirmed(_context(_wiz_with_lora_chain()), ops=ops))
    payload = json.loads(confirmed.data)
    op = payload["ops"][0]
    assert op == {"op": "lora_picker", "tab": "generation", "field_name": "loras", "keep_fixed": []}


def test_lora_picker_op_with_keep_fixed_reports_kept_node_in_preview():
    tool = ProposeFormChangesTool()
    ops = [{"op": "lora_picker", "tab": "generation", "keep_fixed": ["101"]}]
    result = run(tool.execute(_context(_wiz_with_lora_chain()), ops=ops))
    assert result.success is True, result.error
    assert "+ LoRA picker (1 LoRAs seeded, node 101 kept fixed)" in result.preview.items[0]


def test_lora_picker_op_without_a_detected_chain_is_rejected():
    tool = ProposeFormChangesTool()
    ops = [{"op": "lora_picker", "tab": "generation"}]
    result = run(tool.execute(_context(_wiz()), ops=ops))  # no lora_chain in this wizard state
    assert result.success is False
    assert "no LoRA chain detected" in result.error


def test_lora_picker_op_refuses_a_second_picker():
    tool = ProposeFormChangesTool()
    wiz = _wiz_with_lora_chain()
    wiz["form"]["tabs"][0]["items"].append(
        {"kind": "field", "field_name": "loras", "field_type": "lora_picker", "label": "LoRAs", "mappings": []}
    )
    ops = [{"op": "lora_picker", "tab": "generation"}]
    result = run(tool.execute(_context(wiz), ops=ops))
    assert result.success is False
    assert "already exists" in result.error


def test_lora_picker_op_rejects_an_unknown_keep_fixed_node():
    tool = ProposeFormChangesTool()
    ops = [{"op": "lora_picker", "tab": "generation", "keep_fixed": ["999"]}]
    result = run(tool.execute(_context(_wiz_with_lora_chain()), ops=ops))
    assert result.success is False
    assert "not in the detected chain" in result.error
