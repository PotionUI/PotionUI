"""Pins the request body OllamaClient puts on the wire, method by method.

Each expectation was produced by running the client against a mock transport
and freezing what came out. Ollama's request shape is unusually easy to break
silently: sampling knobs live inside a nested ``options`` object, ``think`` and
``keep_alive`` sit at the root, and the force_prompt_tools mode rewrites the
whole conversation into text — so all of it is frozen here.
"""

import pytest

from src.features.llm.clients.ollama import OllamaClient
from tests.features.llm.wire_capture import (
    IMAGE_B64,
    OLLAMA_GENERATE_REPLY,
    OLLAMA_REPLY,
    collect,
    install_wire_capture,
    json_response,
    make_config,
    ndjson_response,
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
    "num_ctx": 8192,
    "top_k": 20,
    "top_p": 0.8,
    "stop": ["</done>"],
    "keep_alive": "5m",
    "think": False,
    "not_an_option": "ignored",
}

OVERRIDE = {"temperature": 0.1, "max_tokens": 64, "top_p": 0.5, "top_k": 40, "think": False}

DEFAULT_OPTIONS = {"temperature": 0.7, "num_predict": 512}

PLAIN_MESSAGES = [
    {"role": "system", "content": "SYS"},
    {"role": "user", "content": "hello"},
    {"role": "assistant", "content": "hi there"},
    {"role": "user", "content": "how are you"},
]

NATIVE_TOOL_MESSAGES = [
    {"role": "system", "content": "SYS"},
    {"role": "user", "content": "use the tool"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_form_state", "arguments": {"scope": "all"}},
            }
        ],
    },
    {"role": "tool", "content": '{"ok": true}', "tool_call_id": "call_1"},
    {"role": "user", "content": "thanks"},
]

PROMPT_TOOLS_TEXT = (
    "\n\n## Available Tools\n\n"
    "You have the following tools available. To call a tool, output a "
    "`<tool_call>` XML block with a JSON body containing `name` and `arguments`.\n\n"
    "Example:\n"
    '<tool_call>{"name": "tool_name", "arguments": {"arg1": "value1"}}</tool_call>\n\n'
    "You may call multiple tools in a single response. "
    "After each tool call you will receive the result in the next message. "
    "When you have enough information, respond normally without any tool_call blocks.\n\n"
    "### Tools\n\n"
    "**get_form_state**: Read the current form\n"
    "  Parameters:\n"
    "  - `scope` (string (required)): what to read\n"
)

NDJSON_CHUNKS = [
    {"message": {"content": "he"}},
    {"message": {"content": "llo"}},
    {"message": {"content": ""}, "done": True, "prompt_eval_count": 20, "eval_count": 10},
]


@pytest.fixture
def client():
    return OllamaClient()


def _ndjson():
    return ndjson_response(NDJSON_CHUNKS)


async def test_generate_text_only_uses_the_generate_endpoint(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [json_response(OLLAMA_GENERATE_REPLY)])

    await client.generate("say hi", make_config("ollama"), "SYS")

    assert capture.url == "http://peer.invalid:1234/api/generate"
    assert capture.body == {
        "model": "golden-model",
        "system": "SYS",
        "prompt": "say hi",
        "stream": False,
        "keep_alive": 0,
        "think": True,
        "options": DEFAULT_OPTIONS,
    }


async def test_generate_with_an_image_switches_to_the_chat_endpoint(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [json_response(OLLAMA_REPLY)])

    await client.generate("say hi", make_config("ollama"), "SYS", image_data=IMAGE_B64)

    assert capture.url == "http://peer.invalid:1234/api/chat"
    assert capture.body == {
        "model": "golden-model",
        "messages": [{"role": "user", "content": "say hi", "images": [IMAGE_B64]}],
        "system": "SYS",
        "stream": False,
        "keep_alive": 0,
        "think": True,
        "options": DEFAULT_OPTIONS,
    }


async def test_provider_options_reach_the_options_object_and_the_root_keys(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [json_response(OLLAMA_GENERATE_REPLY)])

    await client.generate("say hi", make_config("ollama", provider_options=PROVIDER_OPTIONS), "SYS")

    assert capture.body["keep_alive"] == "5m"
    assert capture.body["think"] is False
    assert capture.body["options"] == {
        "temperature": 0.7,
        "num_predict": 512,
        "num_ctx": 8192,
        "top_k": 20,
        "top_p": 0.8,
        "stop": ["</done>"],
    }


async def test_generate_with_history_pins_payload_and_trace(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [json_response(OLLAMA_REPLY)])

    await client.generate_with_history(HISTORY, make_config("ollama"), "SYS")

    assert capture.body == {
        "model": "golden-model",
        "messages": PLAIN_MESSAGES,
        "stream": False,
        "keep_alive": 0,
        "think": True,
        "options": DEFAULT_OPTIONS,
    }
    assert strip(capture.trace, "duration_ms") == {
        "provider": "ollama",
        "model": "golden-model",
        "request_system": "SYS",
        "request_messages": PLAIN_MESSAGES,
        "request_params": DEFAULT_OPTIONS,
        "response_text": "canned reply",
        "prompt_tokens": 20,
        "completion_tokens": 10,
    }


async def test_override_wins_over_provider_options(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [json_response(OLLAMA_REPLY)])
    config = make_config("ollama", provider_options=PROVIDER_OPTIONS)

    await client.generate_with_history(HISTORY, config, "SYS", options_override=OVERRIDE)

    assert capture.body["options"] == {
        "temperature": 0.1,
        "num_predict": 64,
        "num_ctx": 8192,
        "top_k": 40,
        "top_p": 0.5,
        "stop": ["</done>"],
    }
    assert capture.body["think"] is False
    assert capture.body["keep_alive"] == "5m"


