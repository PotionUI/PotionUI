import dataclasses
import json
from pathlib import Path

import pytest

from src.features.llm.tools.base import ToolContext, ToolResult, ToolServices
from src.features.llm.tools.builtin import register_builtin_tools
from src.features.llm.tools.registry import ToolRegistry
from src.features.mcp.catalog import build_catalog

BASELINE = json.loads((Path(__file__).parent / "data" / "baseline_tool_surface.json").read_text())


@pytest.fixture(scope="module")
def current():
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return {entry.name: entry for entry in build_catalog(registry)}


@pytest.mark.parametrize("name", sorted(BASELINE["tools"]))
def test_a_released_tool_keeps_its_name_flags_and_arguments(current, name):
    before = BASELINE["tools"][name]

    assert name in current
    entry = current[name]
    assert (entry.group, entry.writes, entry.spends, entry.unavailable_reason) == (
        before["group"], before["writes"], before["spends"], before["unavailable"],
    )
    if before["parameters"] is None:
        return
    after = entry.tool.parameters
    assert after["type"] == before["parameters"]["type"]
    assert after.get("required", []) == before["parameters"].get("required", [])
    for argument, spec in before["parameters"]["properties"].items():
        assert after["properties"][argument] == spec, argument


def test_the_baseline_covers_every_tool_that_was_released():
    assert len(BASELINE["tools"]) == 56


def test_tool_context_keeps_its_fields_in_order_and_only_grows_at_the_end():
    fields = [field.name for field in dataclasses.fields(ToolContext)]

    assert fields[: len(BASELINE["tool_context_fields"])] == BASELINE["tool_context_fields"]


def test_tool_result_keeps_its_fields_in_order_and_only_grows_at_the_end():
    fields = [field.name for field in dataclasses.fields(ToolResult)]

    assert fields[: len(BASELINE["tool_result_fields"])] == BASELINE["tool_result_fields"]


def test_every_new_tool_context_field_is_optional_so_old_constructions_still_work():
    added = [
        field for field in dataclasses.fields(ToolContext)
        if field.name not in BASELINE["tool_context_fields"]
    ]

    assert all(field.default is None or field.default is not dataclasses.MISSING for field in added)
    ToolContext(user_id="u")


def test_every_new_tool_result_field_is_optional():
    assert ToolResult(success=True, data="x").mcp_content is None


def test_a_tool_services_bundle_still_builds_empty():
    assert ToolServices().media_store is None


@pytest.mark.parametrize("name", sorted(BASELINE["tools"]))
def test_a_released_tool_keeps_its_modes_flags_and_availability(current, name):
    before, tool = BASELINE["tools"][name], current[name].tool

    assert tool.modes == before["modes"]
    assert tool.read_only == before["read_only"]
    assert tool.mcp == before["mcp"]
    assert tool.requires_approval == before["requires_approval"]
    assert getattr(tool, "admin_only", False) is before["admin_only"] is False
    assert tool.destructive is before["destructive"] is False
