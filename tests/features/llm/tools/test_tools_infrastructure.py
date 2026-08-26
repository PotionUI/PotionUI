"""Tests for the LLM tools infrastructure: base, registry, and executor."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from typing import Any, Dict, List

from src.features.llm.clients.base import LLMResponse
from src.features.llm.tools.base import BaseTool, ToolResult, ToolSource, ToolContext, ToolExecution
from src.features.llm.tools.registry import ToolRegistry
from src.features.llm.tools.executor import ToolExecutor


# ---------------------------------------------------------------------------
# Helpers / concrete test implementations
# ---------------------------------------------------------------------------

class EchoTool(BaseTool):
    """Simple tool that echoes the 'message' argument back."""

    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echoes a message back."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message to echo"}
            },
            "required": ["message"],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        message = kwargs.get("message", "")
        return ToolResult(success=True, data=f"echo: {message}")


class FailingTool(BaseTool):
    """Tool that always raises an exception."""

    @property
    def name(self) -> str:
        return "fail"

    @property
    def description(self) -> str:
        return "Always fails."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        raise RuntimeError("intentional failure")


class ImageResultTool(BaseTool):
    """Tool whose result carries its own image (e.g. a render/preview) — used
    to test that a tool-returned image takes precedence over the user's
    attached image for exactly the next LLM call."""

    @property
    def name(self) -> str:
        return "image_tool"

    @property
    def description(self) -> str:
        return "Returns an image."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        return ToolResult(success=True, data="rendered", image_data="tool_img")


def make_context(user_id: str = "user-1") -> ToolContext:
    return ToolContext(user_id=user_id)


def make_llm_response(content: str = "final answer", tool_calls=None):
    response = MagicMock()
    response.content = content
    response.tool_calls = tool_calls or []
    return response


def make_real_llm_response(content: str = "", tool_calls=None) -> LLMResponse:
    """A real (non-mock) LLMResponse — used where the test must exercise the
    actual pydantic model's field set, not a MagicMock that would silently
    accept an assignment to an undeclared attribute."""
    return LLMResponse(content=content, model="model-1", provider_id="p", tool_calls=tool_calls or [])


# ---------------------------------------------------------------------------
# ToolResult tests
# ---------------------------------------------------------------------------

class TestToolResult:
    def test_success_result(self):
        result = ToolResult(success=True, data="some data")
        assert result.success is True
        assert result.data == "some data"
        assert result.error is None

    def test_error_result(self):
        result = ToolResult(success=False, data="", error="something went wrong")
        assert result.success is False
        assert result.error == "something went wrong"


# ---------------------------------------------------------------------------
# ToolContext tests
# ---------------------------------------------------------------------------

class TestToolContext:
    def test_default_values(self):
        ctx = ToolContext(user_id="u1")
        assert ctx.user_id == "u1"
        assert ctx.session_metadata == {}
        assert ctx.segment_category_repository is None
        assert ctx.saved_segment_repository is None
        assert ctx.segment_template_repository is None
        assert ctx.model_index_manager is None
        assert ctx.preset_manager is None
        assert ctx.phrasebook_category_repository is None
        assert ctx.phrasebook_value_repository is None

    def test_with_services(self):
        mock_pm = MagicMock()
        ctx = ToolContext(user_id="u2", preset_manager=mock_pm, session_metadata={"k": "v"})
        assert ctx.preset_manager is mock_pm
        assert ctx.session_metadata == {"k": "v"}


# ---------------------------------------------------------------------------
# ToolExecution tests
# ---------------------------------------------------------------------------

class TestToolExecution:
    def test_fields(self):
        result = ToolResult(success=True, data="ok")
        exec_ = ToolExecution(tool_name="echo", arguments={"message": "hi"}, result=result, duration_ms=42)
        assert exec_.tool_name == "echo"
        assert exec_.arguments == {"message": "hi"}
        assert exec_.result is result
        assert exec_.duration_ms == 42


# ---------------------------------------------------------------------------
# BaseTool / EchoTool tests
# ---------------------------------------------------------------------------

class TestBaseTool:
    @pytest.mark.asyncio
    async def test_execute_returns_result(self):
        tool = EchoTool()
        ctx = make_context()
        result = await tool.execute(ctx, message="hello")
        assert result.success is True
        assert result.data == "echo: hello"

    def test_to_schema_structure(self):
        tool = EchoTool()
        schema = tool.to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "echo"
        assert schema["function"]["description"] == "Echoes a message back."
        assert "parameters" in schema["function"]

    def test_hint_default_empty(self):
        tool = EchoTool()
        assert tool.hint == ""

    def test_hint_override(self):
        class HintedTool(EchoTool):
            @property
            def hint(self) -> str:
                return "Use when user asks to echo."
        tool = HintedTool()
        assert tool.hint == "Use when user asks to echo."

    def test_is_available_defaults_to_true_regardless_of_form_state(self):
        tool = EchoTool()
        assert tool.is_available(None) is True
        assert tool.is_available({}) is True
        assert tool.is_available({"video_director": {"active": False}}) is True


# ---------------------------------------------------------------------------
# ToolRegistry tests
# ---------------------------------------------------------------------------

class TestToolRegistry:
    def test_register_and_get(self):
        registry = ToolRegistry()
        tool = EchoTool()
        registry.register(tool)
        assert registry.get("echo") is tool

    def test_get_missing_returns_none(self):
        registry = ToolRegistry()
        assert registry.get("nonexistent") is None

    def test_get_all(self):
        registry = ToolRegistry()
        echo = EchoTool()
        fail = FailingTool()
        registry.register(echo)
        registry.register(fail)
        all_tools = registry.get_all()
        assert len(all_tools) == 2
        assert echo in all_tools
        assert fail in all_tools

    def test_replace_existing_tool(self):
        registry = ToolRegistry()
        tool1 = EchoTool()
        tool2 = EchoTool()
        registry.register(tool1)
        registry.register(tool2)
        assert registry.get("echo") is tool2

    def test_get_schemas(self):
        registry = ToolRegistry()
        registry.register(EchoTool())
        schemas = registry.get_schemas()
        assert len(schemas) == 1
        assert schemas[0]["type"] == "function"
        assert schemas[0]["function"]["name"] == "echo"

    def test_get_schemas_empty(self):
        registry = ToolRegistry()
        assert registry.get_schemas() == []

    def test_register_tracks_source_and_unregister_source(self):
        registry = ToolRegistry()
        registry.register(EchoTool(), source="plugin-x")
        registry.register(FailingTool())  # builtin source
        removed = registry.unregister_source("plugin-x")
        assert removed == 1
        assert registry.get("echo") is None
        assert registry.get("fail") is not None

    def test_unregister_source_no_match(self):
        registry = ToolRegistry()
        registry.register(EchoTool())
        assert registry.unregister_source("nope") == 0
        assert registry.get("echo") is not None

    def _mode(self, mode_id="generation", tool_names=None):
        from src.features.chat.modes import ChatMode
        return ChatMode(id=mode_id, name=mode_id, tool_names=tool_names or [])

    def test_get_for_mode_includes_global_tools(self):
        registry = ToolRegistry()
        registry.register(EchoTool())  # modes = None -> global
        tools = registry.get_for_mode(self._mode("anything"))
        assert [t.name for t in tools] == ["echo"]

    def test_get_for_mode_includes_declared_mode(self):
        class GenTool(EchoTool):
            modes = ["generation"]
        registry = ToolRegistry()
        registry.register(GenTool())
        assert len(registry.get_for_mode(self._mode("generation"))) == 1
        assert len(registry.get_for_mode(self._mode("other"))) == 0

    def test_get_for_mode_includes_mode_tool_names(self):
        class GenTool(EchoTool):
            modes = ["generation"]
        registry = ToolRegistry()
        registry.register(GenTool())
        # 'other' mode borrows the tool explicitly via tool_names
        mode = self._mode("other", tool_names=["echo"])
        assert [t.name for t in registry.get_for_mode(mode)] == ["echo"]

    def test_get_tool_hints_text(self):
        """get_tool_hints_text should return only the hints portion."""
        class HintedTool(EchoTool):
            @property
            def hint(self) -> str:
                return "Use when user asks to echo."

        registry = ToolRegistry()
        registry.register(HintedTool())
        hints = registry.get_tool_hints_text()

        assert hints == "- echo: Use when user asks to echo."

    def test_get_tool_hints_text_empty(self):
        registry = ToolRegistry()
        assert registry.get_tool_hints_text() == ""


# ---------------------------------------------------------------------------
# ToolRegistry filtered getter tests
# ---------------------------------------------------------------------------

class TestToolRegistryFiltered:
    """Tests for the optional `names` parameter on registry methods."""

    def _make_hinted_tool(self, name: str, hint: str):
        """Create a tool with a custom name and hint."""
        class _Tool(EchoTool):
            @property
            def name(self_inner) -> str:
                return name

            @property
            def hint(self_inner) -> str:
                return hint
        return _Tool()

    def test_get_schemas_with_names_filter(self):
        registry = ToolRegistry()
        registry.register(EchoTool())
        registry.register(FailingTool())
        schemas = registry.get_schemas(names=["echo"])
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "echo"

    def test_get_schemas_with_empty_names_returns_nothing(self):
        registry = ToolRegistry()
        registry.register(EchoTool())
        schemas = registry.get_schemas(names=[])
        assert len(schemas) == 0

    def test_get_schemas_none_returns_all(self):
        registry = ToolRegistry()
        registry.register(EchoTool())
        registry.register(FailingTool())
        schemas = registry.get_schemas(names=None)
        assert len(schemas) == 2

    def test_get_schemas_nonexistent_name(self):
        registry = ToolRegistry()
        registry.register(EchoTool())
        schemas = registry.get_schemas(names=["nonexistent"])
        assert len(schemas) == 0

    def test_get_tool_hints_text_filtered(self):
        registry = ToolRegistry()
        tool_a = self._make_hinted_tool("tool_a", "Hint A")
        tool_b = self._make_hinted_tool("tool_b", "Hint B")
        registry.register(tool_a)
        registry.register(tool_b)
        hints = registry.get_tool_hints_text(names=["tool_a"])
        assert "tool_a: Hint A" in hints
        assert "tool_b" not in hints

    def test_get_tool_hints_text_none_returns_all(self):
        registry = ToolRegistry()
        tool_a = self._make_hinted_tool("tool_a", "Hint A")
        tool_b = self._make_hinted_tool("tool_b", "Hint B")
        registry.register(tool_a)
        registry.register(tool_b)
        hints = registry.get_tool_hints_text(names=None)
        assert "tool_a" in hints
        assert "tool_b" in hints

    def _make_conditional_tool(self, name, description):
        class _Tool(BaseTool):
            @property
            def name(self_inner):
                return name

            @property
            def description(self_inner):
                return description

            @property
            def parameters(self_inner):
                return {"type": "object", "properties": {}}

            async def execute(self_inner, context, **kwargs):
                return ToolResult(success=True, data="")

        return _Tool()

    def test_get_schemas_resolves_description_cross_reference_when_present(self):
        registry = ToolRegistry()
        registry.register(self._make_conditional_tool(
            "run_it", "Do it.{{#if helper}} Call helper first.{{/if}}"
        ))
        registry.register(self._make_conditional_tool("helper", "A helper."))
        schema = registry.get_schemas(names=["run_it", "helper"])[0]
        assert "Call helper first." in schema["function"]["description"]

    def test_get_schemas_drops_description_cross_reference_when_absent(self):
        registry = ToolRegistry()
        registry.register(self._make_conditional_tool(
            "run_it", "Do it.{{#if helper}} Call helper first.{{/if}}"
        ))
        schema = registry.get_schemas(names=["run_it"])[0]
        assert schema["function"]["description"] == "Do it."
        assert "helper" not in schema["function"]["description"]

    def test_get_schemas_none_resolves_against_all_registered(self):
        registry = ToolRegistry()
        registry.register(self._make_conditional_tool(
            "run_it", "Do it.{{#if helper}} Call helper first.{{/if}}"
        ))
        registry.register(self._make_conditional_tool("helper", "A helper."))
        schema = next(s for s in registry.get_schemas(names=None) if s["function"]["name"] == "run_it")
        # helper is registered, so its cross-reference survives.
        assert "Call helper first." in schema["function"]["description"]


# ---------------------------------------------------------------------------
# ToolExecutor allowed_tools filter tests
# ---------------------------------------------------------------------------


class TestToolExecutorAllowedTools:
    """Tests for the allowed_tools filter on ToolExecutor."""

    def _make_executor(self):
        registry = ToolRegistry()
        registry.register(EchoTool())
        registry.register(FailingTool())
        llm_service = AsyncMock()
        return ToolExecutor(tool_registry=registry, llm_service=llm_service), llm_service

    @pytest.mark.asyncio
    async def test_enabled_tools_filters_schemas_sent_to_llm(self):
        """Only allowed tools schemas should be sent to the LLM."""
        executor, llm_service = self._make_executor()
        llm_service.generate_with_tools.return_value = make_llm_response("done", tool_calls=[])

        await executor.execute_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            allowed_tools=["echo"],
        )

        # Check that only the echo tool schema was sent
        call_kwargs = llm_service.generate_with_tools.call_args[1]
        tool_names = [t["function"]["name"] for t in call_kwargs["tools"]]
        assert tool_names == ["echo"]

    @pytest.mark.asyncio
    async def test_enabled_tools_none_sends_all(self):
        """When allowed_tools is None, all tool schemas should be sent."""
        executor, llm_service = self._make_executor()
        llm_service.generate_with_tools.return_value = make_llm_response("done", tool_calls=[])

        await executor.execute_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            allowed_tools=None,
        )

        call_kwargs = llm_service.generate_with_tools.call_args[1]
        tool_names = [t["function"]["name"] for t in call_kwargs["tools"]]
        assert len(tool_names) == 2
        assert "echo" in tool_names
        assert "fail" in tool_names

    @pytest.mark.asyncio
    async def test_execute_tool_rejects_disabled_tool(self):
        """_execute_tool should reject a tool not in the allowed set."""
        executor, _ = self._make_executor()
        result, pending = await executor._execute_tool(
            "echo", make_context(), {"message": "hi"}, allowed_tools=["fail"]
        )
        assert result.success is False
        assert "not enabled" in result.error

    @pytest.mark.asyncio
    async def test_execute_tool_allows_enabled_tool(self):
        """_execute_tool should allow a tool in the allowed set."""
        executor, _ = self._make_executor()
        result, pending = await executor._execute_tool(
            "echo", make_context(), {"message": "hi"}, allowed_tools=["echo"]
        )
        assert result.success is True
        assert result.data == "echo: hi"

    @pytest.mark.asyncio
    async def test_rejection_message_lists_available_tools(self):
        """A rejected tool call names the tools that ARE available (D3 recovery)."""
        executor, _ = self._make_executor()
        result, _ = await executor._execute_tool(
            "echo", make_context(), {"message": "hi"}, allowed_tools=["fail", "other"]
        )
        assert result.success is False
        # The recoverable list is sorted and present so a small model can retry.
        assert "Available tools: fail, other" in result.error

    @pytest.mark.asyncio
    async def test_rejection_message_when_no_tools_available(self):
        executor, _ = self._make_executor()
        result, _ = await executor._execute_tool(
            "echo", make_context(), {"message": "hi"}, allowed_tools=[]
        )
        assert result.success is False
        assert "Available tools: none" in result.error

    @pytest.mark.asyncio
    async def test_execute_tool_none_allows_all(self):
        """_execute_tool with allowed_tools=None allows any tool."""
        executor, _ = self._make_executor()
        result, pending = await executor._execute_tool(
            "echo", make_context(), {"message": "hi"}, allowed_tools=None
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_stream_enabled_tools_filters_schemas(self):
        """Streaming: only allowed tool schemas should be sent to the LLM."""
        executor, llm_service = self._make_executor()
        llm_service.generate_with_tools.return_value = make_llm_response("done", tool_calls=[])

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "hi"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            allowed_tools=["echo"],
        ))

        call_kwargs = llm_service.generate_with_tools.call_args[1]
        tool_names = [t["function"]["name"] for t in call_kwargs["tools"]]
        assert tool_names == ["echo"]

    @pytest.mark.asyncio
    async def test_disabled_tool_called_by_llm_returns_error(self):
        """If LLM tries to call a tool not in allowed_tools, it should get an error."""
        executor, llm_service = self._make_executor()

        tool_call = {
            "id": "call-1",
            "function": {"name": "fail", "arguments": "{}"},
        }
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("ok"),
        ]

        response, executions = await executor.execute_with_tools(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            allowed_tools=["echo"],  # 'fail' tool is not allowed
        )

        assert len(executions) == 1
        assert executions[0].result.success is False
        assert "not enabled" in executions[0].result.error


# ---------------------------------------------------------------------------
# XML tool call parsing tests
# ---------------------------------------------------------------------------

class TestXmlToolCallParsing:
    def test_parse_xml_tool_call_with_name_format(self):
        from src.features.llm.tools.executor import _parse_xml_tool_calls
        content = '<tool_call>\n{"name": "echo", "arguments": {"message": "hi"}}\n</tool_call>'
        calls = _parse_xml_tool_calls(content)
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "echo"
        assert calls[0]["function"]["arguments"] == {"message": "hi"}

    def test_parse_xml_tool_call_with_function_format(self):
        from src.features.llm.tools.executor import _parse_xml_tool_calls
        content = '<tool_call>{"function": {"name": "echo", "arguments": {}}}</tool_call>'
        calls = _parse_xml_tool_calls(content)
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "echo"

    def test_parse_multiple_xml_tool_calls(self):
        from src.features.llm.tools.executor import _parse_xml_tool_calls
        content = (
            '<tool_call>{"name": "a", "arguments": {}}</tool_call>'
            'some text'
            '<tool_call>{"name": "b", "arguments": {}}</tool_call>'
        )
        calls = _parse_xml_tool_calls(content)
        assert len(calls) == 2

    def test_parse_invalid_json_skipped(self):
        from src.features.llm.tools.executor import _parse_xml_tool_calls
        content = '<tool_call>not valid json</tool_call>'
        calls = _parse_xml_tool_calls(content)
        assert len(calls) == 0

    def test_parse_multiple_xml_tool_calls_preserves_order(self):
        from src.features.llm.tools.executor import _parse_xml_tool_calls
        content = (
            '<tool_call>{"name": "a", "arguments": {"n": 1}}</tool_call>'
            'some text between'
            '<tool_call>{"name": "b", "arguments": {"n": 2}}</tool_call>'
            '<tool_call>{"name": "c", "arguments": {"n": 3}}</tool_call>'
        )
        calls = _parse_xml_tool_calls(content)
        assert [c["function"]["name"] for c in calls] == ["a", "b", "c"]

    def test_parse_xml_tool_call_with_mangled_quote_tokens(self):
        """The same tokenizer artifact tool_call_rescue demangles for
        <tool_action> tags shows up inside a well-formed <tool_call> block
        too -- the complete call must still parse, not be dropped."""
        from src.features.llm.tools.executor import _parse_xml_tool_calls
        content = (
            '<tool_call>{<|"|>name<|"|>: <|"|>echo<|"|>, <|"|>arguments<|"|>: '
            '{<|"|>message<|"|>: <|"|>hi<|"|>}}</tool_call>'
        )
        calls = _parse_xml_tool_calls(content)
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "echo"
        assert calls[0]["function"]["arguments"] == {"message": "hi"}

    def test_parse_xml_tool_call_accepts_parameters_key(self):
        """A model that writes 'parameters' (the schema's own field name)
        instead of 'arguments' must still dispatch with the real payload,
        not an empty arguments dict."""
        from src.features.llm.tools.executor import _parse_xml_tool_calls
        content = '<tool_call>{"name": "echo", "parameters": {"message": "hi"}}</tool_call>'
        calls = _parse_xml_tool_calls(content)
        assert len(calls) == 1
        assert calls[0]["function"]["arguments"] == {"message": "hi"}

    def test_parse_no_xml_returns_empty(self):
        from src.features.llm.tools.executor import _parse_xml_tool_calls
        content = 'just regular text'
        calls = _parse_xml_tool_calls(content)
        assert len(calls) == 0

    def test_strip_tool_call_xml(self):
        from src.features.llm.tools.executor import strip_tool_call_xml
        content = 'hello <tool_call>{"name": "x"}</tool_call> world'
        assert strip_tool_call_xml(content) == 'hello  world'

    def test_strip_tool_call_xml_multiline(self):
        from src.features.llm.tools.executor import strip_tool_call_xml
        content = 'before\n<tool_call>\n{"name": "x"}\n</tool_call>\nafter'
        assert strip_tool_call_xml(content) == 'before\n\nafter'

    def test_strip_preserves_clean_content(self):
        from src.features.llm.tools.executor import strip_tool_call_xml
        content = 'no xml here'
        assert strip_tool_call_xml(content) == 'no xml here'


class TestResolveToolCalls:
    """Tests for _resolve_tool_calls which falls back to XML parsing."""

    def _make_executor(self):
        registry = ToolRegistry()
        registry.register(EchoTool())
        llm_service = AsyncMock()
        return ToolExecutor(tool_registry=registry, llm_service=llm_service)

    def test_returns_structured_tool_calls_when_present(self):
        executor = self._make_executor()
        response = make_llm_response("", tool_calls=[{"function": {"name": "echo"}}])
        calls = executor._resolve_tool_calls(response)
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "echo"

    def test_falls_back_to_xml_when_no_structured_calls(self):
        executor = self._make_executor()
        response = make_llm_response(
            '<tool_call>{"name": "echo", "arguments": {"message": "hi"}}</tool_call>',
            tool_calls=[]
        )
        calls = executor._resolve_tool_calls(response)
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "echo"

    def test_returns_empty_when_no_calls_of_any_kind(self):
        executor = self._make_executor()
        response = make_llm_response("just text", tool_calls=[])
        calls = executor._resolve_tool_calls(response)
        assert len(calls) == 0

    @pytest.mark.asyncio
    async def test_xml_tool_calls_executed_in_loop(self):
        """XML tool calls should be parsed and executed like structured ones."""
        executor = self._make_executor()
        llm_service = executor.llm_service

        # First call: model returns XML tool call (no structured tool_calls)
        xml_response = make_llm_response(
            '<tool_call>{"name": "echo", "arguments": {"message": "test"}}</tool_call>',
            tool_calls=[]
        )
        # Second call: model returns final text
        final_response = make_llm_response("Done!", tool_calls=[])

        llm_service.generate_with_tools.side_effect = [xml_response, final_response]

        response, executions = await executor.execute_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        )

        assert response.content == "Done!"
        assert len(executions) == 1
        assert executions[0].tool_name == "echo"


# ---------------------------------------------------------------------------
# ToolExecutor tests
# ---------------------------------------------------------------------------

class TestToolExecutor:
    def _make_executor(self, tool: BaseTool = None):
        registry = ToolRegistry()
        if tool:
            registry.register(tool)
        llm_service = AsyncMock()
        return ToolExecutor(tool_registry=registry, llm_service=llm_service), llm_service

    @pytest.mark.asyncio
    async def test_no_tool_calls_returns_immediately(self):
        executor, llm_service = self._make_executor(EchoTool())
        llm_service.generate_with_tools.return_value = make_llm_response("done", tool_calls=[])

        response, executions = await executor.execute_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            llm_id="model-1",
            system_message="You are helpful.",
            tool_context=make_context(),
        )

        assert response.content == "done"
        assert executions == []
        llm_service.generate_with_tools.assert_called_once()

    @pytest.mark.asyncio
    async def test_single_tool_call_then_final_response(self):
        executor, llm_service = self._make_executor(EchoTool())

        tool_call = {
            "id": "call-1",
            "function": {"name": "echo", "arguments": '{"message": "world"}'},
        }
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("I echoed: world"),
        ]

        response, executions = await executor.execute_with_tools(
            messages=[{"role": "user", "content": "echo world"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        )

        assert response.content == "I echoed: world"
        assert len(executions) == 1
        assert executions[0].tool_name == "echo"
        assert executions[0].arguments == {"message": "world"}
        assert executions[0].result.success is True
        assert executions[0].duration_ms >= 0

    @pytest.mark.asyncio
    async def test_tool_execution_failure_returns_error_result(self):
        executor, llm_service = self._make_executor(FailingTool())

        tool_call = {
            "id": "call-err",
            "function": {"name": "fail", "arguments": "{}"},
        }
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("I could not do that"),
        ]

        response, executions = await executor.execute_with_tools(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        )

        assert len(executions) == 1
        assert executions[0].result.success is False
        assert "intentional failure" in executions[0].result.error
        assert "fail" in executions[0].result.error
        assert executions[0].result.error != "Tool execution failed: intentional failure"

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error_result(self):
        executor, llm_service = self._make_executor()  # no tools registered

        tool_call = {
            "id": "call-x",
            "function": {"name": "ghost", "arguments": "{}"},
        }
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("could not find tool"),
        ]

        _, executions = await executor.execute_with_tools(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        )

        assert len(executions) == 1
        assert executions[0].result.success is False
        assert "ghost" in executions[0].result.error

    @pytest.mark.asyncio
    async def test_max_iterations_triggers_final_call_without_tools(self):
        executor, llm_service = self._make_executor(EchoTool())

        # Always return a tool call - never finishes naturally
        tool_call = {
            "id": "call-loop",
            "function": {"name": "echo", "arguments": '{"message": "x"}'},
        }
        looping = make_llm_response("", tool_calls=[tool_call])
        final = make_llm_response("giving up")

        # max_iterations=2 -> 2 looping responses + 1 final call without tools
        llm_service.generate_with_tools.side_effect = [looping, looping, final]

        response, executions = await executor.execute_with_tools(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            max_iterations=2,
        )

        assert response.content == "giving up"
        assert len(executions) == 2  # one per iteration
        # Last call must have been made with empty tools list
        last_call_kwargs = llm_service.generate_with_tools.call_args_list[-1][1]
        assert last_call_kwargs["tools"] == []

    @pytest.mark.asyncio
    async def test_user_image_data_persists_across_all_iterations(self):
        """The user's attached image must stay available on EVERY iteration of
        the turn, not just the first. Tool-enabled modes instruct the model to
        call tools before answering (see modes/builtin.py's generation-mode
        prompt), so iteration 1 is almost never the model's real answer —
        dropping the image right after it (the old behavior) silently blinded
        the model on the response it actually gives the user."""
        executor, llm_service = self._make_executor(EchoTool())

        tool_call = {
            "id": "c1",
            "function": {"name": "echo", "arguments": '{"message": "img"}'},
        }
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("done"),
        ]

        await executor.execute_with_tools(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            image_data="base64img",
        )

        calls = llm_service.generate_with_tools.call_args_list
        assert calls[0][1]["image_data"] == "base64img"
        assert calls[1][1]["image_data"] == "base64img"
        assert calls[2][1]["image_data"] == "base64img"

    @pytest.mark.asyncio
    async def test_tool_result_image_takes_precedence_then_reverts_to_user_image(self):
        """A tool that returns its own image (e.g. a render preview) should
        override the user's image for exactly the next LLM call; the
        iteration after that must revert to the user's original attachment
        rather than staying stuck on the tool's image or dropping to None."""
        registry = ToolRegistry()
        registry.register(ImageResultTool())
        registry.register(EchoTool())
        llm_service = AsyncMock()
        executor = ToolExecutor(tool_registry=registry, llm_service=llm_service)

        image_tool_call = {"id": "c1", "function": {"name": "image_tool", "arguments": "{}"}}
        echo_tool_call = {"id": "c2", "function": {"name": "echo", "arguments": '{"message": "x"}'}}
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[image_tool_call]),  # iteration 1
            make_llm_response("", tool_calls=[echo_tool_call]),   # iteration 2
            make_llm_response("done"),                             # iteration 3
        ]

        await executor.execute_with_tools(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            image_data="user_img",
        )

        calls = llm_service.generate_with_tools.call_args_list
        assert calls[0][1]["image_data"] == "user_img"  # iteration 1: user's image
        assert calls[1][1]["image_data"] == "tool_img"  # iteration 2: tool's image takes precedence
        assert calls[2][1]["image_data"] == "user_img"  # iteration 3: reverts to user's image

    @pytest.mark.asyncio
    async def test_invalid_json_arguments_handled_gracefully(self):
        executor, llm_service = self._make_executor(EchoTool())

        tool_call = {
            "id": "c1",
            "function": {"name": "echo", "arguments": "NOT JSON"},
        }
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("recovered"),
        ]

        response, executions = await executor.execute_with_tools(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        )

        # Should not raise; arguments fall back to {}
        assert len(executions) == 1
        assert executions[0].arguments == {}
        assert response.content == "recovered"

    @pytest.mark.asyncio
    async def test_original_messages_not_mutated(self):
        executor, llm_service = self._make_executor(EchoTool())
        llm_service.generate_with_tools.return_value = make_llm_response("ok")

        original = [{"role": "user", "content": "hello"}]
        original_copy = list(original)

        await executor.execute_with_tools(
            messages=original,
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        )

        assert original == original_copy