async def test_history_image_attaches_to_the_last_user_message(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [json_response(OLLAMA_REPLY)])

    await client.generate_with_history(HISTORY, make_config("ollama"), "SYS", image_data=IMAGE_B64)

    assert capture.body["messages"][3] == {
        "role": "user",
        "content": "how are you",
        "images": [IMAGE_B64],
    }


async def test_empty_system_message_sends_no_system_turn(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [json_response(OLLAMA_REPLY)])

    await client.generate_with_history(HISTORY, make_config("ollama"), "")

    assert capture.body["messages"] == PLAIN_MESSAGES[1:]


async def test_stream_with_history_pins_payload(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [_ndjson()])

    await collect(client.stream_with_history(HISTORY, make_config("ollama"), "SYS"))

    assert capture.body == {
        "model": "golden-model",
        "messages": PLAIN_MESSAGES,
        "stream": True,
        "keep_alive": 0,
        "think": True,
        "options": DEFAULT_OPTIONS,
    }


async def test_native_tools_payload_and_trace(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [json_response(OLLAMA_REPLY)])

    await client.generate_with_tools(
        tool_history(), make_config("ollama"), "SYS", tools=tool_schemas()
    )

    assert capture.body == {
        "model": "golden-model",
        "messages": NATIVE_TOOL_MESSAGES,
        "stream": False,
        "keep_alive": 0,
        # Thinking is forced off whenever native tools are on the wire.
        "think": False,
        "options": DEFAULT_OPTIONS,
        "tools": tool_schemas(),
    }
    assert capture.trace["request_tools"] == ["get_form_state"]


async def test_native_tool_call_arguments_go_out_as_an_object(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [json_response(OLLAMA_REPLY)])
    history = tool_history()
    history[1]["tool_calls"][0]["function"]["arguments"] = '{"scope": "all"}'

    await client.generate_with_tools(history, make_config("ollama"), "SYS", tools=tool_schemas())

    assert capture.body["messages"][2]["tool_calls"][0]["function"]["arguments"] == {"scope": "all"}
    assert history[1]["tool_calls"][0]["function"]["arguments"] == '{"scope": "all"}'


async def test_tool_result_drops_the_name_field(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [json_response(OLLAMA_REPLY)])

    await client.generate_with_tools(
        tool_history(), make_config("ollama"), "SYS", tools=tool_schemas()
    )

    assert capture.body["messages"][3] == {
        "role": "tool",
        "content": '{"ok": true}',
        "tool_call_id": "call_1",
    }


async def test_force_prompt_tools_rewrites_the_conversation_as_text(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [json_response(OLLAMA_REPLY)])
    config = make_config("ollama", provider_options={"force_prompt_tools": True})

    await client.generate_with_tools(tool_history(), config, "SYS", tools=tool_schemas())

    assert capture.body == {
        "model": "golden-model",
        "messages": [
            {"role": "system", "content": "SYS" + PROMPT_TOOLS_TEXT},
            {"role": "user", "content": "use the tool"},
            {
                "role": "assistant",
                "content": '<tool_call>{"name": "get_form_state", "arguments": {"scope": "all"}}</tool_call>',
            },
            {"role": "user", "content": '[Tool Result: get_form_state]\n{"ok": true}'},
            {"role": "user", "content": "thanks"},
        ],
        "stream": False,
        "keep_alive": 0,
        # No native tools on the wire, so thinking stays on.
        "think": True,
        "options": DEFAULT_OPTIONS,
    }
    assert "tools" not in capture.body
    assert capture.trace["request_system"] == "SYS" + PROMPT_TOOLS_TEXT
    assert capture.trace["request_tools"] is None


async def test_tools_omitted_keeps_thinking_on(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [json_response(OLLAMA_REPLY)])

    await client.generate_with_tools(tool_history(), make_config("ollama"), "SYS")

    assert "tools" not in capture.body
    assert capture.body["think"] is True
    assert capture.trace["request_tools"] is None


async def test_stream_with_tools_pins_payload(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [_ndjson()])

    await collect(
        client.stream_with_tools(tool_history(), make_config("ollama"), "SYS", tools=tool_schemas())
    )

    assert capture.body == {
        "model": "golden-model",
        "messages": NATIVE_TOOL_MESSAGES,
        "stream": True,
        "keep_alive": 0,
        "think": False,
        "options": DEFAULT_OPTIONS,
        "tools": tool_schemas(),
    }


async def test_stream_and_buffered_history_send_the_same_request(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [json_response(OLLAMA_REPLY), _ndjson()])
    config = make_config("ollama", provider_options=PROVIDER_OPTIONS)

    await client.generate_with_history(HISTORY, config, "SYS", image_data=IMAGE_B64, options_override=OVERRIDE)
    buffered = capture.body
    await collect(
        client.stream_with_history(HISTORY, config, "SYS", image_data=IMAGE_B64, options_override=OVERRIDE)
    )
    streamed = capture.body

    assert strip(streamed, "stream") == strip(buffered, "stream")


async def test_stream_and_buffered_tools_send_the_same_request(monkeypatch, client):
    capture = install_wire_capture(monkeypatch, [json_response(OLLAMA_REPLY), _ndjson()])
    config = make_config("ollama", provider_options=PROVIDER_OPTIONS)

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

    assert strip(streamed, "stream") == strip(buffered, "stream")
