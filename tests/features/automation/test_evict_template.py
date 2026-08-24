"""The shipped 'Free VRAM before a generation (automatic)' template:
JSON validity + the automatic evict expression's truth table.
"""

import json
from pathlib import Path

import pytest

from src.features.automation.expr import eval_expression

_TEMPLATE = (
    Path(__file__).resolve().parents[3]
    / "content/automation/marketplace/evict-ollama-before-generation.json"
)


def _load():
    return json.loads(_TEMPLATE.read_text(encoding="utf-8"))


def _expression():
    doc = _load()
    nodes = {n["id"]: n for n in doc["automation"]["graph"]["nodes"]}
    return nodes["needs_room"]["config"]["expression"]


def test_template_json_is_valid_and_wired():
    doc = _load()
    assert doc["schema"] == "potionui.automation"
    graph = doc["automation"]["graph"]
    types_ = {n["type"] for n in graph["nodes"]}
    assert types_ == {"trigger.hook_event", "condition.jinja_expression", "action.ollama_unload"}

    trigger = next(n for n in graph["nodes"] if n["type"] == "trigger.hook_event")
    assert trigger["config"]["hook_name"] == "generation.before_start"
    assert trigger["config"]["wait_for_completion"] is True

    # The evict path leaves the condition's "true" handle.
    evict_edge = next(e for e in graph["edges"] if e["target"] == "unload_ollama")
    assert evict_edge["source_handle"] == "true"


@pytest.mark.parametrize("event,should_evict", [
    ({"vram_estimate_gb": None, "vram_free_gb": 10.0}, True),   # need unknown -> clear the room
    ({"vram_estimate_gb": 12.0, "vram_free_gb": 10.0}, True),   # need exceeds free
    ({"vram_estimate_gb": 5.0, "vram_free_gb": 10.0}, False),   # fits -> keep chat warm
    ({"vram_estimate_gb": 5.0, "vram_free_gb": None}, True),    # free unknown -> clear
    ({"vram_estimate_gb": None, "vram_free_gb": None}, True),
])
def test_automatic_expression_truth_table(event, should_evict):
    assert bool(eval_expression(_expression(), {"event": event})) is should_evict