# ---------------------------------------------------------------------------
# ToolExecutor.execute_with_tools_stream tests
# ---------------------------------------------------------------------------

async def collect_stream_events(gen) -> list:
    """Collect all events from an async generator into a list."""
    events = []
    async for event in gen:
        events.append(event)
    return events


def make_streaming_llm(chunks):
    """Return an async generator that yields the given string chunks."""
    async def _gen(*args, **kwargs):
        for chunk in chunks:
            yield chunk
    return _gen


class TestToolExecutorStream:
    def _make_executor(self, tool: BaseTool = None):
        registry = ToolRegistry()
        if tool:
            registry.register(tool)
        llm_service = AsyncMock()
        return ToolExecutor(tool_registry=registry, llm_service=llm_service), llm_service

    @pytest.mark.asyncio
    async def test_no_tool_calls_yields_token_and_done(self):
        """When no tool calls, the already-fetched response is emitted as a single token + done."""
        executor, llm_service = self._make_executor(EchoTool())
        llm_service.generate_with_tools.return_value = make_llm_response("final answer", tool_calls=[])

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "hi"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        ))

        token_events = [e for e in events if e["type"] == "token"]
        done_events = [e for e in events if e["type"] == "done"]
        assert len(token_events) == 1
        assert token_events[0]["data"]["content"] == "final answer"
        assert len(done_events) == 1
        assert done_events[0]["data"]["full_content"] == "final answer"
        assert done_events[0]["data"]["tool_executions"] == []

    @pytest.mark.asyncio
    async def test_tool_call_yields_tool_start_and_end(self):
        """A tool call should yield tool_start and tool_end events."""
        executor, llm_service = self._make_executor(EchoTool())

        tool_call = {
            "id": "call-1",
            "function": {"name": "echo", "arguments": '{"message": "test"}'},
        }
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("done", tool_calls=[]),
        ]
        llm_service.stream_with_tools = make_streaming_llm(["done"])

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "echo test"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        ))

        event_types = [e["type"] for e in events]
        assert "tool_start" in event_types
        assert "tool_end" in event_types
        assert "token" in event_types
        assert "done" in event_types

    @pytest.mark.asyncio
    async def test_tool_start_event_has_correct_data(self):
        """tool_start event should have tool_name and arguments."""
        executor, llm_service = self._make_executor(EchoTool())

        tool_call = {
            "id": "call-1",
            "function": {"name": "echo", "arguments": '{"message": "hello"}'},
        }
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("ok", tool_calls=[]),
        ]
        llm_service.stream_with_tools = make_streaming_llm(["ok"])

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        ))

        tool_start = next(e for e in events if e["type"] == "tool_start")
        assert tool_start["data"]["tool_name"] == "echo"
        assert tool_start["data"]["arguments"] == {"message": "hello"}

    @pytest.mark.asyncio
    async def test_tool_end_event_has_correct_data(self):
        """tool_end event should have tool_name, success, and duration_ms."""
        executor, llm_service = self._make_executor(EchoTool())

        tool_call = {
            "id": "call-1",
            "function": {"name": "echo", "arguments": '{"message": "world"}'},
        }
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("ok", tool_calls=[]),
        ]
        llm_service.stream_with_tools = make_streaming_llm(["ok"])

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        ))

        tool_end = next(e for e in events if e["type"] == "tool_end")
        assert tool_end["data"]["tool_name"] == "echo"
        assert tool_end["data"]["success"] is True
        assert "duration_ms" in tool_end["data"]

    @pytest.mark.asyncio
    async def test_done_event_has_tool_executions(self):
        """done event should contain the list of tool executions."""
        executor, llm_service = self._make_executor(EchoTool())

        tool_call = {
            "id": "call-1",
            "function": {"name": "echo", "arguments": '{"message": "check"}'},
        }
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("finished", tool_calls=[]),
        ]
        llm_service.stream_with_tools = make_streaming_llm(["finished"])

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        ))

        done = next(e for e in events if e["type"] == "done")
        assert len(done["data"]["tool_executions"]) == 1
        assert done["data"]["tool_executions"][0].tool_name == "echo"

    @pytest.mark.asyncio
    async def test_max_iterations_yields_final_response(self):
        """When max iterations reached, a final non-streaming call is made without tools."""
        executor, llm_service = self._make_executor(EchoTool())

        tool_call = {
            "id": "call-loop",
            "function": {"name": "echo", "arguments": '{"message": "x"}'},
        }
        looping = make_llm_response("", tool_calls=[tool_call])
        final = make_llm_response("max reached", tool_calls=[])

        # max_iterations=2 → 2 looping calls, then 1 final call without tools
        llm_service.generate_with_tools.side_effect = [looping, looping, final]

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            max_iterations=2,
        ))

        token_events = [e for e in events if e["type"] == "token"]
        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1
        assert len(token_events) == 1
        assert token_events[0]["data"]["content"] == "max reached"
        assert done_events[0]["data"]["full_content"] == "max reached"
        assert len(done_events[0]["data"]["tool_executions"]) == 2

    @pytest.mark.asyncio
    async def test_original_messages_not_mutated_stream(self):
        """execute_with_tools_stream should not mutate the original messages list."""
        executor, llm_service = self._make_executor(EchoTool())
        llm_service.generate_with_tools.return_value = make_llm_response("ok", tool_calls=[])
        llm_service.stream_with_tools = make_streaming_llm(["ok"])

        original = [{"role": "user", "content": "hello"}]
        original_copy = list(original)

        await collect_stream_events(executor.execute_with_tools_stream(
            messages=original,
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        ))

        assert original == original_copy


