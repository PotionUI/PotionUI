"""The native client's own generated `<tool_call>` example must never name
a tool that wasn't actually offered for THIS request, and must never claim
empty arguments for a tool that requires some.

`_inject_tools_into_system_message`/`_example_tool_call` (unit-level,
independent of any real send) are covered in `test_native_client.py`. This
file exercises the REAL entry points instead — `generate_with_tools`,
`stream_with_tools`, and `messages_token_counter` (the prepared-request
accounting seam `LLMGateway` uses for budgeting) — with a tokenizer double
that records the actual `chat` list handed to `apply_chat_template`, so
assertions inspect the real prepared system message, not a re-derived
string. All three call sites go through the SAME
`_inject_tools_into_system_message`, so this also proves the accounting
count and the real send can never disagree about what was offered.
"""

from __future__ import annotations

import json
import re
import weakref

import jsonschema
import pytest
import torch

from src.features.llm.clients.native import NativeLLMClient, _LoadedCheckpoint
from src.features.llm.tools.builtin.manage_prompts_tool import AddPromptTool
from tests.features.llm.test_native_client import _config, client, fake_native_model, models_manager


@pytest.fixture(autouse=True)
def _no_real_cuda(monkeypatch):
    # This container reports a real GPU; the prompt-coherence fixtures use
    # a bare recording model with no `.to()` — placement is irrelevant to
    # what's under test here (the SYSTEM MESSAGE text), so keep it off.
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)


class _ChatRecordingTokenizer:
    """Records every `apply_chat_template` call's full `chat` list (not
    just its kwargs, unlike `test_native_client.py`'s own
    `_RecordingTokenizer`) — the seam this card's fix must be visible at:
    the actual system-message text a real send or accounting call
    prepares."""

    chat_template = None

    def __init__(self) -> None:
        self.calls: list = []

    def apply_chat_template(self, chat, add_generation_prompt=True, tokenize=False, **kwargs):
        self.calls.append(list(chat))
        return "PROMPT_TEXT"

    def __call__(self, text, return_tensors=None):
        if return_tensors == "pt":
            return {"input_ids": torch.tensor([[1, 2, 3]])}
        return {"input_ids": [1, 2, 3]}

    def decode(self, ids, skip_special_tokens=True):
        return "the answer"


class _RecordingGenModel:
    def generate(self, input_ids, **gen_kwargs):
        return torch.cat([input_ids, torch.tensor([[9, 9]])], dim=-1)


def _recording_checkpoint() -> _LoadedCheckpoint:
    return _LoadedCheckpoint(
        model=_RecordingGenModel(), tokenizer=_ChatRecordingTokenizer(),
        vision=False, model_type="qwen3", quantized=False,
    )


def _system_text(chat: list) -> str:
    for message in chat:
        if message.get("role") == "system":
            return message.get("content", "")
    return ""


DO_THING = {"function": {"name": "do_thing", "description": "does a thing", "parameters": {"type": "object"}}}
GET_FORM_STATE = {
    "function": {"name": "get_form_state", "description": "reads the form", "parameters": {"type": "object"}},
}
SET_VALUE_REQUIRED = {
    "function": {
        "name": "set_value",
        "description": "sets a value",
        "parameters": {
            "type": "object",
            "properties": {"key": {"type": "string"}, "amount": {"type": "integer"}},
            "required": ["key", "amount"],
        },
    },
}

MANAGE_SCOPED_REQUIRED = {
    "function": {
        "name": "manage_scoped",
        "description": "manages scoped entries",
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["list", "add", "remove"]},
                "scope": {"type": "string", "enum": ["session", "global"]},
                "limit": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                "target": {
                    "type": "object",
                    "properties": {"kind": {"type": "string", "enum": ["prompt", "image"]}, "id": {"type": "string"}},
                    "required": ["kind", "id"],
                },
            },
            "required": ["operation", "scope", "limit", "target"],
        },
    },
}


def _named_tools(tools: list) -> set:
    return {t["function"]["name"] for t in tools}


_NAME_RE = re.compile(r'"name":\s*"([^"]+)"')


