"""build_prompt_tools_text cache-key coverage.

The renderer (_render_prompt_tools_text) consumes a tool's whole schema —
name, description, parameter names/types/descriptions/requiredness — so the
cache key must change whenever any of that changes, not just name/description.
"""

import copy

import pytest

from src.features.llm.clients import ollama_wire


def _tool(name="t", description="d", ptype="string", pdesc="p", required=True):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {"x": {"type": ptype, "description": pdesc}},
                "required": ["x"] if required else [],
            },
        },
    }


@pytest.fixture(autouse=True)
def _clear_cache():
    ollama_wire._prompt_tools_text_cache.clear()
    yield
    ollama_wire._prompt_tools_text_cache.clear()


def test_unchanged_schema_reuses_cached_render(monkeypatch):
    original = ollama_wire._render_prompt_tools_text
    calls = []

    def spy(tools):
        calls.append(1)
        return original(tools)

    monkeypatch.setattr(ollama_wire, "_render_prompt_tools_text", spy)

    tools = [_tool()]
    first = ollama_wire.build_prompt_tools_text(tools)
    second = ollama_wire.build_prompt_tools_text(copy.deepcopy(tools))

    assert first == second
    assert len(calls) == 1


def test_changed_parameter_type_invalidates_cache_immediately():
    tools_v1 = [_tool(ptype="string")]
    tools_v2 = [_tool(ptype="integer")]

    text_v1 = ollama_wire.build_prompt_tools_text(tools_v1)
    text_v2 = ollama_wire.build_prompt_tools_text(tools_v2)

    assert text_v1 != text_v2
    assert "(integer" in text_v2
    assert "(integer" not in text_v1
    # No TTL wait needed: the second call must already reflect tools_v2.
    assert text_v2 == ollama_wire._render_prompt_tools_text(tools_v2)


def test_changed_requiredness_invalidates_cache():
    tools_required = [_tool(required=True)]
    tools_optional = [_tool(required=False)]

    text_required = ollama_wire.build_prompt_tools_text(tools_required)
    text_optional = ollama_wire.build_prompt_tools_text(tools_optional)

    assert "(required)" in text_required
    assert "(required)" not in text_optional


def test_changed_parameter_description_invalidates_cache():
    tools_a = [_tool(pdesc="alpha")]
    tools_b = [_tool(pdesc="beta")]

    assert "alpha" in ollama_wire.build_prompt_tools_text(tools_a)
    assert "beta" in ollama_wire.build_prompt_tools_text(tools_b)


def test_tool_order_change_is_reflected_in_rendered_text():
    tool_a = _tool(name="a", description="A")
    tool_b = _tool(name="b", description="B")

    forward = ollama_wire.build_prompt_tools_text([tool_a, tool_b])
    reversed_ = ollama_wire.build_prompt_tools_text([tool_b, tool_a])

    assert forward != reversed_
    assert forward.index("**a**") < forward.index("**b**")
    assert reversed_.index("**b**") < reversed_.index("**a**")