# ---------------------------------------------------------------------------
# Approval-flow tool helpers
# ---------------------------------------------------------------------------


class ApprovalTool(BaseTool):
    """Tool that requires user approval before applying changes."""

    @property
    def name(self) -> str:
        return "approval_tool"

    @property
    def description(self) -> str:
        return "A tool that needs approval."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}

    @property
    def requires_approval(self) -> bool:
        return True

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        """Returns a preview (does not apply)."""
        return ToolResult(
            success=True,
            data=f"Preview: would apply value={kwargs.get('value', '')}",
        )

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        """Applies the change."""
        return ToolResult(
            success=True,
            data=f"Applied: value={kwargs.get('value', '')}",
        )


class ApprovalToolWithFailingPreview(ApprovalTool):
    """Approval tool whose execute() (preview) fails."""

    @property
    def name(self) -> str:
        return "fail_preview_tool"

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        return ToolResult(success=False, data="", error="preview failed")


# ---------------------------------------------------------------------------
# BaseTool approval-related tests
# ---------------------------------------------------------------------------


class TestBaseToolApproval:
    def test_requires_approval_default_false(self):
        assert EchoTool().requires_approval is False

    def test_requires_approval_overridden_true(self):
        assert ApprovalTool().requires_approval is True

    @pytest.mark.asyncio
    async def test_execute_confirmed_raises_by_default(self):
        """BaseTool.execute_confirmed raises NotImplementedError unless overridden."""
        tool = EchoTool()
        ctx = make_context()
        with pytest.raises(NotImplementedError):
            await tool.execute_confirmed(ctx)

    @pytest.mark.asyncio
    async def test_execute_confirmed_works_when_overridden(self):
        tool = ApprovalTool()
        ctx = make_context()
        result = await tool.execute_confirmed(ctx, value="test")
        assert result.success is True
        assert "Applied" in result.data


# ---------------------------------------------------------------------------
# ToolSource tests
# ---------------------------------------------------------------------------


class TestToolSource:
    def test_required_fields(self):
        src = ToolSource(source_type="url", title="My Source")
        assert src.source_type == "url"
        assert src.title == "My Source"
        assert src.subtitle is None
        assert src.url is None

    def test_all_fields(self):
        src = ToolSource(
            source_type="document",
            title="Doc",
            subtitle="sub",
            description="desc",
            url="https://example.com",
            icon="icon.png",
        )
        assert src.url == "https://example.com"
        assert src.icon == "icon.png"


# ---------------------------------------------------------------------------
# ToolResult sources field
# ---------------------------------------------------------------------------


class TestToolResultSources:
    def test_sources_defaults_to_none(self):
        result = ToolResult(success=True, data="ok")
        assert result.sources is None

    def test_sources_can_be_set(self):
        src = ToolSource(source_type="url", title="T")
        result = ToolResult(success=True, data="ok", sources=[src])
        assert len(result.sources) == 1
        assert result.sources[0].title == "T"


# ---------------------------------------------------------------------------
# ToolExecution pending_approval field
# ---------------------------------------------------------------------------


class TestToolExecutionPendingApproval:
    def test_pending_approval_defaults_false(self):
        result = ToolResult(success=True, data="ok")
        exec_ = ToolExecution(tool_name="echo", arguments={}, result=result, duration_ms=1)
        assert exec_.pending_approval is False

    def test_pending_approval_can_be_set(self):
        result = ToolResult(success=True, data="preview")
        exec_ = ToolExecution(tool_name="approval_tool", arguments={}, result=result, duration_ms=1, pending_approval=True)
        assert exec_.pending_approval is True


# ---------------------------------------------------------------------------
# ToolExecutor approval flow — non-streaming
# ---------------------------------------------------------------------------


