"""Pins the request body OpenAIClient puts on the wire, method by method.

Each expectation was produced by running the client against a mock transport
and freezing what came out, so a refactor that moves payload building around
has to reproduce it byte-for-byte. The trace call is pinned alongside the body
because the admin session-debug viewer reads exactly those fields.
"""

import json

import pytest

from src.features.llm.clients.openai import OpenAIClient
from tests.features.llm.wire_capture import (
    IMAGE_B64,
    OPENAI_REPLY,
    collect,
    install_wire_capture,
    json_response,
    make_config,
    sse_response,
    strip,
    tool_history,
    tool_schemas,
)

HISTORY = [
    {"role": "user", "content": "hello"},
    {"role": "assistant", "content": "hi there"},
    {"role": "user", "content": "how are you"},
]

PROVIDER_OPTIONS = {
    "top_p": 0.9,
    "presence_penalty": 0.25,
    "frequency_penalty": 0.5,
    "unsupported_key": "ignored",
}

# top_k is not an OpenAI parameter; think belongs to Ollama. Both must be dropped.
OVERRIDE = {"temperature": 0.1, "max_tokens": 64, "top_p": 0.5, "top_k": 40, "think": False}

IMAGE_PART = {
    "type": "image_url",
    "image_url": {"url": f"data:image/jpeg;base64,{IMAGE_B64}"},
}

PLAIN_MESSAGES = [
    {"role": "system", "content": "SYS"},
    {"role": "user", "content": "hello"},
    {"role": "assistant", "content": "hi there"},
    {"role": "user", "content": "how are you"},
]

TOOL_MESSAGES = [
    {"role": "system", "content": "SYS"},
    {"role": "user", "content": "use the tool"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_form_state", "arguments": '{"scope": "all"}'},
            }
        ],
    },
    {
        "role": "tool",
        "content": '{"ok": true}',
        "tool_call_id": "call_1",
        "name": "get_form_state",
    },
    {"role": "user", "content": "thanks"},
]

SSE_EVENTS = [
    json.dumps({"choices": [{"delta": {"content": "he"}}]}),
    json.dumps({"choices": [{"delta": {"content": "llo"}}]}),
    json.dumps({"choices": [], "usage": {"total_tokens": 30, "prompt_tokens": 20, "completion_tokens": 10}}),
    "[DONE]",
]


@pytest.fixture
def client():
    return OpenAIClient()


def _sse():
    return sse_response(SSE_EVENTS)


async def test_generate_pins_prompt_payload(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [json_response(OPENAI_REPLY)])

    await client.generate("say hi", make_config("openai"), "SYS")

    assert capture.url == "http://peer.invalid:1234/chat/completions"
    assert capture.body == {
        "model": "golden-model",
        "messages": [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "say hi"},
        ],
        "temperature": 0.7,
        "max_tokens": 512,
    }


async def test_generate_puts_the_image_part_before_the_text(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [json_response(OPENAI_REPLY)])

    await client.generate("say hi", make_config("openai"), "SYS", image_data=IMAGE_B64)

    assert capture.body["messages"][1] == {
        "role": "user",
        "content": [IMAGE_PART, {"type": "text", "text": "say hi"}],
    }


async def test_api_key_becomes_a_bearer_header_and_absence_sends_none(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [json_response(OPENAI_REPLY)] * 2)

    await client.generate("say hi", make_config("openai", api_key="sk-golden"), "SYS")
    assert capture.headers["authorization"] == "Bearer sk-golden"
    assert capture.headers["content-type"] == "application/json"

    await client.generate("say hi", make_config("openai"), "SYS")
    assert "authorization" not in capture.headers


async def test_generate_with_history_pins_payload_and_trace(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [json_response(OPENAI_REPLY)])

    await client.generate_with_history(HISTORY, make_config("openai"), "SYS")

    assert capture.body == {
        "model": "golden-model",
        "messages": PLAIN_MESSAGES,
        "temperature": 0.7,
        "max_tokens": 512,
    }
    assert strip(capture.trace, "duration_ms") == {
        "provider": "openai",
        "model": "golden-model",
        "request_system": "SYS",
        "request_messages": PLAIN_MESSAGES,
        "request_params": {"temperature": 0.7, "max_tokens": 512},
        "response_text": "canned reply",
        "prompt_tokens": 20,
        "completion_tokens": 10,
    }


async def test_provider_options_and_override_merge_into_sampling_params(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [json_response(OPENAI_REPLY)])
    config = make_config("openai", provider_options=PROVIDER_OPTIONS)

    await client.generate_with_history(HISTORY, config, "SYS", options_override=OVERRIDE)

    assert strip(capture.body, "model", "messages") == {
        "temperature": 0.1,
        "max_tokens": 64,
        "top_p": 0.5,
        "presence_penalty": 0.25,
        "frequency_penalty": 0.5,
    }


