"""Pins how OpenAIClient decodes an SSE body into stream events.

Everything here drives the real client method through a mock transport that
hands back the exact byte chunks the test names, so line reassembly, the
tool_call fragment contract, the `[DONE]` stop and the malformed-chunk policy
are all exercised on the production path rather than on a decoder in isolation.
"""

import json

import pytest

from src.features.llm.clients.openai import OpenAIClient
from src.features.llm.clients.openai_wire import OpenAICompatSSEDecoder
from src.features.llm.clients.wire_events import Done, RecordTooLarge, TextDelta, ToolCallDelta
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


def _frame(obj, *, no_space: bool = False, ending: str = "\n\n") -> bytes:
    body = obj if isinstance(obj, str) else json.dumps(obj)
    sep = ":" if no_space else ": "
    return f"data{sep}{body}{ending}".encode()


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


# ---------------------------------------------------------------------------
# LLM-09: proper SSE record framing (WHATWG event-stream field algorithm)
# ---------------------------------------------------------------------------

async def test_no_space_after_the_data_colon_is_still_parsed(monkeypatch, client):
    """`data:{...}` (no space) is exactly as valid as `data: {...}` — the
    decoder must strip AT MOST one leading space, never require one."""
    raw = _frame({"choices": [{"delta": {"content": "hello"}}]}, no_space=True) + _frame("[DONE]")

    events = await _stream(monkeypatch, client, [raw])

    assert _tokens(events) == ["hello"]


async def test_no_space_after_the_data_colon_on_the_tools_stream_too(monkeypatch, client):
    raw = _frame({"choices": [{"delta": {"content": "hello"}}]}, no_space=True) + _frame("[DONE]")

    events = await _stream(monkeypatch, client, [raw], tools=tool_schemas())

    assert _tokens(events) == ["hello"]


def _split_data_record(payload: str, split_at: int) -> bytes:
    """One SSE record whose `data:` value is legally split across two
    `data:` lines — real event-stream framing joins their values with
    `"\\n"` before the whole thing is interpreted as one payload; the split
    point lands mid-token so a naive per-line JSON parse would see two
    invalid halves instead of one valid whole."""
    return f"data: {payload[:split_at]}\ndata: {payload[split_at:]}\n\n".encode()


async def test_multiline_json_split_between_lexical_units_is_joined(monkeypatch, client):
    payload = json.dumps({"choices": [{"delta": {"content": "hello"}}]})
    split_at = payload.index('"content"') + len('"content"')  # mid-token: right after the key
    raw = _split_data_record(payload, split_at) + _frame("[DONE]")

    events = await _stream(monkeypatch, client, [raw])

    assert _tokens(events) == ["hello"]


async def test_multiline_json_split_on_the_tools_stream_too(monkeypatch, client):
    payload = json.dumps({"choices": [{"delta": {"content": "hello"}}]})
    split_at = payload.index('"content"') + len('"content"')
    raw = _split_data_record(payload, split_at) + _frame("[DONE]")

    events = await _stream(monkeypatch, client, [raw], tools=tool_schemas())

    assert _tokens(events) == ["hello"]


async def test_a_comment_line_between_two_data_lines_of_the_same_record_is_ignored(monkeypatch, client):
    payload = json.dumps({"choices": [{"delta": {"content": "hello"}}]})
    split_at = payload.index('"content"') + len('"content"')
    raw = (
        f"data: {payload[:split_at]}\n: a keep-alive comment\ndata: {payload[split_at:]}\n\n".encode()
        + _frame("[DONE]")
    )

    events = await _stream(monkeypatch, client, [raw])

    assert _tokens(events) == ["hello"]


async def test_multiple_multiline_records_in_one_chunk_all_decode(monkeypatch, client):
    payload_a = json.dumps({"choices": [{"delta": {"content": "a"}}]})
    payload_b = json.dumps({"choices": [{"delta": {"content": "b"}}]})
    split_a = payload_a.index('"content"')
    split_b = payload_b.index('"content"')
    raw = _split_data_record(payload_a, split_a) + _split_data_record(payload_b, split_b) + _frame("[DONE]")

    events = await _stream(monkeypatch, client, [raw])

    assert _tokens(events) == ["a", "b"]