class TestToolExecutorApprovalNonStreaming:
    def _make_executor(self, tool: BaseTool):
        registry = ToolRegistry()
        registry.register(tool)
        llm_service = AsyncMock()
        return ToolExecutor(tool_registry=registry, llm_service=llm_service), llm_service

    @pytest.mark.asyncio
    async def test_approval_tool_pauses_loop(self):
        """When requires_approval tool is called, the loop stops and returns pending=True."""
        executor, llm_service = self._make_executor(ApprovalTool())

        tool_call = {
            "id": "call-1",
            "function": {"name": "approval_tool", "arguments": '{"value": "x"}'},
        }
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[tool_call]),
        ]

        response, executions = await executor.execute_with_tools(
            messages=[{"role": "user", "content": "do it"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        )

        # Loop must have stopped — only 1 LLM call made
        assert llm_service.generate_with_tools.call_count == 1
        assert len(executions) == 1
        assert executions[0].pending_approval is True
        assert executions[0].result.success is True
        assert "Preview" in executions[0].result.data
        # Response content is empty (loop did not get a final LLM response)
        assert response.content == ""

    @pytest.mark.asyncio
    async def test_failed_preview_does_not_pause_loop(self):
        """If the preview (execute()) fails, the tool should NOT trigger approval pause."""
        executor, llm_service = self._make_executor(ApprovalToolWithFailingPreview())

        tool_call = {
            "id": "call-1",
            "function": {"name": "fail_preview_tool", "arguments": '{"value": "x"}'},
        }
        # Second call returns a final answer after the failed tool result is fed back
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("could not preview"),
        ]

        response, executions = await executor.execute_with_tools(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        )

        assert executions[0].pending_approval is False
        assert response.content == "could not preview"

    @pytest.mark.asyncio
    async def test_execute_tool_confirmed_success(self):
        executor, _ = self._make_executor(ApprovalTool())
        result = await executor.execute_tool_confirmed(
            "approval_tool", make_context(), {"value": "hello"}
        )
        assert result.success is True
        assert "Applied" in result.data

    @pytest.mark.asyncio
    async def test_execute_tool_confirmed_unknown_tool(self):
        executor, _ = self._make_executor(ApprovalTool())
        result = await executor.execute_tool_confirmed(
            "nonexistent", make_context(), {}
        )
        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_execute_tool_confirmed_non_approval_tool(self):
        """Calling execute_tool_confirmed on a non-approval tool returns an error."""
        executor, _ = self._make_executor(EchoTool())
        result = await executor.execute_tool_confirmed(
            "echo", make_context(), {"message": "hi"}
        )
        assert result.success is False
        assert "does not require approval" in result.error

    @pytest.mark.asyncio
    async def test_execute_tool_confirmed_execute_confirmed_raises(self):
        """If execute_confirmed raises, the error is caught and returned."""
        class BrokenConfirmTool(ApprovalTool):
            @property
            def name(self) -> str:
                return "broken_confirm"

            async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
                raise RuntimeError("confirm boom")

        executor, _ = self._make_executor(BrokenConfirmTool())
        result = await executor.execute_tool_confirmed("broken_confirm", make_context(), {})
        assert result.success is False
        assert "confirm boom" in result.error
        assert "broken_confirm" in result.error
        assert result.error != "Confirmed execution failed: confirm boom"

    @pytest.mark.asyncio
    async def test_on_tool_event_receives_pending_approval_flag(self):
        """on_tool_event callback should receive pending_approval=True for approval tools."""
        executor, llm_service = self._make_executor(ApprovalTool())

        tool_call = {
            "id": "call-1",
            "function": {"name": "approval_tool", "arguments": '{"value": "z"}'},
        }
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[tool_call]),
        ]

        events = []
        def capture(event_type, data):
            events.append((event_type, data))

        await executor.execute_with_tools(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            on_tool_event=capture,
        )

        end_events = [d for t, d in events if t == "tool_end"]
        assert len(end_events) == 1
        assert end_events[0]["pending_approval"] is True
        assert "arguments" in end_events[0]


# ---------------------------------------------------------------------------
# ToolExecutor approval flow — streaming
# ---------------------------------------------------------------------------


class TestToolExecutorApprovalStream:
    def _make_executor(self, tool: BaseTool):
        registry = ToolRegistry()
        registry.register(tool)
        llm_service = AsyncMock()
        return ToolExecutor(tool_registry=registry, llm_service=llm_service), llm_service

    @pytest.mark.asyncio
    async def test_approval_tool_emits_pending_done_and_stops(self):
        """Streaming: approval tool emits tool_end with pending=True then done with pending_tool_approval=True."""
        executor, llm_service = self._make_executor(ApprovalTool())

        tool_call = {
            "id": "call-1",
            "function": {"name": "approval_tool", "arguments": '{"value": "abc"}'},
        }
        llm_service.generate_with_tools.return_value = make_llm_response("", tool_calls=[tool_call])

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "go"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        ))

        # LLM called only once (loop stopped)
        assert llm_service.generate_with_tools.call_count == 1

        event_types = [e["type"] for e in events]
        assert "tool_start" in event_types
        assert "tool_end" in event_types
        assert "done" in event_types
        # No token events — the loop stopped before any final LLM response
        assert "token" not in event_types

        tool_end = next(e for e in events if e["type"] == "tool_end")
        assert tool_end["data"]["pending_approval"] is True
        assert "arguments" in tool_end["data"]

        done = next(e for e in events if e["type"] == "done")
        assert done["data"]["pending_tool_approval"] is True
        assert len(done["data"]["tool_executions"]) == 1
        assert done["data"]["tool_executions"][0].pending_approval is True

    @pytest.mark.asyncio
    async def test_normal_tool_done_has_pending_false(self):
        """Non-approval tool streaming: done event has pending_tool_approval=False."""
        executor, llm_service = self._make_executor(EchoTool())

        tool_call = {
            "id": "call-1",
            "function": {"name": "echo", "arguments": '{"message": "hi"}'},
        }
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("done", tool_calls=[]),
        ]

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        ))

        done = next(e for e in events if e["type"] == "done")
        assert done["data"]["pending_tool_approval"] is False

    @pytest.mark.asyncio
    async def test_tool_end_event_includes_sources(self):
        """tool_end event should include sources when the tool result has them."""
        class SourceTool(EchoTool):
            @property
            def name(self) -> str:
                return "source_tool"

            async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
                return ToolResult(
                    success=True,
                    data="result",
                    sources=[ToolSource(source_type="url", title="Example", url="https://example.com")],
                )

        registry = ToolRegistry()
        registry.register(SourceTool())
        llm_service = AsyncMock()
        executor = ToolExecutor(tool_registry=registry, llm_service=llm_service)

        tool_call = {
            "id": "call-s",
            "function": {"name": "source_tool", "arguments": '{"message": "x"}'},
        }
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("final", tool_calls=[]),
        ]

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        ))

        tool_end = next(e for e in events if e["type"] == "tool_end")
        assert "sources" in tool_end["data"]
        assert tool_end["data"]["sources"][0]["title"] == "Example"
        assert tool_end["data"]["sources"][0]["url"] == "https://example.com"


# ---------------------------------------------------------------------------
# ToolExecutor.execute_with_tools_stream — native per-iteration streaming
#
# (B5 speed pass) When the LLM config isn't force_prompt_tools, the executor
# streams every iteration live via llm_service.stream_with_tools() instead of
# calling the non-streaming generate_with_tools() and emitting the answer as
# one blob. See ToolExecutor._force_prompt_tools_for.
# ---------------------------------------------------------------------------

def make_config(provider_options=None):
    """A bare config double with just the attribute _force_prompt_tools_for reads."""
    cfg = MagicMock()
    cfg.provider_options = provider_options or {}
    return cfg


def make_multi_call_event_stream(call_event_lists):
    """Return a plain callable that, on each call, returns a fresh async generator
    yielding the next pre-built list of event dicts — one list per expected call to
    llm_service.stream_with_tools (one per tool-loop iteration)."""
    state = {"n": 0}

    def _factory(*args, **kwargs):
        events = call_event_lists[state["n"]]
        state["n"] += 1

        async def _gen():
            for event in events:
                yield event

        return _gen()

    return _factory


class TestNativeConfigForcesLegacyPath:
    """A `type == "native"` config always uses the buffered legacy path,
    regardless of provider_options.force_prompt_tools -- NativeLLMClient never
    returns structured tool_calls (it's always prompt-injected XML), so
    leaving this opt-in let a native config fall through to the live
    per-token streaming path, where a `<tool_call>` block -- complete or not
    -- reaches the user as raw text before this executor ever looks at it."""

    def _make_executor(self, tool: BaseTool = None, config_type: str = "native", provider_options=None):
        registry = ToolRegistry()
        if tool:
            registry.register(tool)
        llm_service = AsyncMock()
        cfg = MagicMock()
        cfg.type = config_type
        cfg.provider_options = provider_options or {}
        llm_service.repository.get_configuration = MagicMock(return_value=cfg)
        return ToolExecutor(tool_registry=registry, llm_service=llm_service), llm_service

    @pytest.mark.asyncio
    async def test_native_type_uses_legacy_path_without_the_admin_flag(self):
        executor, llm_service = self._make_executor(EchoTool(), config_type="native")
        llm_service.generate_with_tools.return_value = make_llm_response("buffered answer", tool_calls=[])
        # If the native streaming path were used by mistake, this would be
        # consulted instead and (having no side_effect queued) would raise —
        # a failure here makes a wrong routing decision loud, not silent.
        llm_service.stream_with_tools = make_multi_call_event_stream([])

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "hi"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        ))

        done = next(e for e in events if e["type"] == "done")
        assert done["data"]["full_content"] == "buffered answer"
        assert llm_service.generate_with_tools.call_count == 1

    @pytest.mark.asyncio
    async def test_non_native_type_without_flag_still_uses_streaming_path(self):
        """The admin-set opt-in still governs other providers — this fix is
        specific to type == "native", not a blanket behavior change."""
        executor, llm_service = self._make_executor(config_type="ollama")
        llm_service.stream_with_tools = make_multi_call_event_stream([[
            {"type": "token", "content": "hi there"},
        ]])

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "hi"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        ))

        done = next(e for e in events if e["type"] == "done")
        assert done["data"]["full_content"] == "hi there"
        assert llm_service.generate_with_tools.call_count == 0

    @pytest.mark.asyncio
    async def test_complete_tool_call_from_a_native_config_never_leaks_as_live_tokens(self):
        """The reported symptom: a native LLM's complete,
        well-formed <tool_call> block must dispatch as a real tool call and
        never show up as literal text in a streamed token."""
        executor, llm_service = self._make_executor(EchoTool(), config_type="native")
        call = '<tool_call>{"name": "echo", "arguments": {"message": "hi"}}</tool_call>'
        llm_service.generate_with_tools.side_effect = [
            make_llm_response(call, tool_calls=[]),
            make_llm_response("echo: hi", tool_calls=[]),
        ]
        llm_service.stream_with_tools = make_multi_call_event_stream([])

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "echo hi"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            allowed_tools=["echo"],
        ))

        assert all("<tool_call>" not in e["data"]["content"] for e in events if e["type"] == "token")
        assert [e["data"]["tool_name"] for e in events if e["type"] == "tool_start"] == ["echo"]
        done = next(e for e in events if e["type"] == "done")
        assert done["data"]["full_content"] == "echo: hi"