def _example_block(text: str) -> str:
    start = text.index("for example ")
    end = text.index("</tool_call>", start) + len("</tool_call>")
    return text[start:end]


def _assert_example_names_are_offered_or_generic(text: str, tools: list) -> None:
    """The concrete example's own `"name"` field(s) — not the per-tool
    schema listing lines below it, which legitimately name every offered
    tool — must each be either an actually-offered tool or the generic,
    non-callable `<tool name>` placeholder. Never an invented callable."""
    offered = _named_tools(tools)
    example = _example_block(text)
    names = _NAME_RE.findall(example)
    assert names, f"no <tool_call> example found in: {example!r}"
    for name in names:
        assert name in offered or name == "<tool name>", (
            f"example named {name!r}, which was not among the offered tools {offered}"
        )


@pytest.fixture
def wired_checkpoint(client, fake_native_model, monkeypatch):
    """Warms the SAME recording checkpoint for both `messages_token_counter`
    (which only ever reads a WARM checkpoint via a weak reference) and the
    real generate/stream entry points (via `_acquire`) — the same dual-wiring
    `test_native_client.py`'s own `TestPreflightAndGenerateAgreeOnThinkingKwargs`
    uses to prove the two paths agree."""
    path, is_te = client._resolve_model(fake_native_model)
    checkpoint = _recording_checkpoint()
    client._checkpoint_refs[client._cache_key(path, is_te)] = weakref.ref(checkpoint)
    monkeypatch.setattr(NativeLLMClient, "_acquire", lambda self, p, cfg, is_te=False: checkpoint)
    return checkpoint


async def _drain_stream_with_tools(client, messages, config, tools):
    async for _event in client.stream_with_tools(messages, config, config.system_message, tools=tools):
        pass


class TestPromptCoherenceExcludingGetFormState:
    """A toolset that does not include `get_form_state` at all — the
    concrete example must never mention it."""

    TOOLS = [DO_THING]

    @pytest.mark.asyncio
    async def test_generate_with_tools(self, client, fake_native_model, wired_checkpoint):
        config = _config(fake_native_model)
        await client.generate_with_tools(
            [{"role": "user", "content": "hi"}], config, config.system_message, tools=self.TOOLS,
        )
        text = _system_text(wired_checkpoint.tokenizer.calls[-1])
        assert "get_form_state" not in text
        _assert_example_names_are_offered_or_generic(text, self.TOOLS)

    @pytest.mark.asyncio
    async def test_stream_with_tools(self, client, fake_native_model, wired_checkpoint):
        config = _config(fake_native_model)
        await _drain_stream_with_tools(client, [{"role": "user", "content": "hi"}], config, self.TOOLS)
        text = _system_text(wired_checkpoint.tokenizer.calls[-1])
        assert "get_form_state" not in text
        _assert_example_names_are_offered_or_generic(text, self.TOOLS)

    def test_prepared_request_accounting(self, client, fake_native_model, wired_checkpoint):
        config = _config(fake_native_model)
        counter = client.messages_token_counter(config)
        assert counter is not None
        counter(config.system_message, [{"role": "user", "content": "hi"}], self.TOOLS)
        text = _system_text(wired_checkpoint.tokenizer.calls[-1])
        assert "get_form_state" not in text
        _assert_example_names_are_offered_or_generic(text, self.TOOLS)


class TestPromptCoherenceIncludingGetFormState:
    """A toolset where `get_form_state` IS actually offered — naming it in
    the example is fine now, since it's genuinely among the offered tools."""

    TOOLS = [DO_THING, GET_FORM_STATE]

    @pytest.mark.asyncio
    async def test_generate_with_tools(self, client, fake_native_model, wired_checkpoint):
        config = _config(fake_native_model)
        await client.generate_with_tools(
            [{"role": "user", "content": "hi"}], config, config.system_message, tools=self.TOOLS,
        )
        text = _system_text(wired_checkpoint.tokenizer.calls[-1])
        _assert_example_names_are_offered_or_generic(text, self.TOOLS)

    @pytest.mark.asyncio
    async def test_stream_with_tools(self, client, fake_native_model, wired_checkpoint):
        config = _config(fake_native_model)
        await _drain_stream_with_tools(client, [{"role": "user", "content": "hi"}], config, self.TOOLS)
        text = _system_text(wired_checkpoint.tokenizer.calls[-1])
        _assert_example_names_are_offered_or_generic(text, self.TOOLS)

    def test_prepared_request_accounting(self, client, fake_native_model, wired_checkpoint):
        config = _config(fake_native_model)
        counter = client.messages_token_counter(config)
        counter(config.system_message, [{"role": "user", "content": "hi"}], self.TOOLS)
        text = _system_text(wired_checkpoint.tokenizer.calls[-1])
        _assert_example_names_are_offered_or_generic(text, self.TOOLS)