async def test_history_image_lands_on_the_last_user_message(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [json_response(OPENAI_REPLY)])

    await client.generate_with_history(HISTORY, make_config("openai"), "SYS", image_data=IMAGE_B64)

    messages = capture.body["messages"]
    assert messages[:3] == PLAIN_MESSAGES[:3]
    assert messages[3] == {
        "role": "user",
        "content": [IMAGE_PART, {"type": "text", "text": "how are you"}],
    }


async def test_stream_with_history_pins_payload_and_trace(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [_sse()])

    await collect(client.stream_with_history(HISTORY, make_config("openai"), "SYS"))

    assert capture.body == {
        "model": "golden-model",
        "messages": PLAIN_MESSAGES,
        "temperature": 0.7,
        "max_tokens": 512,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    assert strip(capture.trace, "duration_ms") == {
        "provider": "openai",
        "model": "golden-model",
        "request_system": "SYS",
        "request_messages": PLAIN_MESSAGES,
        "request_params": {"temperature": 0.7, "max_tokens": 512},
        "response_text": "hello",
        "prompt_tokens": 20,
        "completion_tokens": 10,
    }


async def test_generate_with_tools_pins_payload_and_trace(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [json_response(OPENAI_REPLY)])

    await client.generate_with_tools(
        tool_history(), make_config("openai"), "SYS", tools=tool_schemas()
    )

    assert capture.body == {
        "model": "golden-model",
        "messages": TOOL_MESSAGES,
        "temperature": 0.7,
        "max_tokens": 512,
        "tools": tool_schemas(),
    }
    assert strip(capture.trace, "duration_ms") == {
        "provider": "openai",
        "model": "golden-model",
        "request_system": "SYS",
        "request_messages": TOOL_MESSAGES,
        "request_params": {"temperature": 0.7, "max_tokens": 512},
        "request_tools": ["get_form_state"],
        "response_text": "canned reply",
        "response_tool_calls": None,
        "prompt_tokens": 20,
        "completion_tokens": 10,
    }


async def test_tool_call_arguments_go_out_as_a_json_string(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [json_response(OPENAI_REPLY)])
    history = tool_history()

    await client.generate_with_tools(history, make_config("openai"), "SYS", tools=tool_schemas())

    sent = capture.body["messages"][2]["tool_calls"][0]["function"]["arguments"]
    assert sent == '{"scope": "all"}'
    # The caller's stored history keeps the canonical object shape.
    assert history[1]["tool_calls"][0]["function"]["arguments"] == {"scope": "all"}


async def test_generate_with_tools_omits_tools_key_when_none_given(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [json_response(OPENAI_REPLY)])

    await client.generate_with_tools(tool_history(), make_config("openai"), "SYS")

    assert "tools" not in capture.body
    # The buffered path traces an empty list here where the streaming path
    # traces None; both are load-bearing for the viewer's "native tools" flag.
    assert capture.trace["request_tools"] == []


async def test_stream_with_tools_omits_tools_key_and_traces_none(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [_sse()])

    await collect(client.stream_with_tools(tool_history(), make_config("openai"), "SYS"))

    assert "tools" not in capture.body
    assert capture.trace["request_tools"] is None


async def test_tools_image_lands_on_the_last_user_message(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [json_response(OPENAI_REPLY)])

    await client.generate_with_tools(
        tool_history(), make_config("openai"), "SYS", tools=tool_schemas(), image_data=IMAGE_B64
    )

    assert capture.body["messages"][4] == {
        "role": "user",
        "content": [IMAGE_PART, {"type": "text", "text": "thanks"}],
    }


async def test_stream_with_tools_pins_payload(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [_sse()])

    await collect(
        client.stream_with_tools(tool_history(), make_config("openai"), "SYS", tools=tool_schemas())
    )

    assert capture.body == {
        "model": "golden-model",
        "messages": TOOL_MESSAGES,
        "temperature": 0.7,
        "max_tokens": 512,
        "stream": True,
        "stream_options": {"include_usage": True},
        "tools": tool_schemas(),
    }


async def test_stream_and_buffered_history_send_the_same_request(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [json_response(OPENAI_REPLY), _sse()])
    config = make_config("openai", api_key="sk-golden", provider_options=PROVIDER_OPTIONS)

    await client.generate_with_history(HISTORY, config, "SYS", image_data=IMAGE_B64, options_override=OVERRIDE)
    buffered = capture.body
    await collect(
        client.stream_with_history(HISTORY, config, "SYS", image_data=IMAGE_B64, options_override=OVERRIDE)
    )
    streamed = capture.body

    assert strip(streamed, "stream", "stream_options") == buffered


async def test_stream_and_buffered_tools_send_the_same_request(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [json_response(OPENAI_REPLY), _sse()])
    config = make_config("openai", provider_options=PROVIDER_OPTIONS)

    await client.generate_with_tools(
        tool_history(), config, "SYS", tools=tool_schemas(), image_data=IMAGE_B64, options_override=OVERRIDE
    )
    buffered = capture.body
    await collect(
        client.stream_with_tools(
            tool_history(), config, "SYS", tools=tool_schemas(), image_data=IMAGE_B64, options_override=OVERRIDE
        )
    )
    streamed = capture.body

    assert strip(streamed, "stream", "stream_options") == buffered