class TestToolExecutorNativeStream:
    def _make_executor(self, tool: BaseTool = None, provider_options=None):
        registry = ToolRegistry()
        if tool:
            registry.register(tool)
        llm_service = AsyncMock()
        # A plain (non-async) callable standing in for the real, synchronous
        # LLMRepository.get_configuration — an AsyncMock chain here would make
        # every config attribute access truthy and force the legacy path.
        llm_service.repository.get_configuration = MagicMock(
            return_value=make_config(provider_options)
        )
        return ToolExecutor(tool_registry=registry, llm_service=llm_service), llm_service

    @pytest.mark.asyncio
    async def test_streams_multiple_token_events_live(self):
        """No tool calls: content streams as multiple token events, not one blob."""
        executor, llm_service = self._make_executor()
        llm_service.stream_with_tools = make_multi_call_event_stream([[
            {"type": "token", "content": "Hel"},
            {"type": "token", "content": "lo"},
            {"type": "token", "content": "!"},
            {"type": "usage", "tokens_used": 10, "prompt_tokens": 6, "completion_tokens": 4},
        ]])

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "hi"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        ))

        token_events = [e for e in events if e["type"] == "token"]
        assert [e["data"]["content"] for e in token_events] == ["Hel", "lo", "!"]
        assert len(token_events) > 1  # the whole point: not a single blob

        done = next(e for e in events if e["type"] == "done")
        assert done["data"]["full_content"] == "Hello!"
        assert done["data"]["tokens_used"] == 10
        assert done["data"]["prompt_tokens"] == 6
        assert done["data"]["completion_tokens"] == 4
        # The old non-streaming per-iteration call must not happen at all.
        assert llm_service.generate_with_tools.call_count == 0

    @pytest.mark.asyncio
    async def test_tool_calls_event_executes_tool_then_streams_final_answer(self):
        """A tool_calls event drives tool_start/tool_end; the next iteration's answer streams live."""
        executor, llm_service = self._make_executor(EchoTool())

        tool_call = {"id": "call-1", "function": {"name": "echo", "arguments": '{"message": "hi"}'}}
        llm_service.stream_with_tools = make_multi_call_event_stream([
            [
                {"type": "tool_calls", "tool_calls": [tool_call]},
                {"type": "usage", "tokens_used": None, "prompt_tokens": None, "completion_tokens": None},
            ],
            [
                {"type": "token", "content": "final "},
                {"type": "token", "content": "answer"},
                {"type": "usage", "tokens_used": 5, "prompt_tokens": 2, "completion_tokens": 3},
            ],
        ])

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "echo test"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        ))

        event_types = [e["type"] for e in events]
        assert "tool_start" in event_types
        assert "tool_end" in event_types

        token_events = [e for e in events if e["type"] == "token"]
        assert [e["data"]["content"] for e in token_events] == ["final ", "answer"]

        done = next(e for e in events if e["type"] == "done")
        assert done["data"]["full_content"] == "final answer"
        assert done["data"]["tokens_used"] == 5
        assert len(done["data"]["tool_executions"]) == 1
        assert done["data"]["tool_executions"][0].tool_name == "echo"
        assert llm_service.generate_with_tools.call_count == 0

    @pytest.mark.asyncio
    async def test_approval_flow_preserved_in_native_stream(self):
        """requires_approval tools still pause the loop with pending_tool_approval=True."""
        executor, llm_service = self._make_executor(ApprovalTool())

        tool_call = {"id": "call-1", "function": {"name": "approval_tool", "arguments": '{"value": "abc"}'}}
        llm_service.stream_with_tools = make_multi_call_event_stream([[
            {"type": "tool_calls", "tool_calls": [tool_call]},
            {"type": "usage", "tokens_used": None, "prompt_tokens": None, "completion_tokens": None},
        ]])

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "go"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        ))

        event_types = [e["type"] for e in events]
        assert "tool_start" in event_types
        assert "tool_end" in event_types
        assert "token" not in event_types  # loop paused before any answer streamed

        tool_end = next(e for e in events if e["type"] == "tool_end")
        assert tool_end["data"]["pending_approval"] is True

        done = next(e for e in events if e["type"] == "done")
        assert done["data"]["pending_tool_approval"] is True
        assert len(done["data"]["tool_executions"]) == 1
        assert done["data"]["tool_executions"][0].pending_approval is True

    @pytest.mark.asyncio
    async def test_force_prompt_tools_config_uses_legacy_buffered_path(self):
        """force_prompt_tools configs must not go through the native streaming path."""
        executor, llm_service = self._make_executor(
            EchoTool(), provider_options={"force_prompt_tools": True}
        )
        llm_service.generate_with_tools.return_value = make_llm_response("legacy answer", tool_calls=[])
        # If the native path were used by mistake, this would be consulted and
        # (having no side_effect queued) would raise StopAsyncIteration weirdness;
        # asserting it's never called makes the routing decision explicit.
        llm_service.stream_with_tools = make_multi_call_event_stream([])

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "hi"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        ))

        done = next(e for e in events if e["type"] == "done")
        assert done["data"]["full_content"] == "legacy answer"
        assert llm_service.generate_with_tools.call_count == 1

    @pytest.mark.asyncio
    async def test_user_image_persists_across_native_stream_iterations(self):
        """Native streaming loop: the user's image must not be dropped after
        iteration 1 — see the identical assertion for execute_with_tools."""
        executor, llm_service = self._make_executor(EchoTool())

        tool_call = {"id": "call-1", "function": {"name": "echo", "arguments": '{"message": "hi"}'}}
        factory = make_multi_call_event_stream([
            [
                {"type": "tool_calls", "tool_calls": [tool_call]},
                {"type": "usage", "tokens_used": None, "prompt_tokens": None, "completion_tokens": None},
            ],
            [
                {"type": "token", "content": "final"},
                {"type": "usage", "tokens_used": 1, "prompt_tokens": 1, "completion_tokens": 1},
            ],
        ])
        captured_image_args = []

        def _capturing_factory(*args, **kwargs):
            captured_image_args.append(kwargs.get("image_data"))
            return factory(*args, **kwargs)

        llm_service.stream_with_tools = _capturing_factory

        await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "echo test"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            image_data="base64img",
        ))

        assert captured_image_args == ["base64img", "base64img"]

    @pytest.mark.asyncio
    async def test_tool_result_image_precedence_in_native_stream(self):
        """Native streaming loop: a tool-returned image overrides the user's
        image for exactly the next call, then the loop reverts."""
        registry = ToolRegistry()
        registry.register(ImageResultTool())
        registry.register(EchoTool())
        llm_service = AsyncMock()
        llm_service.repository.get_configuration = MagicMock(return_value=make_config())
        executor = ToolExecutor(tool_registry=registry, llm_service=llm_service)

        image_tool_call = {"id": "c1", "function": {"name": "image_tool", "arguments": "{}"}}
        echo_tool_call = {"id": "c2", "function": {"name": "echo", "arguments": '{"message": "x"}'}}
        factory = make_multi_call_event_stream([
            [
                {"type": "tool_calls", "tool_calls": [image_tool_call]},
                {"type": "usage", "tokens_used": None, "prompt_tokens": None, "completion_tokens": None},
            ],
            [
                {"type": "tool_calls", "tool_calls": [echo_tool_call]},
                {"type": "usage", "tokens_used": None, "prompt_tokens": None, "completion_tokens": None},
            ],
            [
                {"type": "token", "content": "done"},
                {"type": "usage", "tokens_used": 1, "prompt_tokens": 1, "completion_tokens": 1},
            ],
        ])
        captured_image_args = []

        def _capturing_factory(*args, **kwargs):
            captured_image_args.append(kwargs.get("image_data"))
            return factory(*args, **kwargs)

        llm_service.stream_with_tools = _capturing_factory

        await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "hi"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            image_data="user_img",
        ))

        assert captured_image_args == ["user_img", "tool_img", "user_img"]


# ---------------------------------------------------------------------------
# ToolExecutor._execute_with_tools_stream_legacy (force_prompt_tools configs)
#
# Same image-persistence contract as execute_with_tools / execute_with_tools_stream,
# tested directly against the legacy buffered-streaming path since it has its
# own independent (now-fixed) copy of the image bookkeeping.
# ---------------------------------------------------------------------------


class TestToolExecutorLegacyStreamImagePersistence:
    def _make_executor(self, tool: BaseTool = None):
        registry = ToolRegistry()
        if tool:
            registry.register(tool)
        llm_service = AsyncMock()
        llm_service.repository.get_configuration = MagicMock(
            return_value=make_config({"force_prompt_tools": True})
        )
        return ToolExecutor(tool_registry=registry, llm_service=llm_service), llm_service

    @pytest.mark.asyncio
    async def test_user_image_persists_across_legacy_stream_iterations(self):
        executor, llm_service = self._make_executor(EchoTool())

        tool_call = {"id": "c1", "function": {"name": "echo", "arguments": '{"message": "hi"}'}}
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("done", tool_calls=[]),
        ]

        await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "hi"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            image_data="base64img",
        ))

        calls = llm_service.generate_with_tools.call_args_list
        assert calls[0][1]["image_data"] == "base64img"
        assert calls[1][1]["image_data"] == "base64img"

    @pytest.mark.asyncio
    async def test_tool_result_image_precedence_in_legacy_stream(self):
        registry = ToolRegistry()
        registry.register(ImageResultTool())
        registry.register(EchoTool())
        llm_service = AsyncMock()
        llm_service.repository.get_configuration = MagicMock(
            return_value=make_config({"force_prompt_tools": True})
        )
        executor = ToolExecutor(tool_registry=registry, llm_service=llm_service)

        image_tool_call = {"id": "c1", "function": {"name": "image_tool", "arguments": "{}"}}
        echo_tool_call = {"id": "c2", "function": {"name": "echo", "arguments": '{"message": "x"}'}}
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[image_tool_call]),
            make_llm_response("", tool_calls=[echo_tool_call]),
            make_llm_response("done", tool_calls=[]),
        ]

        await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "hi"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            image_data="user_img",
        ))

        calls = llm_service.generate_with_tools.call_args_list
        assert calls[0][1]["image_data"] == "user_img"
        assert calls[1][1]["image_data"] == "tool_img"
        assert calls[2][1]["image_data"] == "user_img"


# ---------------------------------------------------------------------------
# iteration_nudge: a mid-loop reminder appended as a trailing system message
# once the turn's first tool round has completed, so the "do not narrate,
# just call the tool" rule doesn't lose to recency as rounds accumulate. It
# must never be written into `working_messages` itself (that would let it
# stack or go stale) — only synthesized fresh for each outgoing call.
# ---------------------------------------------------------------------------

_NUDGE = "NUDGE: call the next tool now."


class TestToolExecutorIterationNudge:
    def _make_executor(self, tool: BaseTool = None):
        registry = ToolRegistry()
        if tool:
            registry.register(tool)
        llm_service = AsyncMock()
        # A plain (non-async) callable standing in for the real, synchronous
        # LLMRepository.get_configuration — an AsyncMock chain here would make
        # every config attribute access truthy and force the legacy path (see
        # TestToolExecutorNativeStream._make_executor).
        llm_service.repository.get_configuration = MagicMock(return_value=make_config())
        return ToolExecutor(tool_registry=registry, llm_service=llm_service), llm_service

    @pytest.mark.asyncio
    async def test_buffered_nudge_absent_first_call_then_last_each_round_after(self):
        executor, llm_service = self._make_executor(EchoTool())
        tool_call = {"id": "c1", "function": {"name": "echo", "arguments": '{"message": "x"}'}}
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("done"),
        ]

        await executor.execute_with_tools(
            messages=[{"role": "user", "content": "go"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            iteration_nudge=_NUDGE,
        )

        calls = llm_service.generate_with_tools.call_args_list
        first, second, third = (c.kwargs["messages"] for c in calls)

        assert not any(m.get("content") == _NUDGE for m in first)
        assert second[-1] == {"role": "system", "content": _NUDGE}
        assert third[-1] == {"role": "system", "content": _NUDGE}
        # Never stacked — exactly one nudge message even after two rounds.
        assert sum(1 for m in third if m.get("content") == _NUDGE) == 1

    @pytest.mark.asyncio
    async def test_buffered_nudge_absent_when_none(self):
        executor, llm_service = self._make_executor(EchoTool())
        tool_call = {"id": "c1", "function": {"name": "echo", "arguments": '{"message": "x"}'}}
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("done"),
        ]

        await executor.execute_with_tools(
            messages=[{"role": "user", "content": "go"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        )

        for c in llm_service.generate_with_tools.call_args_list:
            assert not any(m.get("content") == _NUDGE for m in c.kwargs["messages"])

    @pytest.mark.asyncio
    async def test_native_stream_nudge_absent_first_call_then_last_each_round_after(self):
        executor, llm_service = self._make_executor(EchoTool())
        tool_call = {"id": "call-1", "function": {"name": "echo", "arguments": '{"message": "hi"}'}}
        factory = make_multi_call_event_stream([
            [{"type": "tool_calls", "tool_calls": [tool_call]}],
            [{"type": "tool_calls", "tool_calls": [tool_call]}],
            [{"type": "token", "content": "done"}],
        ])
        captured = []

        def _capturing_factory(*args, **kwargs):
            captured.append(kwargs["messages"])
            return factory(*args, **kwargs)

        llm_service.stream_with_tools = _capturing_factory

        await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "go"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            iteration_nudge=_NUDGE,
        ))

        first, second, third = captured
        assert not any(m.get("content") == _NUDGE for m in first)
        assert second[-1] == {"role": "system", "content": _NUDGE}
        assert third[-1] == {"role": "system", "content": _NUDGE}
        assert sum(1 for m in third if m.get("content") == _NUDGE) == 1

    @pytest.mark.asyncio
    async def test_legacy_stream_nudge_absent_first_call_then_last_each_round_after(self):
        registry = ToolRegistry()
        registry.register(EchoTool())
        llm_service = AsyncMock()
        llm_service.repository.get_configuration = MagicMock(
            return_value=make_config({"force_prompt_tools": True})
        )
        executor = ToolExecutor(tool_registry=registry, llm_service=llm_service)

        tool_call = {"id": "c1", "function": {"name": "echo", "arguments": '{"message": "hi"}'}}
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("done", tool_calls=[]),
        ]

        await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "go"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            iteration_nudge=_NUDGE,
        ))

        calls = llm_service.generate_with_tools.call_args_list
        first, second, third = (c.kwargs["messages"] for c in calls)
        assert not any(m.get("content") == _NUDGE for m in first)
        assert second[-1] == {"role": "system", "content": _NUDGE}
        assert third[-1] == {"role": "system", "content": _NUDGE}
        assert sum(1 for m in third if m.get("content") == _NUDGE) == 1


# ---------------------------------------------------------------------------
# Cap exhaustion: hitting max_iterations must never be a silent forced
# finish. Both streaming loops emit a visible status event alongside the
# existing logger.warning and inject a wrap-up instruction into the final,
# tool-free call.
# ---------------------------------------------------------------------------

class TestToolExecutorCapExhaustionSignal:
    def _make_executor(self, tool: BaseTool = None):
        registry = ToolRegistry()
        if tool:
            registry.register(tool)
        llm_service = AsyncMock()
        # See TestToolExecutorNativeStream._make_executor: a plain callable
        # standing in for the real synchronous get_configuration, or an
        # AsyncMock chain here would force the legacy path.
        llm_service.repository.get_configuration = MagicMock(return_value=make_config())
        return ToolExecutor(tool_registry=registry, llm_service=llm_service), llm_service

    @pytest.mark.asyncio
    async def test_native_stream_emits_status_and_injects_wrapup_message(self):
        executor, llm_service = self._make_executor(EchoTool())
        tool_call = {"id": "call-loop", "function": {"name": "echo", "arguments": '{"message": "x"}'}}
        looping = [{"type": "tool_calls", "tool_calls": [tool_call]}]
        final = [{"type": "token", "content": "giving up"}]

        factory = make_multi_call_event_stream([looping, looping, final])
        captured = []

        def _capturing_factory(*args, **kwargs):
            captured.append(kwargs["messages"])
            return factory(*args, **kwargs)

        llm_service.stream_with_tools = _capturing_factory

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "go"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            max_iterations=2,
        ))

        status_events = [e for e in events if e["type"] == "status" and e["data"]["step"] == "tool_budget_exhausted"]
        assert len(status_events) == 1

        final_call_messages = captured[-1]
        assert final_call_messages[-1] == {
            "role": "system",
            "content": ToolExecutor._TOOL_BUDGET_EXHAUSTED_MESSAGE,
        }

    @pytest.mark.asyncio
    async def test_legacy_stream_emits_status_and_injects_wrapup_message(self):
        registry = ToolRegistry()
        registry.register(EchoTool())
        llm_service = AsyncMock()
        llm_service.repository.get_configuration = MagicMock(
            return_value=make_config({"force_prompt_tools": True})
        )
        executor = ToolExecutor(tool_registry=registry, llm_service=llm_service)

        tool_call = {"id": "call-loop", "function": {"name": "echo", "arguments": '{"message": "x"}'}}
        looping = make_llm_response("", tool_calls=[tool_call])
        final = make_llm_response("giving up", tool_calls=[])
        llm_service.generate_with_tools.side_effect = [looping, looping, final]

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "go"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            max_iterations=2,
        ))

        status_events = [e for e in events if e["type"] == "status" and e["data"]["step"] == "tool_budget_exhausted"]
        assert len(status_events) == 1

        last_call_messages = llm_service.generate_with_tools.call_args_list[-1].kwargs["messages"]
        assert last_call_messages[-1] == {
            "role": "system",
            "content": ToolExecutor._TOOL_BUDGET_EXHAUSTED_MESSAGE,
        }