class TestPromptCoherenceWithRequiredArguments:
    """A tool with required parameters must still get a concrete example
    (never falling back to generic guidance just because arguments are
    needed) — but never an empty argument object misrepresenting it as
    parameter-free."""

    TOOLS = [SET_VALUE_REQUIRED]

    @pytest.mark.asyncio
    async def test_generate_with_tools(self, client, fake_native_model, wired_checkpoint):
        config = _config(fake_native_model)
        await client.generate_with_tools(
            [{"role": "user", "content": "hi"}], config, config.system_message, tools=self.TOOLS,
        )
        text = _system_text(wired_checkpoint.tokenizer.calls[-1])
        _assert_example_names_are_offered_or_generic(text, self.TOOLS)
        example = _example_block(text)
        assert '"name": "set_value"' in example
        assert '"arguments": {}' not in example

    @pytest.mark.asyncio
    async def test_stream_with_tools(self, client, fake_native_model, wired_checkpoint):
        config = _config(fake_native_model)
        await _drain_stream_with_tools(client, [{"role": "user", "content": "hi"}], config, self.TOOLS)
        text = _system_text(wired_checkpoint.tokenizer.calls[-1])
        example = _example_block(text)
        assert '"name": "set_value"' in example
        assert '"arguments": {}' not in example

    def test_prepared_request_accounting(self, client, fake_native_model, wired_checkpoint):
        config = _config(fake_native_model)
        counter = client.messages_token_counter(config)
        counter(config.system_message, [{"role": "user", "content": "hi"}], self.TOOLS)
        text = _system_text(wired_checkpoint.tokenizer.calls[-1])
        example = _example_block(text)
        assert '"name": "set_value"' in example
        assert '"arguments": {}' not in example


def _example_arguments(text: str) -> dict:
    example = _example_block(text)
    call = example[example.index("<tool_call>") + len("<tool_call>"):example.index("</tool_call>")]
    return json.loads(call)["arguments"]


def _assert_scoped_arguments_satisfy_their_schema(arguments: dict) -> None:
    schema = MANAGE_SCOPED_REQUIRED["function"]["parameters"]["properties"]
    assert arguments["operation"] in schema["operation"]["enum"]
    assert arguments["scope"] in schema["scope"]["enum"]
    assert arguments["limit"] is None or isinstance(arguments["limit"], int)
    assert arguments["target"]["kind"] in schema["target"]["properties"]["kind"]["enum"]
    assert isinstance(arguments["target"]["id"], str)


class TestPromptCoherenceWithEnumAndUnionArguments:
    """Required arguments whose schema names the allowed values (enum), a
    union of types, or a nested object with its own required keys get an
    example that satisfies that schema — a model shown `"operation": "..."`
    for an enum learns an argument the tool will reject."""

    TOOLS = [MANAGE_SCOPED_REQUIRED]

    @pytest.mark.asyncio
    async def test_generate_with_tools(self, client, fake_native_model, wired_checkpoint):
        config = _config(fake_native_model)
        await client.generate_with_tools(
            [{"role": "user", "content": "hi"}], config, config.system_message, tools=self.TOOLS,
        )
        text = _system_text(wired_checkpoint.tokenizer.calls[-1])
        _assert_example_names_are_offered_or_generic(text, self.TOOLS)
        _assert_scoped_arguments_satisfy_their_schema(_example_arguments(text))

    @pytest.mark.asyncio
    async def test_stream_with_tools(self, client, fake_native_model, wired_checkpoint):
        config = _config(fake_native_model)
        await _drain_stream_with_tools(client, [{"role": "user", "content": "hi"}], config, self.TOOLS)
        text = _system_text(wired_checkpoint.tokenizer.calls[-1])
        _assert_scoped_arguments_satisfy_their_schema(_example_arguments(text))

    def test_prepared_request_accounting(self, client, fake_native_model, wired_checkpoint):
        config = _config(fake_native_model)
        counter = client.messages_token_counter(config)
        counter(config.system_message, [{"role": "user", "content": "hi"}], self.TOOLS)
        text = _system_text(wired_checkpoint.tokenizer.calls[-1])
        _assert_scoped_arguments_satisfy_their_schema(_example_arguments(text))


