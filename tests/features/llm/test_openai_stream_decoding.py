"""Pins how OpenAIClient decodes an SSE body into stream events.

Everything here drives the real client method through a mock transport that
hands back the exact byte chunks the test names, so line reassembly, the
tool_call fragment contract, the `[DONE]` stop and the malformed-chunk policy
are all exercised on the production path rather than on a decoder in isolation.
"""

import json

import pytest

from src.features.llm.clients.openai import OpenAIClient
from tests.features.llm.wire_capture import (
    chunked_response,
    closable_response,
    collect,
    install_wire_capture,
    make_config,
    tool_schemas,
)

HISTORY = [{"role": "user", "content": "hello"}]

NO_USAGE = {
    "type": "usage",
    "tokens_used": None,
    "prompt_tokens": None,
    "completion_tokens": None,
    "completion": {"reason": "unknown", "raw": None},
}


@pytest.fixture
def client():
    return OpenAIClient()


def _frame(obj) -> bytes:
    body = obj if isinstance(obj, str) else json.dumps(obj)
    return f"data: {body}\n\n".encode()


def _delta(content: str) -> bytes:
    return _frame({"choices": [{"delta": {"content": content}}]})


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
        return await collect(client.stream_with_history(HISTORY, make_config("openai"), "SYS"))
    return await collect(
        client.stream_with_tools(HISTORY, make_config("openai"), "SYS", tools=tools)
    )


async def test_a_frame_split_across_chunk_boundaries_is_reassembled(monkeypatch, client):
    raw = _delta("hel") + _delta("lo") + _frame("[DONE]")

    events = await _stream(monkeypatch, client, _split(raw, 7, 25, 44, 60))

    assert _tokens(events) == ["hel", "lo"]


async def test_several_frames_in_one_chunk_all_decode(monkeypatch, client):
    raw = _delta("a") + _delta("b") + _delta("c") + _frame("[DONE]")

    events = await _stream(monkeypatch, client, [raw])

    assert _tokens(events) == ["a", "b", "c"]


async def test_usage_from_the_final_chunk_is_yielded_last(monkeypatch, client):
    usage_frame = _frame(
        {"choices": [], "usage": {"total_tokens": 30, "prompt_tokens": 20, "completion_tokens": 10}}
    )

    events = await _stream(monkeypatch, client, [_delta("hi") + usage_frame + _frame("[DONE]")])

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


async def test_finish_reason_arrives_before_the_usage_chunk_and_done(monkeypatch, client):
    """The choice carrying `finish_reason` precedes the separate choice-less
    usage chunk and `[DONE]` — the decoder must remember it across chunks."""
    finish_frame = _frame({"choices": [{"delta": {}, "finish_reason": "length"}]})
    usage_frame = _frame(
        {"choices": [], "usage": {"total_tokens": 30, "prompt_tokens": 20, "completion_tokens": 10}}
    )

    events = await _stream(
        monkeypatch, client, [_delta("hi") + finish_frame + usage_frame + _frame("[DONE]")]
    )

    assert events[-1]["completion"] == {"reason": "length", "raw": "length"}


async def test_finish_reason_stop_normalizes_to_stop(monkeypatch, client):
    finish_frame = _frame({"choices": [{"delta": {}, "finish_reason": "stop"}]})

    events = await _stream(monkeypatch, client, [_delta("hi") + finish_frame + _frame("[DONE]")])

    assert events[-1]["completion"] == {"reason": "stop", "raw": "stop"}


async def test_a_stream_without_usage_still_ends_with_an_empty_usage_event(monkeypatch, client):
    events = await _stream(monkeypatch, client, [_delta("hi") + _frame("[DONE]")])

    assert events[-1] == NO_USAGE


async def test_done_stops_reading_the_body(monkeypatch, client):
    raw = _delta("kept") + _frame("[DONE]") + _delta("dropped")

    events = await _stream(monkeypatch, client, [raw])

    assert _tokens(events) == ["kept"]


async def test_a_malformed_frame_is_skipped_and_the_stream_continues(monkeypatch, client):
    raw = _delta("a") + b"data: {not json\n\n" + _delta("b") + _frame("[DONE]")

    events = await _stream(monkeypatch, client, [raw])

    assert _tokens(events) == ["a", "b"]


async def test_non_data_lines_and_empty_deltas_yield_nothing(monkeypatch, client):
    raw = (
        b": heartbeat\n\n"
        + b"event: ping\n\n"
        + _frame({"choices": [{"delta": {}}]})
        + _delta("")
        + _frame({"choices": []})
        + _delta("only")
        + _frame("[DONE]")
    )

    events = await _stream(monkeypatch, client, [raw])

    assert _tokens(events) == ["only"]


async def test_tool_call_fragments_are_assembled_by_index(monkeypatch, client):
    raw = (
        _frame(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_a",
                                    "type": "function",
                                    "function": {"name": "get_form", "arguments": '{"sc'},
                                }
                            ]
                        }
                    }
                ]
            }
        )
        + _frame(
            {
                "choices": [
                    {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'ope": 1}'}}]}}
                ]
            }
        )
        + _frame(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 1,
                                    "id": "call_b",
                                    "type": "function",
                                    "function": {"name": "other", "arguments": "{}"},
                                }
                            ]
                        }
                    }
                ]
            }
        )
        + _frame("[DONE]")
    )

    events = await _stream(monkeypatch, client, _split(raw, 90, 200, 330), tools=tool_schemas())

    assert events[0] == {
        "type": "tool_calls",
        "tool_calls": [
            {"id": "call_a", "type": "function", "function": {"name": "get_form", "arguments": '{"scope": 1}'}},
            {"id": "call_b", "type": "function", "function": {"name": "other", "arguments": "{}"}},
        ],
    }
    assert events[1] == NO_USAGE


async def test_a_turn_with_no_tool_calls_yields_no_tool_calls_event(monkeypatch, client):
    events = await _stream(
        monkeypatch, client, [_delta("plain") + _frame("[DONE]")], tools=tool_schemas()
    )

    assert [e["type"] for e in events] == ["token", "usage"]


async def test_tool_calls_are_yielded_before_usage(monkeypatch, client):
    raw = (
        _frame(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "id": "c", "type": "function", "function": {"name": "n", "arguments": "{}"}}
                            ]
                        }
                    }
                ],
                "usage": {"total_tokens": 3, "prompt_tokens": 2, "completion_tokens": 1},
            }
        )
        + _frame("[DONE]")
    )

    events = await _stream(monkeypatch, client, [raw], tools=tool_schemas())

    assert [e["type"] for e in events] == ["tool_calls", "usage"]
    assert events[1]["tokens_used"] == 3


async def test_closing_the_generator_midstream_releases_the_response(monkeypatch, client):
    released: list[bool] = []
    chunks = [_delta("first"), _delta("second"), _frame("[DONE]")]

    install_wire_capture(monkeypatch, [closable_response(chunks, released)])
    stream = client.stream_with_history(HISTORY, make_config("openai"), "SYS")

    assert await stream.__anext__() == {"type": "token", "content": "first"}
    await stream.aclose()

    assert released == [True]


async def test_a_non_200_status_raises_with_the_body_text(monkeypatch, client):
    with pytest.raises(ValueError, match="OpenAI returned status 503: upstream down"):
        await _stream(monkeypatch, client, [b"upstream down"], status=503)


async def test_a_non_200_status_raises_on_the_tools_stream_too(monkeypatch, client):
    with pytest.raises(ValueError, match="OpenAI returned status 401: bad key"):
        await _stream(monkeypatch, client, [b"bad key"], tools=tool_schemas(), status=401)