# ---------------------------------------------------------------------------
# Forced tool calls: a caller can name a tool to run as the turn's first
# action; the executor runs it, then the loop lets the model present the
# result — one code path with a model-chosen call.
# ---------------------------------------------------------------------------

class TestForcedToolCall:
    def _make_executor(self, tool: BaseTool = None, provider_options=None):
        registry = ToolRegistry()
        if tool:
            registry.register(tool)
        llm_service = AsyncMock()
        llm_service.repository.get_configuration = MagicMock(
            return_value=make_config(provider_options)
        )
        return ToolExecutor(tool_registry=registry, llm_service=llm_service), llm_service

    @pytest.mark.asyncio
    async def test_buffered_runs_forced_tool_before_llm(self):
        executor, llm_service = self._make_executor(EchoTool())
        # The model's first (and only) turn returns a plain answer with no calls.
        llm_service.generate_with_tools.return_value = make_llm_response("presented", tool_calls=[])

        response, executions = await executor.execute_with_tools(
            messages=[{"role": "user", "content": "/enhance a fox"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            allowed_tools=["echo"],
            forced_tool_call={"name": "echo", "arguments": {"message": "forced"}},
        )

        # The forced tool ran (once) and is the only recorded execution.
        assert [e.tool_name for e in executions] == ["echo"]
        assert executions[0].result.data == "echo: forced"
        # Its result was fed to the model, which then produced the final answer.
        sent_messages = llm_service.generate_with_tools.call_args[1]["messages"]
        assert any(m.get("role") == "tool" and m.get("name") == "echo" for m in sent_messages)
        assert response.content == "presented"

    @pytest.mark.asyncio
    async def test_forced_tool_ignores_allowed_tools_guard_only_when_listed(self):
        """The runner adds the forced tool to allowed_tools; if it isn't, the guard rejects it."""
        executor, _ = self._make_executor(EchoTool())
        executor.llm_service.generate_with_tools.return_value = make_llm_response("done", tool_calls=[])

        _, executions = await executor.execute_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            allowed_tools=["something_else"],
            forced_tool_call={"name": "echo", "arguments": {"message": "x"}},
        )
        # Guard rejects the un-listed forced tool with a recoverable error result.
        assert executions[0].tool_name == "echo"
        assert executions[0].result.success is False
        assert "not enabled" in executions[0].result.error

    @pytest.mark.asyncio
    async def test_native_stream_emits_forced_tool_events_then_presents(self):
        executor, llm_service = self._make_executor(EchoTool())
        llm_service.stream_with_tools = make_multi_call_event_stream([[
            {"type": "token", "content": "here "},
            {"type": "token", "content": "you go"},
            {"type": "usage", "tokens_used": 3, "prompt_tokens": 1, "completion_tokens": 2},
        ]])

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "/enhance"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            allowed_tools=["echo"],
            forced_tool_call={"name": "echo", "arguments": {"message": "forced"}},
        ))

        starts = [e for e in events if e["type"] == "tool_start"]
        assert starts and starts[0]["data"]["tool_name"] == "echo"
        token_events = [e for e in events if e["type"] == "token"]
        assert [e["data"]["content"] for e in token_events] == ["here ", "you go"]
        done = next(e for e in events if e["type"] == "done")
        assert [te.tool_name for te in done["data"]["tool_executions"]] == ["echo"]

    @pytest.mark.asyncio
    async def test_legacy_stream_emits_forced_tool_events(self):
        """force_prompt_tools configs run the forced tool through the legacy stream path too."""
        executor, llm_service = self._make_executor(
            EchoTool(), provider_options={"force_prompt_tools": True}
        )
        llm_service.generate_with_tools.return_value = make_llm_response("presented", tool_calls=[])

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "/enhance"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            allowed_tools=["echo"],
            forced_tool_call={"name": "echo", "arguments": {"message": "forced"}},
        ))

        starts = [e for e in events if e["type"] == "tool_start"]
        assert starts and starts[0]["data"]["tool_name"] == "echo"
        done = next(e for e in events if e["type"] == "done")
        assert [te.tool_name for te in done["data"]["tool_executions"]] == ["echo"]


# ---------------------------------------------------------------------------
# Suppress-at-source: a <tool_call> embedded in a live token stream must
# never reach the client as text, and must dispatch the moment it closes.
# ---------------------------------------------------------------------------

class TestStreamToolCallFilter:
    """Unit-level: _StreamToolCallFilter's segmenting, independent of the executor."""

    def _make(self):
        from src.features.llm.tools.executor import _StreamToolCallFilter
        return _StreamToolCallFilter()

    def test_ordinary_text_forwards_immediately_with_no_holdback(self):
        f = self._make()
        assert f.feed("Hel") == [("text", "Hel")]
        assert f.feed("lo!") == [("text", "lo!")]
        assert f.flush() == ""

    def test_complete_block_in_one_chunk_is_suppressed_and_returned_whole(self):
        f = self._make()
        call = '<tool_call>{"name": "echo", "arguments": {}}</tool_call>'
        segments = f.feed(f"before {call} after")
        assert segments == [("text", "before "), ("block", call), ("text", " after")]
        assert not f.suppressing

    def test_open_tag_split_across_two_chunks_is_never_forwarded(self):
        f = self._make()
        first = f.feed("hi <tool_c")
        assert first == [("text", "hi ")]  # the partial tag prefix is held back, not forwarded
        second = f.feed('all>{"name": "echo", "arguments": {}}</tool_call> done')
        assert second == [
            ("block", '<tool_call>{"name": "echo", "arguments": {}}</tool_call>'),
            ("text", " done"),
        ]

    def test_unclosed_block_is_never_returned_and_flush_stays_hidden(self):
        f = self._make()
        segments = f.feed('before <tool_call>{"name": "echo"')
        assert segments == [("text", "before ")]
        assert f.suppressing is True
        assert f.flush() == ""  # the cut-off tag stays hidden, never forwarded

    def test_multiple_blocks_in_one_chunk_return_in_order(self):
        f = self._make()
        call_a = '<tool_call>{"name": "a", "arguments": {}}</tool_call>'
        call_b = '<tool_call>{"name": "b", "arguments": {}}</tool_call>'
        segments = f.feed(f"{call_a}mid{call_b}")
        assert segments == [("block", call_a), ("text", "mid"), ("block", call_b)]

    def test_trailing_angle_bracket_in_prose_is_not_held_back_forever(self):
        f = self._make()
        # "a < b" ends in a way that could theoretically start a tag were more
        # text to arrive, but none of it is an actual prefix of "<tool_call>".
        assert f.feed("compare a < b here") == [("text", "compare a < b here")]


class TestExecuteWithToolsStreamSuppression:
    """execute_with_tools_stream (the true native per-token path): a
    <tool_call> written in content must never reach the client as text."""

    def _make_executor(self, tool: BaseTool = None):
        registry = ToolRegistry()
        if tool:
            registry.register(tool)
        llm_service = AsyncMock()
        llm_service.repository.get_configuration = MagicMock(return_value=make_config())
        return ToolExecutor(tool_registry=registry, llm_service=llm_service), llm_service

    @pytest.mark.asyncio
    async def test_complete_call_split_across_chunks_never_leaks_a_tag_token(self):
        """The reported symptom: a complete, well-formed
        <tool_call> arriving as live streamed tokens must never appear in a
        token event, and must dispatch as a real tool call."""
        executor, llm_service = self._make_executor(EchoTool())
        call = '<tool_call>{"name": "echo", "arguments": {"message": "hi"}}</tool_call>'
        chunks = [call[i:i + 5] for i in range(0, len(call), 5)]
        llm_service.stream_with_tools = make_multi_call_event_stream([
            [{"type": "token", "content": c} for c in chunks],
            [{"type": "token", "content": "echo: hi"}],
        ])

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "echo hi"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            allowed_tools=["echo"],
        ))

        token_events = [e for e in events if e["type"] == "token"]
        assert all("<tool_call>" not in e["data"]["content"] for e in token_events)
        assert all("</tool_call>" not in e["data"]["content"] for e in token_events)
        assert "".join(e["data"]["content"] for e in token_events) == "echo: hi"
        assert [e["data"]["tool_name"] for e in events if e["type"] == "tool_start"] == ["echo"]
        tool_end = next(e for e in events if e["type"] == "tool_end")
        assert tool_end["data"]["success"] is True
        done = next(e for e in events if e["type"] == "done")
        assert done["data"]["full_content"] == "echo: hi"
        assert done["data"]["pending_tool_approval"] is False

    @pytest.mark.asyncio
    async def test_tool_start_fires_mid_generation_before_iteration_ends(self):
        """The tool-execution event surface must appear as soon as the block
        closes, not only after the whole iteration's text has streamed in."""
        executor, llm_service = self._make_executor(EchoTool())
        llm_service.stream_with_tools = make_multi_call_event_stream([
            [
                {"type": "token", "content": "one moment... "},
                {"type": "token", "content": '<tool_call>{"name": "echo", "arguments": {"message": "hi"}}</tool_call>'},
                {"type": "token", "content": " (still generating, unaware of the result)"},
            ],
            [{"type": "token", "content": "echo: hi"}],
        ])

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "echo hi"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            allowed_tools=["echo"],
        ))

        order = [e["type"] for e in events]
        first_tool_start = order.index("tool_start")
        # Text streamed before the block, then the dispatch events -- the
        # tool chip appears right where the tag opened, not only once the
        # rest of that iteration's generation (including the model's own
        # trailing, result-unaware text) has finished arriving.
        assert order[:1] == ["token"]
        assert order[first_tool_start:first_tool_start + 2] == ["tool_start", "tool_end"]
        assert order.index("token", first_tool_start) > first_tool_start
        token_events = [e for e in events if e["type"] == "token"]
        assert all("<tool_call>" not in e["data"]["content"] for e in token_events)

    @pytest.mark.asyncio
    async def test_multiple_calls_in_one_reply_execute_in_order(self):
        executor, llm_service = self._make_executor(EchoTool())
        calls = (
            '<tool_call>{"name": "echo", "arguments": {"message": "first"}}</tool_call>'
            '<tool_call>{"name": "echo", "arguments": {"message": "second"}}</tool_call>'
        )
        llm_service.stream_with_tools = make_multi_call_event_stream([
            [{"type": "token", "content": calls}],
            [{"type": "token", "content": "done"}],
        ])

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "echo twice"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            allowed_tools=["echo"],
        ))

        tool_starts = [e["data"]["arguments"] for e in events if e["type"] == "tool_start"]
        assert tool_starts == [{"message": "first"}, {"message": "second"}]
        token_events = [e for e in events if e["type"] == "token"]
        assert all("<tool_call>" not in e["data"]["content"] for e in token_events)

    @pytest.mark.asyncio
    async def test_pending_approval_inline_drains_without_further_dispatch(self):
        """A tool requiring approval, detected inline, pauses the turn like
        the structured path — nothing after it in the SAME generation (more
        text, or another call) is forwarded or dispatched."""
        executor, llm_service = self._make_executor(ApprovalTool())
        content = (
            '<tool_call>{"name": "approval_tool", "arguments": {"value": "x"}}</tool_call>'
            'the model rambles on after its own call, unaware of the result'
            '<tool_call>{"name": "approval_tool", "arguments": {"value": "y"}}</tool_call>'
        )
        llm_service.stream_with_tools = make_multi_call_event_stream([
            [{"type": "token", "content": content}],
        ])

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "do it"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            allowed_tools=["approval_tool"],
        ))

        assert [e["data"]["content"] for e in events if e["type"] == "token"] == []
        assert [e["type"] for e in events if e["type"] in ("tool_start", "tool_end")] == ["tool_start", "tool_end"]
        done = next(e for e in events if e["type"] == "done")
        assert done["data"]["pending_tool_approval"] is True
        assert done["data"]["full_content"] == ""
        assert len(done["data"]["tool_executions"]) == 1

    @pytest.mark.asyncio
    async def test_truncated_call_now_gets_a_corrective_retry_not_a_silent_finish(self):
        """Suppressed at the source, an unclosed block was never shown live —
        so unlike before, it is safe to ask the model to resend it."""
        executor, llm_service = self._make_executor(EchoTool())
        truncated = '<tool_call>{"name": "echo", "arguments": {"mess'
        stream_calls = MagicMock(side_effect=make_multi_call_event_stream([
            [{"type": "token", "content": truncated}],
            [{"type": "token", "content": "echo: hi"}],
        ]))
        llm_service.stream_with_tools = stream_calls

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "echo hi"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            allowed_tools=["echo"],
        ))

        assert all("<tool_call>" not in e["data"]["content"] for e in events if e["type"] == "token")
        done = next(e for e in events if e["type"] == "done")
        assert done["data"]["full_content"] == "echo: hi"
        assert stream_calls.call_count == 2

    @pytest.mark.asyncio
    async def test_closed_malformed_call_now_gets_a_corrective_retry_not_a_silent_finish(self):
        executor, llm_service = self._make_executor(EchoTool())
        malformed = '<tool_call>{"name": "echo", "arguments": {"message": "hi\nthere"}}</tool_call>'
        stream_calls = MagicMock(side_effect=make_multi_call_event_stream([
            [{"type": "token", "content": malformed}],
            [{"type": "token", "content": "echo: hi there"}],
        ]))
        llm_service.stream_with_tools = stream_calls

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "echo hi there"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            allowed_tools=["echo"],
        ))

        assert all("<tool_call>" not in e["data"]["content"] for e in events if e["type"] == "token")
        done = next(e for e in events if e["type"] == "done")
        assert done["data"]["full_content"] == "echo: hi there"
        assert stream_calls.call_count == 2

    @pytest.mark.asyncio
    async def test_tool_rejection_is_fed_back_to_the_model_same_turn(self):
        """A dispatched call the TOOL ITSELF rejects (not a parse failure)
        must reach the model as a same-turn correction automatically -- the
        existing tool-result-message mechanism, unaffected by inline
        dispatch happening mid-generation instead of after it."""
        executor, llm_service = self._make_executor(FailingTool())
        stream_calls = MagicMock(side_effect=make_multi_call_event_stream([
            [{"type": "token", "content": '<tool_call>{"name": "fail", "arguments": {}}</tool_call>'}],
            [{"type": "token", "content": "sorted it out"}],
        ]))
        llm_service.stream_with_tools = stream_calls

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "go"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            allowed_tools=["fail"],
        ))

        tool_end = next(e for e in events if e["type"] == "tool_end")
        assert tool_end["data"]["success"] is False
        done = next(e for e in events if e["type"] == "done")
        assert done["data"]["full_content"] == "sorted it out"
        assert stream_calls.call_count == 2
        second_call_messages = stream_calls.call_args_list[1].kwargs["messages"]
        assert any(
            m.get("role") == "tool" and "intentional failure" in m.get("content", "")
            for m in second_call_messages
        )

    @pytest.mark.asyncio
    async def test_plain_text_reply_streams_unaffected(self):
        executor, llm_service = self._make_executor(EchoTool())
        llm_service.stream_with_tools = make_multi_call_event_stream([
            [{"type": "token", "content": "Hel"}, {"type": "token", "content": "lo!"}],
        ])

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "hi"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        ))

        assert [e["data"]["content"] for e in events if e["type"] == "token"] == ["Hel", "lo!"]
        done = next(e for e in events if e["type"] == "done")
        assert done["data"]["full_content"] == "Hello!"