class TestPromptCoherenceWithRealAddPromptTool:
    """The real, shipping `AddPromptTool` requires `segments` with
    `minItems: 1` — a schema feature the hand-rolled tools above don't
    exercise. The example's arguments are validated against the tool's
    actual JSON schema with `jsonschema` rather than hand-checked, so this
    catches any schema keyword the generator doesn't yet honor."""

    TOOLS = [AddPromptTool().to_schema()]

    @pytest.mark.asyncio
    async def test_generate_with_tools(self, client, fake_native_model, wired_checkpoint):
        config = _config(fake_native_model)
        await client.generate_with_tools(
            [{"role": "user", "content": "hi"}], config, config.system_message, tools=self.TOOLS,
        )
        text = _system_text(wired_checkpoint.tokenizer.calls[-1])
        _assert_example_names_are_offered_or_generic(text, self.TOOLS)
        jsonschema.validate(instance=_example_arguments(text), schema=self.TOOLS[0]["function"]["parameters"])

    @pytest.mark.asyncio
    async def test_stream_with_tools(self, client, fake_native_model, wired_checkpoint):
        config = _config(fake_native_model)
        await _drain_stream_with_tools(client, [{"role": "user", "content": "hi"}], config, self.TOOLS)
        text = _system_text(wired_checkpoint.tokenizer.calls[-1])
        jsonschema.validate(instance=_example_arguments(text), schema=self.TOOLS[0]["function"]["parameters"])

    def test_prepared_request_accounting(self, client, fake_native_model, wired_checkpoint):
        config = _config(fake_native_model)
        counter = client.messages_token_counter(config)
        counter(config.system_message, [{"role": "user", "content": "hi"}], self.TOOLS)
        text = _system_text(wired_checkpoint.tokenizer.calls[-1])
        jsonschema.validate(instance=_example_arguments(text), schema=self.TOOLS[0]["function"]["parameters"])


class TestPromptCoherenceWithNoTools:
    """No tools offered at all — the system message must carry no tool
    guidance whatsoever, on every entry point."""

    @pytest.mark.asyncio
    async def test_generate_with_tools(self, client, fake_native_model, wired_checkpoint):
        config = _config(fake_native_model)
        await client.generate_with_tools(
            [{"role": "user", "content": "hi"}], config, config.system_message, tools=None,
        )
        text = _system_text(wired_checkpoint.tokenizer.calls[-1])
        assert "<tool_call>" not in text
        assert text == config.system_message

    @pytest.mark.asyncio
    async def test_stream_with_tools(self, client, fake_native_model, wired_checkpoint):
        config = _config(fake_native_model)
        await _drain_stream_with_tools(client, [{"role": "user", "content": "hi"}], config, None)
        text = _system_text(wired_checkpoint.tokenizer.calls[-1])
        assert "<tool_call>" not in text
        assert text == config.system_message

    def test_prepared_request_accounting(self, client, fake_native_model, wired_checkpoint):
        config = _config(fake_native_model)
        counter = client.messages_token_counter(config)
        counter(config.system_message, [{"role": "user", "content": "hi"}], None)
        text = _system_text(wired_checkpoint.tokenizer.calls[-1])
        assert "<tool_call>" not in text
        assert text == config.system_message
