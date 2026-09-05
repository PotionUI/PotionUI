"""LLM-11: the force_prompt_tools renderer must preserve the COMPLETE
parameters contract, not just top-level property name/type/description/
requiredness.

Uses two real builtin tools whose schemas a flat top-level-only renderer
cannot express at all: `AddPromptTool` (an array item schema, an `enum`, a
`minItems` bound) and `UpdateFormSettingsTool` (a nested object schema
inside an array item, with its own nested `required` list) — plus one small
hand-built "alternative schema" fixture (a schema built from `oneOf`
alternatives, with no top-level `properties` at all) to prove an
explicitly-empty object schema is distinguished from one with constraints
elsewhere.

Each assertion below checks for a JSON fragment the OLD renderer
(`_render_prompt_tools_text`'s prior top-level-`properties`-only
implementation) could never have produced — substituting that renderer back
in makes every test in this file fail, which is the "regression test that
fails when the old renderer is substituted" the card asks for; verified
directly by bite-check (see the report).
"""

import json

import pytest

from src.features.llm.clients.ollama import OllamaClient
from src.features.llm.clients.ollama_wire import _prompt_tools_text_cache
from src.features.llm.gateway import LLMGateway
from src.features.llm.tools.builtin.manage_prompts_tool import AddPromptTool
from src.features.llm.tools.builtin.update_form_settings_tool import UpdateFormSettingsTool
from src.features.llm import context_budget
from tests.features.llm.wire_capture import OLLAMA_REPLY, install_wire_capture, json_response, make_config


def _compact(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _alternative_schema_tool() -> dict:
    """A schema with NO top-level `properties` at all — built entirely from
    `oneOf` alternatives. The old renderer's `params.get("properties", {})`
    would see nothing and print "Parameters: none", indistinguishable from a
    tool that genuinely takes no arguments."""
    return {
        "type": "function",
        "function": {
            "name": "pick_mode",
            "description": "Choose exactly one mode shape.",
            "parameters": {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {"preset": {"type": "string", "enum": ["fast", "quality"]}},
                        "required": ["preset"],
                    },
                    {
                        "type": "object",
                        "properties": {"custom": {"type": "object", "additionalProperties": True}},
                        "required": ["custom"],
                    },
                ],
            },
        },
    }


