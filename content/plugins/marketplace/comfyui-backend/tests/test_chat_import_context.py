"""Tests for the comfyui-import chat mode's context contributor
(`backend.chat.context.build_import_context`)."""

from backend.chat.context import build_import_context, _MAX_BLOCK_CHARS, _MAX_UNMAPPED_LINES


def _wiz(**overrides):
    base = {
        "workflow_name": "My Workflow",
        "format": "api",
        "node_count": 3,
        "candidates": [],
        "form": {"tabs": []},
        "mapped": [],
    }
    base.update(overrides)
    return base


def test_returns_none_without_wizard_state():
    assert build_import_context({}, session=None, user_id="u1") is None
    assert build_import_context({"comfyui_import": "not-a-dict"}, session=None, user_id="u1") is None


def test_renders_workflow_summary_and_form():
    wiz = _wiz(
        workflow_name="SDXL basic",
        format="ui",
        node_count=12,
        form={
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
                        },
                        {
                            "kind": "row",
                            "columns": 2,
                            "items": [
                                {
                                    "kind": "field",
                                    "field_name": "cfg_scale",
                                    "field_type": "float",
                                    "label": "CFG",
                                    "mappings": [],
                                }
                            ],
                        },
                    ],
                }
            ]
        },
    )
    block = build_import_context({"comfyui_import": wiz}, session=None, user_id="u1")

    assert 'SDXL basic' in block
    assert "ui format, 12 nodes" in block
    assert 'Tab "Generation"' in block
    assert "seed (seed) ← 3.inputs.seed [seed]" in block
    assert "cfg_scale (float): unmapped" in block


def test_unmapped_candidates_grouped_by_node_excludes_locked_and_mapped():
    wiz = _wiz(
        candidates=[
            {
                "node_id": "3", "class_type": "KSampler", "node_title": "Sampler",
                "input_name": "cfg", "current_value": 7.5, "value_type": "float",
                "suggested_field_type": "float", "role": None, "locked": False,
            },
            {
                "node_id": "3", "class_type": "KSampler", "node_title": "Sampler",
                "input_name": "steps", "current_value": 20, "value_type": "int",
                "suggested_field_type": "int", "role": None, "locked": False,
            },
            {
                "node_id": "6", "class_type": "CLIPTextEncode", "node_title": "Positive",
                "input_name": "text", "current_value": "a cat", "value_type": "str",
                "suggested_field_type": None, "role": "prompt_positive", "locked": True,
            },
            {
                "node_id": "9", "class_type": "KSamplerSelect", "node_title": "Sampler name",
                "input_name": "sampler_name", "current_value": "euler", "value_type": "str",
                "suggested_field_type": "string", "role": None, "locked": False,
            },
        ],
        mapped=[{"field_name": "sampler", "node_id": "9", "input_name": "sampler_name", "transform": "none"}],
    )
    block = build_import_context({"comfyui_import": wiz}, session=None, user_id="u1")

    # cfg/steps on node 3 grouped onto one line
    assert '3 KSampler "Sampler": cfg=7.5 (float), steps=20 (int)' in block
    # locked prompt input never appears
    assert "a cat" not in block
    assert "CLIPTextEncode" not in block
    # already-mapped candidate never appears in the unmapped section
    assert "sampler_name=euler" not in block


def test_unmapped_section_is_capped_and_budget_bounded():
    candidates = [
        {
            "node_id": str(i), "class_type": "SomeNode", "node_title": f"Node {i}",
            "input_name": "value", "current_value": i, "value_type": "int",
            "suggested_field_type": "int", "role": None, "locked": False,
        }
        for i in range(100)
    ]
    wiz = _wiz(candidates=candidates)
    block = build_import_context({"comfyui_import": wiz}, session=None, user_id="u1")

    assert len(block) <= _MAX_BLOCK_CHARS
    node_lines = [ln for ln in block.splitlines() if ln.startswith("  ") and "SomeNode" in ln]
    assert len(node_lines) <= _MAX_UNMAPPED_LINES
    assert "more, use get_workflow_inputs" in block