async def test_multibyte_text_split_across_byte_chunks(monkeypatch, client):
    """A UTF-8 multibyte character (café's `é`, 2 bytes) cut in the middle of
    its own byte sequence by the transport must still decode correctly —
    httpx's own incremental decoder handles this beneath the record framer,
    which never sees a partial line, but this pins that the record layer
    built on top of it doesn't regress that guarantee. `ensure_ascii=False`
    is required here — the default `json.dumps` escapes 'é' as the ASCII
    sequence `\\u00e9`, which would never exercise a real multibyte split."""
    payload = json.dumps({"choices": [{"delta": {"content": "café"}}]}, ensure_ascii=False)
    raw = f"data: {payload}\n\n".encode() + _frame("[DONE]")
    # 'é' encodes as the two bytes 0xC3 0xA9 — cut the chunk boundary
    # between them.
    split_index = raw.index("caf".encode()) + len("caf".encode()) + 1

    events = await _stream(monkeypatch, client, _split(raw, split_index))

    assert _tokens(events) == ["café"]


@pytest.mark.parametrize("ending", ["\n\n", "\r\n\r\n", "\r\r"], ids=["lf", "crlf", "cr"])
async def test_every_documented_line_ending_style_decodes_identically(monkeypatch, client, ending):
    raw = _frame({"choices": [{"delta": {"content": "hi"}}]}, ending=ending) + _frame("[DONE]", ending=ending)

    events = await _stream(monkeypatch, client, [raw])

    assert _tokens(events) == ["hi"]


async def test_an_unfinished_record_at_eof_emits_nothing(monkeypatch, client):
    """A record with no terminating blank line before the connection ends
    is discarded — never dispatched, so it emits neither text nor a
    completion event for what would have been valid JSON had it closed."""
    raw = _delta("kept") + b"data: " + json.dumps({"choices": [{"delta": {"content": "lost"}}]}).encode()

    events = await _stream(monkeypatch, client, [raw])

    assert _tokens(events) == ["kept"]
    # No Done ever arrived either, so the stream falls through to the
    # unverified default rather than fabricating a finish reason.
    assert events[-1] == NO_USAGE


async def test_an_incomplete_done_sentinel_at_eof_is_never_treated_as_done(monkeypatch, client):
    """`data: [DON` with no closing blank line is not `[DONE]` and is not
    dispatched at all — the body simply ended, not "completed"."""
    raw = _delta("kept") + b"data: [DON"

    events = await _stream(monkeypatch, client, [raw])

    assert _tokens(events) == ["kept"]
    assert events[-1] == NO_USAGE


async def test_an_oversized_record_is_dropped_and_the_next_record_still_decodes(monkeypatch, client):
    huge = "x" * (2 * 1024 * 1024)  # well past MAX_RECORD_BYTES
    raw = f"data: {huge}\n\n".encode() + _delta("after") + _frame("[DONE]")

    events = await _stream(monkeypatch, client, [raw])

    assert _tokens(events) == ["after"]


# ---------------------------------------------------------------------------
# LLM-09 rework: the dropped record's OWN discard state must persist to its
# own blank-line boundary. Exercised directly against the decoder with a
# monkeypatched tiny `MAX_RECORD_BYTES` (a class attribute) so the fixtures
# read as arithmetic rather than megabyte-sized strings — this is the one
# place in this file that talks to the decoder in isolation rather than
# through a real client method, because "does the discard flag survive to
# its boundary" is a decoder-internal property no client-level assertion
# can distinguish from "it happened to work this time". The two real-stream
# fixtures below (history AND tools) are the end-to-end proof the bug
# (a dropped record's suffix reaching the client as if it were a fresh one)
# cannot resurface through the actual production call path.
# ---------------------------------------------------------------------------

def _decode(monkeypatch, max_bytes: int, lines: list[str]) -> list:
    monkeypatch.setattr(OpenAICompatSSEDecoder, "MAX_RECORD_BYTES", max_bytes)
    decoder = OpenAICompatSSEDecoder("test")
    events: list = []
    for line in lines:
        events.extend(decoder.feed(line))
    return events


def test_overflow_suffix_that_would_decode_as_content_is_discarded(monkeypatch):
    events = _decode(monkeypatch, 8, [
        "data: " + "x" * 20,
        'data: {"choices": [{"delta": {"content": "leaked"}}]}',
        "",
    ])

    assert events == [RecordTooLarge(20)]


def test_overflow_suffix_that_would_decode_as_a_tool_fragment_is_discarded(monkeypatch):
    events = _decode(monkeypatch, 8, [
        "data: " + "x" * 20,
        'data: {"choices": [{"delta": {"tool_calls": '
        '[{"index": 0, "id": "c", "type": "function", "function": {"name": "n", "arguments": "{}"}}]}}]}',
        "",
    ])

    assert events == [RecordTooLarge(20)]


