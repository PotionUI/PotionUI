"""Pins how OllamaClient decodes an NDJSON body into stream events.

Ollama's stream differs from the OpenAI one in three ways this file freezes:
the terminator is a `done: true` object rather than a sentinel line, the token
counts ride on that same object, and `message.tool_calls` arrives complete in
one chunk instead of as indexed fragments.
"""

import json

import pytest

from src.features.llm.clients.ollama import OllamaClient
from tests.features.llm.wire_capture import (
    chunked_response,
    closable_response,
    collect,
    install_wire_capture,
    make_config,
    tool_schemas,
)

HISTORY = [{"role": "user", "content": "hello"}]

TOOL_CALLS = [{"function": {"name": "get_form_state", "arguments": {"scope": "all"}}}]


@pytest.fixture
def client():
    return OllamaClient()


def _line(obj) -> bytes:
    return (json.dumps(obj) + "\n").encode()


def _token(content: str) -> bytes:
    return _line({"message": {"content": content}})


def _done(**extra) -> bytes:
    return _line({"message": {"content": ""}, "done": True, **extra})


def _split(raw: bytes, *cuts: int) -> list[bytes]:
    pieces = []
    previous = 0
    for cut in cuts:
        pieces.append(raw[previous:cut])
        previous = cut
    pieces.append(raw[previous:])
    return pieces


def _tokens(events) -> list[str]:
    return [e["content"] for e in events if e["type"] == "token"]


async def _stream(monkeypatch, client, chunks, *, tools=None, status=200):
    install_wire_capture(monkeypatch, [chunked_response(chunks, status)])
    if tools is None:
        return await collect(client.stream_with_history(HISTORY, make_config("ollama"), "SYS"))
    return await collect(
        client.stream_with_tools(HISTORY, make_config("ollama"), "SYS", tools=tools)
    )


async def test_a_line_split_across_chunk_boundaries_is_reassembled(monkeypatch, client):
    raw = _token("hel") + _token("lo") + _done()

    events = await _stream(monkeypatch, client, _split(raw, 5, 20, 40))

    assert _tokens(events) == ["hel", "lo"]


async def test_several_lines_in_one_chunk_all_decode(monkeypatch, client):
    raw = _token("a") + _token("b") + _token("c") + _done()

    events = await _stream(monkeypatch, client, [raw])

    assert _tokens(events) == ["a", "b", "c"]


async def test_the_done_object_carries_the_token_counts(monkeypatch, client):
    raw = _token("hi") + _done(prompt_eval_count=20, eval_count=10)

    events = await _stream(monkeypatch, client, [raw])

    assert events == [
        {"type": "token", "content": "hi"},
        {
            "type": "usage",
            "tokens_used": 30,
            "prompt_tokens": 20,
            "completion_tokens": 10,
            "completion": {"reason": "unknown", "raw": None},
        },
    ]


async def test_a_done_object_without_counts_reports_no_total(monkeypatch, client):
    events = await _stream(monkeypatch, client, [_token("hi") + _done()])

    assert events[-1] == {
        "type": "usage",
        "tokens_used": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "completion": {"reason": "unknown", "raw": None},
    }


async def test_one_missing_count_still_produces_a_total(monkeypatch, client):
    events = await _stream(monkeypatch, client, [_done(eval_count=10)])

    assert events[-1] == {
        "type": "usage",
        "tokens_used": 10,
        "prompt_tokens": None,
        "completion_tokens": 10,
        "completion": {"reason": "unknown", "raw": None},
    }


async def test_done_reason_length_normalizes_to_length(monkeypatch, client):
    events = await _stream(monkeypatch, client, [_token("hi") + _done(done_reason="length")])

    assert events[-1]["completion"] == {"reason": "length", "raw": "length"}


async def test_done_reason_stop_normalizes_to_stop(monkeypatch, client):
    events = await _stream(monkeypatch, client, [_token("hi") + _done(done_reason="stop")])

    assert events[-1]["completion"] == {"reason": "stop", "raw": "stop"}


async def test_an_undocumented_done_reason_stays_unknown_with_its_raw_value(monkeypatch, client):
    events = await _stream(monkeypatch, client, [_token("hi") + _done(done_reason="unload")])

    assert events[-1]["completion"] == {"reason": "unknown", "raw": "unload"}


async def test_done_stops_reading_the_body(monkeypatch, client):
    raw = _token("kept") + _done() + _token("dropped")

    events = await _stream(monkeypatch, client, [raw])

    assert _tokens(events) == ["kept"]


async def test_a_malformed_line_is_skipped_and_the_stream_continues(monkeypatch, client):
    raw = _token("a") + b"{not json\n" + _token("b") + _done()

    events = await _stream(monkeypatch, client, [raw])

    assert _tokens(events) == ["a", "b"]


async def test_empty_content_yields_no_token(monkeypatch, client):
    raw = _token("") + _line({}) + _line({"message": {}}) + _token("only") + _done()

    events = await _stream(monkeypatch, client, [raw])

    assert _tokens(events) == ["only"]


async def test_tool_calls_arrive_complete_and_are_yielded_before_usage(monkeypatch, client):
    raw = _line({"message": {"content": "", "tool_calls": TOOL_CALLS}}) + _done(
        prompt_eval_count=2, eval_count=1
    )

    events = await _stream(monkeypatch, client, [raw], tools=tool_schemas())

    assert events == [
        {"type": "tool_calls", "tool_calls": TOOL_CALLS},
        {
            "type": "usage",
            "tokens_used": 3,
            "prompt_tokens": 2,
            "completion_tokens": 1,
            "completion": {"reason": "unknown", "raw": None},
        },
    ]


async def test_a_later_tool_calls_chunk_replaces_the_earlier_one(monkeypatch, client):
    later = [{"function": {"name": "other", "arguments": {}}}]
    raw = (
        _line({"message": {"content": "", "tool_calls": TOOL_CALLS}})
        + _line({"message": {"content": "", "tool_calls": later}})
        + _done()
    )

    events = await _stream(monkeypatch, client, [raw], tools=tool_schemas())

    assert events[0] == {"type": "tool_calls", "tool_calls": later}


async def test_a_turn_with_no_tool_calls_yields_no_tool_calls_event(monkeypatch, client):
    events = await _stream(monkeypatch, client, [_token("plain") + _done()], tools=tool_schemas())

    assert [e["type"] for e in events] == ["token", "usage"]


async def test_closing_the_generator_midstream_releases_the_response(monkeypatch, client):
    released: list[bool] = []
    chunks = [_token("first"), _token("second"), _done()]

    install_wire_capture(monkeypatch, [closable_response(chunks, released)])
    stream = client.stream_with_history(HISTORY, make_config("ollama"), "SYS")

    assert await stream.__anext__() == {"type": "token", "content": "first"}
    await stream.aclose()

    assert released == [True]


async def test_a_non_200_status_raises_with_the_body_text(monkeypatch, client):
    with pytest.raises(ValueError, match="Ollama returned status 500: model not found"):
        await _stream(monkeypatch, client, [b"model not found"], status=500)


async def test_a_non_200_status_raises_on_the_tools_stream_too(monkeypatch, client):
    with pytest.raises(ValueError, match="Ollama returned status 400: bad tools"):
        await _stream(monkeypatch, client, [b"bad tools"], tools=tool_schemas(), status=400)
