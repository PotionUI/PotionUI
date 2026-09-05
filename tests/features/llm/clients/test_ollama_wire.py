"""build_prompt_tools_text cache-key coverage.

The renderer (_render_prompt_tools_text) embeds a tool's COMPLETE parameters
JSON Schema verbatim (see LLM-11) — name, description, and the full nested
schema, not a lossy top-level-only prose summary — so the cache key must
change whenever ANY of that changes, including a change buried in a nested
object/array item contract, an enum, or a required list several levels deep.
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


def _nested_tool(item_enum=("a", "b"), item_required=("id",)):
    """A schema shaped like a real tool's array-of-objects parameter — the
    exact shape a top-level-only renderer (the pre-LLM-11 implementation)
    could never express: a nested `items` object with its own `enum` and its
    own `required` list, several levels inside the top-level schema."""
    return {
        "type": "function",
        "function": {
            "name": "nested",
            "description": "d",
            "parameters": {
                "type": "object",
                "properties": {
                    "rows": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "kind": {"type": "string", "enum": list(item_enum)},
                            },
                            "required": list(item_required),
                        },
                    },
                },
                "required": ["rows"],
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
    assert '"type":"integer"' in text_v2
    assert '"type":"integer"' not in text_v1
    # No TTL wait needed: the second call must already reflect tools_v2.
    assert text_v2 == ollama_wire._render_prompt_tools_text(tools_v2)


def test_changed_requiredness_invalidates_cache():
    tools_required = [_tool(required=True)]
    tools_optional = [_tool(required=False)]

    text_required = ollama_wire.build_prompt_tools_text(tools_required)
    text_optional = ollama_wire.build_prompt_tools_text(tools_optional)

    assert '"required":["x"]' in text_required
    assert '"required":["x"]' not in text_optional
    assert '"required":[]' in text_optional


def test_changed_parameter_description_invalidates_cache():
    tools_a = [_tool(pdesc="alpha")]
    tools_b = [_tool(pdesc="beta")]

    assert '"description":"alpha"' in ollama_wire.build_prompt_tools_text(tools_a)
    assert '"description":"beta"' in ollama_wire.build_prompt_tools_text(tools_b)


def test_tool_order_change_is_reflected_in_rendered_text():
    tool_a = _tool(name="a", description="A")
    tool_b = _tool(name="b", description="B")

    forward = ollama_wire.build_prompt_tools_text([tool_a, tool_b])
    reversed_ = ollama_wire.build_prompt_tools_text([tool_b, tool_a])

    assert forward != reversed_
    assert forward.index("**a**") < forward.index("**b**")
    assert reversed_.index("**b**") < reversed_.index("**a**")


# ---------------------------------------------------------------------------
# LLM-11: nested-contract cache identity. The pre-LLM-11 cache KEY already
# hashed the whole `function` object (see build_prompt_tools_text's
# docstring), so these prove the RENDERED TEXT now agrees — a nested change
# is not just cache-busting but actually visible in what the model reads.
# ---------------------------------------------------------------------------

def test_a_change_buried_in_a_nested_enum_invalidates_the_cache_and_the_text():
    tools_v1 = [_nested_tool(item_enum=("a", "b"))]
    tools_v2 = [_nested_tool(item_enum=("a", "b", "c"))]

    text_v1 = ollama_wire.build_prompt_tools_text(tools_v1)
    text_v2 = ollama_wire.build_prompt_tools_text(tools_v2)

    assert text_v1 != text_v2
    assert '"enum":["a","b","c"]' in text_v2
    assert '"enum":["a","b","c"]' not in text_v1
    assert text_v2 == ollama_wire._render_prompt_tools_text(tools_v2)


def test_a_change_in_a_nested_required_list_invalidates_the_cache_and_the_text():
    tools_v1 = [_nested_tool(item_required=("id",))]
    tools_v2 = [_nested_tool(item_required=("id", "kind"))]

    text_v1 = ollama_wire.build_prompt_tools_text(tools_v1)
    text_v2 = ollama_wire.build_prompt_tools_text(tools_v2)

    assert text_v1 != text_v2
    assert '"required":["id","kind"]' in text_v2
    assert '"required":["id","kind"]' not in text_v1


def test_the_nested_item_contract_is_present_verbatim_in_the_rendered_text():
    """The specific fragment a top-level-only renderer (the pre-LLM-11
    implementation) could never produce at all: it only ever looked at
    `params.get("properties", {})`'s IMMEDIATE values, never descending into
    an array's `items`. Bite-checked: substituting that renderer back in
    makes this assertion fail (see the LLM-11 report)."""
    text = ollama_wire.build_prompt_tools_text([_nested_tool()])

    assert '"items":{"properties":{"id":{"type":"string"},"kind":{"enum":["a","b"],"type":"string"}},"required":["id"],"type":"object"}' in text