def test_overflow_suffix_that_would_decode_as_done_is_discarded(monkeypatch):
    """The exact reported bug: an oversized value immediately followed by
    `data: [DONE]` must never dispatch `Done` from the dropped record's
    own suffix."""
    events = _decode(monkeypatch, 8, [
        "data: " + "x" * 20,
        "data: [DONE]",
        "",
    ])

    assert events == [RecordTooLarge(20)]


def test_a_comment_mid_discard_is_also_ignored(monkeypatch):
    events = _decode(monkeypatch, 8, [
        "data: " + "x" * 20,
        ": a comment",
        "data: [DONE]",
        "",
    ])

    assert events == [RecordTooLarge(20)]


def test_multiline_record_exceeding_the_bound_only_cumulatively_is_dropped(monkeypatch):
    events = _decode(monkeypatch, 8, [
        "data: 1234",    # 4 bytes — within the 8-byte cap so far
        "data: 56789",   # +5 bytes = 9 > 8 — trips here, not on the first line
        "",
    ])

    assert events == [RecordTooLarge(9)]


def test_many_empty_data_fields_eventually_trip_the_bound(monkeypatch):
    """Every `data:` line costs at least one byte toward the bound, even an
    empty one — otherwise 10k of them would grow the retained list (and the
    eventual `"\\n"`-joined payload) forever without ever tripping it."""
    monkeypatch.setattr(OpenAICompatSSEDecoder, "MAX_RECORD_BYTES", 100)
    decoder = OpenAICompatSSEDecoder("test")
    events: list = []
    for _ in range(10_000):
        events.extend(decoder.feed("data:"))
    events.extend(decoder.feed(""))

    too_large = [e for e in events if isinstance(e, RecordTooLarge)]
    assert len(too_large) == 1
    assert too_large[0].size == 101


def test_recovery_at_the_following_complete_record(monkeypatch):
    # The cap is raised to 100 here (rather than the 8 used above) purely so
    # the RECOVERY record's own real JSON (43 bytes) fits under it — the
    # trigger record (200 "x"s) still comfortably overflows either way.
    events = _decode(monkeypatch, 100, [
        "data: " + "x" * 200,
        "",
        'data: {"choices": [{"delta": {"content": "ok"}}]}',
        "",
    ])

    assert events == [RecordTooLarge(200), TextDelta("ok")]


def test_malformed_complete_record_is_still_skipped_after_an_unrelated_overflow(monkeypatch):
    """The pre-existing malformed-JSON skip policy is untouched by the
    overflow/discard machinery — a normal-sized but broken record still
    just logs and moves on (no RecordTooLarge for it), and the next good
    one still decodes, after an unrelated EARLIER record was dropped for
    size."""
    events = _decode(monkeypatch, 100, [
        "data: " + "x" * 200,
        "",
        "data: {not json",
        "",
        'data: {"choices": [{"delta": {"content": "ok"}}]}',
        "",
    ])

    assert events == [RecordTooLarge(200), TextDelta("ok")]


async def test_overflow_suffix_never_surfaces_as_done_on_the_history_stream(monkeypatch, client):
    # Cap raised to 100 (vs. the 8 used in the decoder-level tests above) so
    # the real "still going" delta (52 bytes) fits under it while the 200
    # "x"s still comfortably overflow.
    monkeypatch.setattr(OpenAICompatSSEDecoder, "MAX_RECORD_BYTES", 100)
    raw = (
        (f"data: {'x' * 200}\n").encode()
        + b"data: [DONE]\n\n"
        + _delta("still going")
        + _frame("[DONE]")
    )

    events = await _stream(monkeypatch, client, [raw])

    assert _tokens(events) == ["still going"]


async def test_overflow_suffix_never_surfaces_as_done_on_the_tools_stream(monkeypatch, client):
    monkeypatch.setattr(OpenAICompatSSEDecoder, "MAX_RECORD_BYTES", 100)
    raw = (
        (f"data: {'x' * 200}\n").encode()
        + b"data: [DONE]\n\n"
        + _delta("still going")
        + _frame("[DONE]")
    )

    events = await _stream(monkeypatch, client, [raw], tools=tool_schemas())

    assert _tokens(events) == ["still going"]


async def test_a_non_200_status_raises_with_the_body_text(monkeypatch, client):
    with pytest.raises(ValueError, match="OpenAI returned status 503: upstream down"):
        await _stream(monkeypatch, client, [b"upstream down"], status=503)


async def test_a_non_200_status_raises_on_the_tools_stream_too(monkeypatch, client):
    with pytest.raises(ValueError, match="OpenAI returned status 401: bad key"):
        await _stream(monkeypatch, client, [b"bad key"], tools=tool_schemas(), status=401)