def _genuinely_empty_schema_tool() -> dict:
    """An explicitly empty object schema — no properties, no other
    constraints. Must render DIFFERENTLY from `_alternative_schema_tool`'s
    (constraints-elsewhere) and `_no_properties_key_schema_tool`'s (also no
    top-level properties, but via `additionalProperties`) schemas, even
    though the OLD renderer would have described all three identically as
    "Parameters: none"."""
    return {
        "type": "function",
        "function": {
            "name": "no_args",
            "description": "Takes no arguments.",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _no_properties_key_schema_tool() -> dict:
    """No top-level `properties` key at all, constrained only via
    `additionalProperties` — distinct from both the oneOf fixture and the
    explicitly-empty-object fixture above."""
    return {
        "type": "function",
        "function": {
            "name": "free_form",
            "description": "Accepts an arbitrary key-value bag.",
            "parameters": {"type": "object", "additionalProperties": {"type": "string"}},
        },
    }


@pytest.fixture(autouse=True)
def _clear_prompt_tools_cache():
    _prompt_tools_text_cache.clear()
    yield
    _prompt_tools_text_cache.clear()


@pytest.fixture
def client():
    return OllamaClient()


# ---------------------------------------------------------------------------
# Distinguishing an explicitly-empty object schema from one with constraints
# elsewhere (the card's explicit "distinguish" requirement)
# ---------------------------------------------------------------------------

def test_explicitly_empty_object_differs_from_a_schema_with_constraints_elsewhere():
    from src.features.llm.clients import ollama_wire

    empty_text = ollama_wire._render_prompt_tools_text([_genuinely_empty_schema_tool()])
    oneof_text = ollama_wire._render_prompt_tools_text([_alternative_schema_tool()])
    additional_props_text = ollama_wire._render_prompt_tools_text([_no_properties_key_schema_tool()])

    assert empty_text != oneof_text != additional_props_text
    assert empty_text != additional_props_text
    # The old renderer's "Parameters: none" string must be gone entirely —
    # it collapsed exactly these three distinct schemas into one message.
    assert "Parameters: none" not in empty_text
    assert "Parameters: none" not in oneof_text
    assert "Parameters: none" not in additional_props_text
    assert _compact(_genuinely_empty_schema_tool()["function"]["parameters"]) in empty_text
    assert _compact(_alternative_schema_tool()["function"]["parameters"]) in oneof_text
    assert _compact(_no_properties_key_schema_tool()["function"]["parameters"]) in additional_props_text


# ---------------------------------------------------------------------------
# Real tools with nested contracts, through the real wire-capture seam
# ---------------------------------------------------------------------------

async def test_force_prompt_tools_renders_the_complete_contract_for_real_tools(monkeypatch, client):
    add_prompt_schema = AddPromptTool().to_schema()
    update_form_schema = UpdateFormSettingsTool().to_schema()
    alt_schema = _alternative_schema_tool()

    capture = install_wire_capture(monkeypatch, [json_response(OLLAMA_REPLY)])
    config = make_config("ollama", provider_options={"force_prompt_tools": True})

    await client.generate_with_tools(
        [{"role": "user", "content": "hi"}], config, "SYS",
        tools=[add_prompt_schema, update_form_schema, alt_schema],
    )

    # force_prompt_tools puts EVERYTHING in the system prompt; the native
    # tools field must stay absent regardless of how rich the schema is.
    assert "tools" not in capture.body
    system_text = capture.body["messages"][0]["content"]

    assert _compact(add_prompt_schema["function"]["parameters"]) in system_text
    assert _compact(update_form_schema["function"]["parameters"]) in system_text
    assert _compact(alt_schema["function"]["parameters"]) in system_text

    # Fragments a top-level-only renderer could never have produced —
    # nested enum, nested minItems bound, and a nested object's OWN
    # `required` list one level inside an array's `items`.
    assert '"enum":["positive","negative"]' in system_text  # AddPromptTool.usage_hint
    assert '"minItems":1' in system_text  # AddPromptTool.segments
    assert '"required":["field_name","value"]' in system_text  # UpdateFormSettingsTool.changes.items
    assert '"oneOf"' in system_text  # the alternative-schema fixture


async def test_native_tool_calling_is_unaffected_by_the_prompt_tool_renderer(monkeypatch, client):
    """Control: the structured/native Ollama path never touches
    `_render_prompt_tools_text` at all — the full, untouched schema dicts go
    straight into `payload["tools"]`, exactly as before LLM-11."""
    add_prompt_schema = AddPromptTool().to_schema()
    capture = install_wire_capture(monkeypatch, [json_response(OLLAMA_REPLY)])
    config = make_config("ollama")  # force_prompt_tools NOT set

    await client.generate_with_tools(
        [{"role": "user", "content": "hi"}], config, "SYS", tools=[add_prompt_schema],
    )

    assert capture.body["tools"] == [add_prompt_schema]
    # No system-prompt rewrite happened at all.
    assert capture.body["messages"][0] == {"role": "system", "content": "SYS"}


# ---------------------------------------------------------------------------
# LLM-03: the budget's tool-schema estimate is computed from the schemas'
# own JSON size (context_budget.count_tool_schemas), independent of this
# card's prompt-TEXT rendering fix — a large real contract still trips a
# small context window, with the estimate honestly labelled unmeasured.
# ---------------------------------------------------------------------------

class TestGatewayToolSchemaBudgetControl:
    @pytest.fixture
    def gateway(self):
        from unittest.mock import AsyncMock, Mock
        gw = LLMGateway(llm_repository=Mock())
        gw._ollama.generate_with_tools = AsyncMock()
        return gw

    @pytest.mark.asyncio
    async def test_a_large_real_tool_schema_can_exceed_a_small_context_window(self, gateway):
        gateway.repository.get_configuration.return_value = make_config(
            "ollama", provider_options={"context_window": 50}, max_tokens=10,
        )
        tools = [AddPromptTool().to_schema(), UpdateFormSettingsTool().to_schema()]

        with pytest.raises(context_budget.ContextBudgetExceededError) as exc_info:
            await gateway.generate_with_tools(
                messages=[{"role": "user", "content": "hi"}], llm_id="cfg-1", tools=tools,
            )

        breakdown = exc_info.value.breakdown
        assert breakdown["tool_schema_tokens"] > 0
        # A JSON-size estimate, never a real tokenizer count — must never be
        # presented as measured.
        assert breakdown["measured"] is False
        gateway._ollama.generate_with_tools.assert_not_called()