# ---------------------------------------------------------------------------
# Malformed tool-call rescue
# ---------------------------------------------------------------------------

class TestToolExecutorRescue:
    """Near-miss invocations are repaired, steered, or fall back honestly."""

    def _make_executor(self, tool: BaseTool = None):
        registry = ToolRegistry()
        if tool:
            registry.register(tool)
        llm_service = AsyncMock()
        return ToolExecutor(tool_registry=registry, llm_service=llm_service), llm_service

    @pytest.mark.asyncio
    async def test_repairs_tool_action_tag_into_dispatched_call(self):
        executor, llm_service = self._make_executor(EchoTool())
        llm_service.generate_with_tools.side_effect = [
            make_llm_response('<tool_action type="echo" message="hi">', tool_calls=[]),
            make_llm_response("done echoing"),
        ]

        response, executions = await executor.execute_with_tools(
            messages=[{"role": "user", "content": "echo hi"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            allowed_tools=["echo"],
        )

        assert response.content == "done echoing"
        assert [te.tool_name for te in executions] == ["echo"]
        assert executions[0].arguments == {"message": "hi"}
        assert response.rescues == [
            {"tool_name": "echo", "repaired": True, "original_format": "tool_action_tag"}
        ]

    @pytest.mark.asyncio
    async def test_ambiguous_near_miss_retries_with_nudge_then_succeeds(self):
        executor, llm_service = self._make_executor(EchoTool())
        # First reply names echo but omits the required 'message' → ambiguous.
        llm_service.generate_with_tools.side_effect = [
            make_llm_response('<tool_action type="echo">', tool_calls=[]),
            make_llm_response("ok, what should I echo?"),
        ]

        response, executions = await executor.execute_with_tools(
            messages=[{"role": "user", "content": "echo"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            allowed_tools=["echo"],
        )

        assert response.content == "ok, what should I echo?"
        assert executions == []
        assert response.rescues is None
        # A corrective system nudge was appended before the second call.
        second_messages = llm_service.generate_with_tools.call_args_list[1].kwargs["messages"]
        assert any(
            m["role"] == "system" and "<tool_call>" in m["content"] for m in second_messages
        )

    @pytest.mark.asyncio
    async def test_bounded_retries_then_honest_fallback(self):
        executor, llm_service = self._make_executor(EchoTool())
        # Always ambiguous: 1 original + 2 retries, then an honest fallback.
        llm_service.generate_with_tools.side_effect = [
            make_llm_response('<tool_action type="echo">', tool_calls=[]),
            make_llm_response('<tool_action type="echo">', tool_calls=[]),
            make_llm_response('<tool_action type="echo">', tool_calls=[]),
        ]

        response, executions = await executor.execute_with_tools(
            messages=[{"role": "user", "content": "echo"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            allowed_tools=["echo"],
        )

        assert llm_service.generate_with_tools.call_count == 3
        assert "couldn't format" in response.content
        assert "<tool_action" not in response.content
        assert executions == []

    @pytest.mark.asyncio
    async def test_prose_mentioning_a_tool_is_not_rescued(self):
        executor, llm_service = self._make_executor(EchoTool())
        llm_service.generate_with_tools.return_value = make_llm_response(
            "I already used echo to check that.", tool_calls=[]
        )

        response, executions = await executor.execute_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            allowed_tools=["echo"],
        )

        assert response.content == "I already used echo to check that."
        assert executions == []
        assert response.rescues is None
        assert llm_service.generate_with_tools.call_count == 1

    @pytest.mark.asyncio
    async def test_legacy_stream_suppresses_markup_and_dispatches_repair(self):
        """The native (force_prompt_tools) buffered path never streams the raw markup."""
        registry = ToolRegistry()
        registry.register(EchoTool())
        llm_service = AsyncMock()
        llm_service.repository.get_configuration = MagicMock(
            return_value=make_config({"force_prompt_tools": True})
        )
        executor = ToolExecutor(tool_registry=registry, llm_service=llm_service)
        llm_service.generate_with_tools.side_effect = [
            make_llm_response('<tool_action type="echo" message="hi">', tool_calls=[]),
            make_llm_response("done echoing", tool_calls=[]),
        ]

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "echo hi"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            allowed_tools=["echo"],
        ))

        token_events = [e for e in events if e["type"] == "token"]
        assert all("tool_action" not in e["data"]["content"] for e in token_events)
        assert [e["data"]["tool_name"] for e in events if e["type"] == "tool_start"] == ["echo"]
        done = next(e for e in events if e["type"] == "done")
        assert done["data"]["rescues"] == [
            {"tool_name": "echo", "repaired": True, "original_format": "tool_action_tag"}
        ]


# ---------------------------------------------------------------------------
# Truncated `<tool_call>` rescue (cut off mid-generation, not miswritten)
# ---------------------------------------------------------------------------

class TestToolExecutorTruncatedToolCall:
    def _make_executor(self, tool: BaseTool = None):
        registry = ToolRegistry()
        if tool:
            registry.register(tool)
        llm_service = AsyncMock()
        return ToolExecutor(tool_registry=registry, llm_service=llm_service), llm_service

    @pytest.mark.asyncio
    async def test_unclosed_tool_call_never_reaches_the_user_and_retries(self):
        executor, llm_service = self._make_executor(EchoTool())
        truncated = '<tool_call>{"name": "echo", "arguments": {"mess'
        llm_service.generate_with_tools.side_effect = [
            make_llm_response(truncated, tool_calls=[]),
            make_llm_response("echo: hi", tool_calls=[]),
        ]

        response, executions = await executor.execute_with_tools(
            messages=[{"role": "user", "content": "echo hi"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            allowed_tools=["echo"],
        )

        assert response.content == "echo: hi"
        assert "<tool_call>" not in response.content
        assert executions == []
        assert llm_service.generate_with_tools.call_count == 2
        second_messages = llm_service.generate_with_tools.call_args_list[1].kwargs["messages"]
        nudges = [
            m["content"] for m in second_messages
            if m["role"] == "system" and "cut off" in m["content"]
        ]
        assert len(nudges) == 1
        assert "echo" in nudges[0]
        assert '<tool_call>{"name": "get_form_state", "arguments": {}}</tool_call>' in nudges[0]
        # The truncated markup was stripped from the assistant message re-sent
        # to the model too, not just from the final response.
        assert all("<tool_call>" not in m["content"] for m in second_messages if m["role"] == "assistant")

    @pytest.mark.asyncio
    async def test_bounded_retries_then_honest_fallback_never_shows_partial_json(self):
        executor, llm_service = self._make_executor(EchoTool())
        truncated = '<tool_call>{"name": "echo", "arguments": {"operations": [{"op": "x'
        llm_service.generate_with_tools.side_effect = [
            make_llm_response(truncated, tool_calls=[]),
            make_llm_response(truncated, tool_calls=[]),
            make_llm_response(truncated, tool_calls=[]),
        ]

        response, executions = await executor.execute_with_tools(
            messages=[{"role": "user", "content": "echo"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            allowed_tools=["echo"],
        )

        assert llm_service.generate_with_tools.call_count == 3
        assert "cut off" in response.content
        assert "<tool_call>" not in response.content
        assert '"op": "x' not in response.content
        assert executions == []

    @pytest.mark.asyncio
    async def test_closed_tool_call_dispatches_normally_and_is_unaffected(self):
        executor, llm_service = self._make_executor(EchoTool())
        llm_service.generate_with_tools.side_effect = [
            make_llm_response(
                '<tool_call>{"name": "echo", "arguments": {"message": "hi"}}</tool_call>',
                tool_calls=[],
            ),
            make_llm_response("done echoing", tool_calls=[]),
        ]

        response, executions = await executor.execute_with_tools(
            messages=[{"role": "user", "content": "echo hi"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            allowed_tools=["echo"],
        )

        assert [te.tool_name for te in executions] == ["echo"]
        assert executions[0].arguments == {"message": "hi"}
        assert response.content == "done echoing"
        assert llm_service.generate_with_tools.call_count == 2

    @pytest.mark.asyncio
    async def test_legacy_stream_suppresses_truncated_markup_and_retries(self):
        """The native (force_prompt_tools) buffered path never streams a
        partial `<tool_call>` payload, and steers the model to resend it."""
        registry = ToolRegistry()
        registry.register(EchoTool())
        llm_service = AsyncMock()
        llm_service.repository.get_configuration = MagicMock(
            return_value=make_config({"force_prompt_tools": True})
        )
        executor = ToolExecutor(tool_registry=registry, llm_service=llm_service)
        truncated = '<tool_call>{"name": "echo", "arguments": {"mess'
        llm_service.generate_with_tools.side_effect = [
            make_llm_response(truncated, tool_calls=[]),
            make_llm_response("echo: hi", tool_calls=[]),
        ]

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "echo hi"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            allowed_tools=["echo"],
        ))

        token_events = [e for e in events if e["type"] == "token"]
        assert all("<tool_call>" not in e["data"]["content"] for e in token_events)
        done = next(e for e in events if e["type"] == "done")
        assert done["data"]["full_content"] == "echo: hi"
        assert llm_service.generate_with_tools.call_count == 2


# ---------------------------------------------------------------------------
# Closed `<tool_call>` with unparseable JSON — corrective retry, not a silent drop
# ---------------------------------------------------------------------------

class TestToolExecutorMalformedClosedToolCall:
    def _make_executor(self, tool: BaseTool = None):
        registry = ToolRegistry()
        if tool:
            registry.register(tool)
        llm_service = AsyncMock()
        return ToolExecutor(tool_registry=registry, llm_service=llm_service), llm_service

    @pytest.mark.asyncio
    async def test_unparseable_json_inside_a_closed_tag_gets_a_corrective_nudge(self):
        """A complete <tool_call>...</tool_call> whose JSON doesn't parse
        (e.g. a literal newline in a string value) must not be silently
        dropped nor shown raw — it gets the same corrective retry as any
        other near-miss."""
        executor, llm_service = self._make_executor(EchoTool())
        malformed = '<tool_call>{"name": "echo", "arguments": {"message": "hi\nthere"}}</tool_call>'
        llm_service.generate_with_tools.side_effect = [
            make_llm_response(malformed, tool_calls=[]),
            make_llm_response("echo: hi there", tool_calls=[]),
        ]

        response, executions = await executor.execute_with_tools(
            messages=[{"role": "user", "content": "echo hi there"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            allowed_tools=["echo"],
        )

        assert response.content == "echo: hi there"
        assert "<tool_call>" not in response.content
        assert executions == []
        second_messages = llm_service.generate_with_tools.call_args_list[1].kwargs["messages"]
        nudges = [
            m["content"] for m in second_messages
            if m["role"] == "system" and "did not parse" in m["content"]
        ]
        assert len(nudges) == 1
        assert "echo" in nudges[0]

    @pytest.mark.asyncio
    async def test_closed_tag_with_valid_json_dispatches_without_a_retry(self):
        """A complete, well-formed <tool_call> is the primary case — it must
        dispatch directly, never round-trip through a corrective retry."""
        executor, llm_service = self._make_executor(EchoTool())
        llm_service.generate_with_tools.side_effect = [
            make_llm_response(
                '<tool_call>{"name": "echo", "arguments": {"message": "hi"}}</tool_call>',
                tool_calls=[],
            ),
            make_llm_response("echo: hi", tool_calls=[]),
        ]

        response, executions = await executor.execute_with_tools(
            messages=[{"role": "user", "content": "echo hi"}],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
            allowed_tools=["echo"],
        )

        assert [te.tool_name for te in executions] == ["echo"]
        assert executions[0].arguments == {"message": "hi"}
        assert llm_service.generate_with_tools.call_count == 2


# ---------------------------------------------------------------------------
# Helpers for tool-result bounding / repeated-call guard / failure accounting
# ---------------------------------------------------------------------------

class BigResultTool(BaseTool):
    """Tool whose result exceeds the truncation bound."""

    @property
    def name(self) -> str:
        return "big_result"

    @property
    def description(self) -> str:
        return "Returns oversized data."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        return ToolResult(success=True, data="x" * 9000)


class CountingTool(BaseTool):
    """Tool that records every invocation it actually receives, and fails
    when called with mode='fail' — used to prove the repeated-call guard
    short-circuits without re-executing the tool."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "counting_tool"

    @property
    def description(self) -> str:
        return "Counts invocations."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "mode": {"type": "string"},
                "x": {"type": "integer"},
            },
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        self.calls.append(dict(kwargs))
        if kwargs.get("mode") == "fail":
            return ToolResult(success=False, data="", error="boom")
        return ToolResult(success=True, data="ok")


def find_tool_message(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    return next(m for m in messages if m.get("role") == "tool")


# ---------------------------------------------------------------------------
# Tool result content bounding (executor.py's four message-construction sites)
# ---------------------------------------------------------------------------

class TestToolResultTruncation:
    def _make_executor(self, tool: BaseTool):
        registry = ToolRegistry()
        registry.register(tool)
        llm_service = AsyncMock()
        return ToolExecutor(tool_registry=registry, llm_service=llm_service), llm_service

    @pytest.mark.asyncio
    async def test_oversized_result_truncated_with_marker_buffered(self):
        executor, llm_service = self._make_executor(BigResultTool())
        tool_call = {"id": "c1", "function": {"name": "big_result", "arguments": "{}"}}
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("done"),
        ]

        await executor.execute_with_tools(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        )

        second_messages = llm_service.generate_with_tools.call_args_list[1].kwargs["messages"]
        content = find_tool_message(second_messages)["content"]
        assert len(content) < 9000
        assert content.startswith("x" * 100)
        assert "[Result truncated at 8000 characters." in content
        assert "Refine the call" in content

    @pytest.mark.asyncio
    async def test_small_result_not_truncated_buffered(self):
        executor, llm_service = self._make_executor(EchoTool())
        tool_call = {"id": "c1", "function": {"name": "echo", "arguments": '{"message": "hi"}'}}
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("done"),
        ]

        await executor.execute_with_tools(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        )

        second_messages = llm_service.generate_with_tools.call_args_list[1].kwargs["messages"]
        content = find_tool_message(second_messages)["content"]
        assert content == "echo: hi"
        assert "truncated" not in content

    @pytest.mark.asyncio
    async def test_oversized_result_truncated_with_marker_legacy_stream(self):
        """Default AsyncMock forces the legacy stream path (see
        TestNativeConfigForcesLegacyPath) — proves the same bound applies on
        that message-construction site, not just the buffered one."""
        executor, llm_service = self._make_executor(BigResultTool())
        tool_call = {"id": "c1", "function": {"name": "big_result", "arguments": "{}"}}
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("done", tool_calls=[]),
        ]

        await collect_stream_events(executor.execute_with_tools_stream(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        ))

        second_messages = llm_service.generate_with_tools.call_args_list[1].kwargs["messages"]
        content = find_tool_message(second_messages)["content"]
        assert len(content) < 9000
        assert "[Result truncated at 8000 characters." in content


# ---------------------------------------------------------------------------
# Repeated-call guard: an identical call that already failed this turn is
# refused without re-executing the tool.
# ---------------------------------------------------------------------------

class TestToolExecutorRepeatedCallGuard:
    def _make_executor(self, tool: BaseTool):
        registry = ToolRegistry()
        registry.register(tool)
        llm_service = AsyncMock()
        return ToolExecutor(tool_registry=registry, llm_service=llm_service), llm_service

    @pytest.mark.asyncio
    async def test_identical_failing_call_short_circuits_buffered(self):
        tool = CountingTool()
        executor, llm_service = self._make_executor(tool)
        call = {"id": "c1", "function": {"name": "counting_tool", "arguments": '{"mode": "fail"}'}}
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[call]),
            make_llm_response("", tool_calls=[call]),
            make_llm_response("done"),
        ]

        response, executions = await executor.execute_with_tools(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        )

        assert len(tool.calls) == 1  # the tool itself only ran once
        assert len(executions) == 2
        assert executions[0].result.error == "boom"
        assert executions[1].result.success is False
        assert "You already called counting_tool" in executions[1].result.error
        assert "boom" in executions[1].result.error
        assert response.content == "done"

    @pytest.mark.asyncio
    async def test_different_arguments_are_not_blocked_buffered(self):
        tool = CountingTool()
        executor, llm_service = self._make_executor(tool)
        call1 = {"id": "c1", "function": {"name": "counting_tool", "arguments": '{"mode": "fail", "x": 1}'}}
        call2 = {"id": "c2", "function": {"name": "counting_tool", "arguments": '{"mode": "fail", "x": 2}'}}
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[call1]),
            make_llm_response("", tool_calls=[call2]),
            make_llm_response("done"),
        ]

        _, executions = await executor.execute_with_tools(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        )

        assert len(tool.calls) == 2  # both attempts really executed
        assert executions[1].result.error == "boom"  # not the teaching message

    @pytest.mark.asyncio
    async def test_identical_successful_call_is_not_blocked_buffered(self):
        tool = CountingTool()
        executor, llm_service = self._make_executor(tool)
        call = {"id": "c1", "function": {"name": "counting_tool", "arguments": '{"mode": "ok"}'}}
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[call]),
            make_llm_response("", tool_calls=[call]),
            make_llm_response("done"),
        ]

        _, executions = await executor.execute_with_tools(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        )

        assert len(tool.calls) == 2  # both really executed, neither blocked
        assert all(e.result.success for e in executions)

    @pytest.mark.asyncio
    async def test_identical_failing_call_short_circuits_legacy_stream(self):
        tool = CountingTool()
        executor, llm_service = self._make_executor(tool)
        call = {"id": "c1", "function": {"name": "counting_tool", "arguments": '{"mode": "fail"}'}}
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[call]),
            make_llm_response("", tool_calls=[call]),
            make_llm_response("done", tool_calls=[]),
        ]

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        ))

        done = next(e for e in events if e["type"] == "done")
        assert len(tool.calls) == 1
        assert len(done["data"]["tool_executions"]) == 2
        assert "You already called counting_tool" in done["data"]["tool_executions"][1].result.error

    @pytest.mark.asyncio
    async def test_identical_failing_call_short_circuits_native_stream(self):
        tool = CountingTool()
        registry = ToolRegistry()
        registry.register(tool)
        llm_service = AsyncMock()
        llm_service.repository.get_configuration = MagicMock(return_value=make_config())
        executor = ToolExecutor(tool_registry=registry, llm_service=llm_service)

        call = {"id": "c1", "function": {"name": "counting_tool", "arguments": '{"mode": "fail"}'}}
        llm_service.stream_with_tools = make_multi_call_event_stream([
            [
                {"type": "tool_calls", "tool_calls": [call]},
                {"type": "usage", "tokens_used": None, "prompt_tokens": None, "completion_tokens": None},
            ],
            [
                {"type": "tool_calls", "tool_calls": [call]},
                {"type": "usage", "tokens_used": None, "prompt_tokens": None, "completion_tokens": None},
            ],
            [
                {"type": "token", "content": "done"},
                {"type": "usage", "tokens_used": None, "prompt_tokens": None, "completion_tokens": None},
            ],
        ])

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        ))

        done = next(e for e in events if e["type"] == "done")
        assert len(tool.calls) == 1
        assert len(done["data"]["tool_executions"]) == 2
        assert "You already called counting_tool" in done["data"]["tool_executions"][1].result.error


# ---------------------------------------------------------------------------
# Per-tool failure accounting on the streaming `done` event (see also
# TestToolExecutorRepeatedCallGuard, which exercises the same `guard` object)
# ---------------------------------------------------------------------------

class TestToolExecutorFailureAccounting:
    def _make_executor(self, tool: BaseTool = None):
        registry = ToolRegistry()
        if tool:
            registry.register(tool)
        llm_service = AsyncMock()
        return ToolExecutor(tool_registry=registry, llm_service=llm_service), llm_service

    @pytest.mark.asyncio
    async def test_legacy_stream_done_event_counts_failures_per_tool(self):
        tool = CountingTool()
        executor, llm_service = self._make_executor(tool)
        call1 = {"id": "c1", "function": {"name": "counting_tool", "arguments": '{"mode": "fail", "x": 1}'}}
        call2 = {"id": "c2", "function": {"name": "counting_tool", "arguments": '{"mode": "fail", "x": 2}'}}
        llm_service.generate_with_tools.side_effect = [
            make_llm_response("", tool_calls=[call1]),
            make_llm_response("", tool_calls=[call2]),
            make_llm_response("done", tool_calls=[]),
        ]

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        ))

        done = next(e for e in events if e["type"] == "done")
        assert done["data"]["tool_failures"] == {"counting_tool": 2}

    @pytest.mark.asyncio
    async def test_legacy_stream_done_event_omits_tool_failures_when_none_failed(self):
        executor, llm_service = self._make_executor(EchoTool())
        llm_service.generate_with_tools.return_value = make_llm_response("ok", tool_calls=[])

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        ))

        done = next(e for e in events if e["type"] == "done")
        assert done["data"]["tool_failures"] is None

    @pytest.mark.asyncio
    async def test_native_stream_done_event_counts_failures_per_tool(self):
        tool = CountingTool()
        registry = ToolRegistry()
        registry.register(tool)
        llm_service = AsyncMock()
        llm_service.repository.get_configuration = MagicMock(return_value=make_config())
        executor = ToolExecutor(tool_registry=registry, llm_service=llm_service)

        call1 = {"id": "c1", "function": {"name": "counting_tool", "arguments": '{"mode": "fail", "x": 1}'}}
        call2 = {"id": "c2", "function": {"name": "counting_tool", "arguments": '{"mode": "fail", "x": 2}'}}
        llm_service.stream_with_tools = make_multi_call_event_stream([
            [
                {"type": "tool_calls", "tool_calls": [call1]},
                {"type": "usage", "tokens_used": None, "prompt_tokens": None, "completion_tokens": None},
            ],
            [
                {"type": "tool_calls", "tool_calls": [call2]},
                {"type": "usage", "tokens_used": None, "prompt_tokens": None, "completion_tokens": None},
            ],
            [
                {"type": "token", "content": "done"},
                {"type": "usage", "tokens_used": None, "prompt_tokens": None, "completion_tokens": None},
            ],
        ])

        events = await collect_stream_events(executor.execute_with_tools_stream(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        ))

        done = next(e for e in events if e["type"] == "done")
        assert done["data"]["tool_failures"] == {"counting_tool": 2}

    @pytest.mark.asyncio
    async def test_buffered_response_exposes_tool_failures_as_a_real_pydantic_field(self):
        """Uses a real LLMResponse (not a MagicMock) so a pydantic assignment
        error on an undeclared field would surface here, not just work by
        virtue of a test double accepting any attribute."""
        tool = CountingTool()
        executor, llm_service = self._make_executor(tool)
        call1 = {"id": "c1", "function": {"name": "counting_tool", "arguments": '{"mode": "fail", "x": 1}'}}
        call2 = {"id": "c2", "function": {"name": "counting_tool", "arguments": '{"mode": "fail", "x": 2}'}}
        llm_service.generate_with_tools.side_effect = [
            make_real_llm_response(tool_calls=[call1]),
            make_real_llm_response(tool_calls=[call2]),
            make_real_llm_response(content="done"),
        ]

        response, _ = await executor.execute_with_tools(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        )

        assert response.tool_failures == {"counting_tool": 2}

    @pytest.mark.asyncio
    async def test_buffered_response_tool_failures_is_none_when_nothing_failed(self):
        executor, llm_service = self._make_executor(EchoTool())
        llm_service.generate_with_tools.return_value = make_real_llm_response(content="ok")

        response, _ = await executor.execute_with_tools(
            messages=[],
            llm_id="model-1",
            system_message="sys",
            tool_context=make_context(),
        )

        assert response.tool_failures is None
