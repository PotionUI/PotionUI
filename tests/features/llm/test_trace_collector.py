"""Tests for the chat LLM call-trace collector (trace_collector.py).

Covers the ContextVar-based no-op-unless-active contract that
src.features.llm.clients.openai / ollama rely on: record() must be silent
with no active context or no installed recorder, must forward the active
purpose/session/user and an incrementing iteration counter when active, and
must restore the outer context on exit (nesting, e.g. title generation inside
a chat turn).
"""
import asyncio
from unittest.mock import Mock

from src.features.llm import trace_collector


def _reset_recorder():
    trace_collector.set_recorder(None)


class TestTraceCollectorInactive:
    def setup_method(self):
        _reset_recorder()

    def test_record_is_noop_with_no_active_context(self):
        recorder = Mock()
        trace_collector.set_recorder(recorder)
        # No activate() — record() must not touch the recorder at all.
        trace_collector.record(
            provider="openai", model="gpt-4", request_system="sys",
            request_messages=[{"role": "user", "content": "hi"}], request_params={},
        )
        recorder.record.assert_not_called()

    def test_record_is_noop_with_no_installed_recorder(self):
        # Recorder never installed (e.g. collector imported before bootstrap).
        with trace_collector.activate("session-1", "user-1", purpose="chat"):
            trace_collector.record(
                provider="openai", model="gpt-4", request_system="sys",
                request_messages=[], request_params={},
            )
        # No exception, and is_active() correctly reports the window.
        assert not trace_collector.is_active()


class TestTraceCollectorActive:
    def setup_method(self):
        _reset_recorder()

    def teardown_method(self):
        _reset_recorder()

    def test_record_forwards_context_and_call_fields(self):
        recorder = Mock()
        trace_collector.set_recorder(recorder)
        with trace_collector.activate("session-1", "user-1", purpose="chat"):
            trace_collector.record(
                provider="ollama",
                model="llama3",
                request_system="you are helpful",
                request_messages=[{"role": "user", "content": "hi"}],
                request_params={"temperature": 0.7},
                request_tools=["tool_a"],
                response_text="hello",
                response_tool_calls=None,
                prompt_tokens=10,
                completion_tokens=5,
                duration_ms=123,
            )
        recorder.record.assert_called_once()
        kwargs = recorder.record.call_args.kwargs
        assert kwargs["session_id"] == "session-1"
        assert kwargs["user_id"] == "user-1"
        assert kwargs["purpose"] == "chat"
        assert kwargs["iteration"] == 1
        assert kwargs["provider"] == "ollama"
        assert kwargs["model"] == "llama3"
        assert kwargs["request_messages"] == [{"role": "user", "content": "hi"}]
        assert kwargs["duration_ms"] == 123

    def test_iteration_increments_within_one_activation(self):
        recorder = Mock()
        trace_collector.set_recorder(recorder)
        with trace_collector.activate("session-1", "user-1", purpose="chat_tools"):
            for _ in range(3):
                trace_collector.record(
                    provider="ollama", model="m", request_system=None,
                    request_messages=[], request_params={},
                )
        iterations = [c.kwargs["iteration"] for c in recorder.record.call_args_list]
        assert iterations == [1, 2, 3]

    def test_activation_is_scoped_and_restored_after_exit(self):
        recorder = Mock()
        trace_collector.set_recorder(recorder)
        with trace_collector.activate("session-1", "user-1", purpose="chat"):
            assert trace_collector.is_active()
        assert not trace_collector.is_active()

    def test_nested_activation_overrides_purpose_then_restores_outer(self):
        recorder = Mock()
        trace_collector.set_recorder(recorder)
        with trace_collector.activate("session-1", "user-1", purpose="chat"):
            with trace_collector.activate("session-1", "user-1", purpose="title"):
                trace_collector.record(
                    provider="ollama", model="m", request_system=None,
                    request_messages=[], request_params={},
                )
            trace_collector.record(
                provider="ollama", model="m", request_system=None,
                request_messages=[], request_params={},
            )
        purposes = [c.kwargs["purpose"] for c in recorder.record.call_args_list]
        assert purposes == ["title", "chat"]

    def test_record_never_raises_when_recorder_fails(self):
        recorder = Mock()
        recorder.record.side_effect = RuntimeError("db down")
        trace_collector.set_recorder(recorder)
        with trace_collector.activate("session-1", "user-1", purpose="chat"):
            # Must not propagate — tracing failure can't break the chat turn.
            trace_collector.record(
                provider="ollama", model="m", request_system=None,
                request_messages=[], request_params={},
            )


async def _inner_stream(record_mid_stream: bool):
    """Shaped like a provider client's stream_with_history: yields tokens,
    optionally records mid-stream (mirrors mid-loop bookkeeping some clients
    do), then records once more after the token loop — exactly where
    clients/openai.py's stream_with_history calls trace_collector.record()
    once the SSE loop is exhausted, but before the generator itself ends.
    """
    yield {"type": "token", "content": "a"}
    if record_mid_stream:
        trace_collector.record(
            provider="openai", model="gpt-4", request_system="sys",
            request_messages=[], request_params={}, response_text="a",
        )
    yield {"type": "token", "content": "b"}
    trace_collector.record(
        provider="openai", model="gpt-4", request_system="sys",
        request_messages=[], request_params={}, response_text="ab",
    )


async def _outer_stream(session_id: str, user_id: str, purpose: str, record_mid_stream: bool = False):
    """Shaped like ConversationRunner.send_message_stream: an async generator
    that activates the trace context and consumes an inner async generator
    with `async for`, re-yielding its events (e.g. to an SSE loop).
    """
    with trace_collector.activate(session_id, user_id, purpose=purpose):
        async for event in _inner_stream(record_mid_stream):
            yield event


class TestTraceCollectorStreamingPropagation:
    """Proves trace context survives the real shape: an async generator that
    `with activate(...)`-wraps an `async for` over another async generator,
    itself driven step-by-step by an outer consumer (like an SSE loop).
    """

    def setup_method(self):
        _reset_recorder()

    def teardown_method(self):
        _reset_recorder()

    @staticmethod
    async def _drain_step_by_step(gen):
        """Consume an async generator one `__anext__()` at a time, like an
        SSE loop would, rather than a single `async for` — so a bug where
        context only survives inside one bulk consumption wouldn't be masked.
        """
        events = []
        while True:
            try:
                events.append(await gen.__anext__())
            except StopAsyncIteration:
                return events

    async def test_context_propagates_through_nested_async_generator_stream(self):
        recorder = Mock()
        trace_collector.set_recorder(recorder)

        events = await self._drain_step_by_step(
            _outer_stream("session-1", "user-1", purpose="chat", record_mid_stream=True)
        )

        assert [e["content"] for e in events] == ["a", "b"]
        assert recorder.record.call_count == 2
        calls = recorder.record.call_args_list
        for call in calls:
            assert call.kwargs["session_id"] == "session-1"
            assert call.kwargs["user_id"] == "user-1"
            assert call.kwargs["purpose"] == "chat"
        # Iteration increments across the whole activation, mid-stream and
        # post-loop record() calls alike.
        assert [c.kwargs["iteration"] for c in calls] == [1, 2]
        assert calls[0].kwargs["response_text"] == "a"
        assert calls[1].kwargs["response_text"] == "ab"

    async def test_context_restored_after_outer_generator_exhausted(self):
        recorder = Mock()
        trace_collector.set_recorder(recorder)

        await self._drain_step_by_step(_outer_stream("session-1", "user-1", purpose="chat"))
        assert not trace_collector.is_active()

        # record() calls made after the generator finishes (outside its
        # `with activate(...)` window) must be silent no-ops.
        trace_collector.record(
            provider="openai", model="gpt-4", request_system=None,
            request_messages=[], request_params={},
        )
        assert recorder.record.call_count == 1  # only the post-loop record() inside the generator

    async def test_record_is_noop_when_inner_generator_driven_without_activation(self):
        recorder = Mock()
        trace_collector.set_recorder(recorder)

        # Drive the inner generator directly, bypassing _outer_stream's
        # `with activate(...)` — mirrors a caller that forgets to activate.
        events = await self._drain_step_by_step(_inner_stream(record_mid_stream=True))

        assert [e["content"] for e in events] == ["a", "b"]
        recorder.record.assert_not_called()

    async def test_two_concurrent_streams_keep_independent_contexts(self):
        """Two concurrently-streaming chat turns must not leak trace context.

        In production each `send_message_stream` SSE call is driven as its
        own asyncio Task (FastAPI's StreamingResponse iterates the generator
        inside the request's Task), and contextvars.Context is copy-on-Task,
        not copy-on-generator. Drive each stream via asyncio.create_task to
        match that topology — a naive single-Task interleave of two bare
        generators is *not* representative here and would (correctly) show
        cross-talk, since a plain generator shares its caller's Context.
        """
        recorder = Mock()
        trace_collector.set_recorder(recorder)

        async def _drain(session_id, user_id, purpose):
            return await self._drain_step_by_step(
                _outer_stream(session_id, user_id, purpose=purpose, record_mid_stream=True)
            )

        results_a, results_b = await asyncio.gather(
            asyncio.create_task(_drain("session-a", "user-a", "chat")),
            asyncio.create_task(_drain("session-b", "user-b", "chat_tools")),
        )

        assert [e["content"] for e in results_a] == ["a", "b"]
        assert [e["content"] for e in results_b] == ["a", "b"]

        sessions = {c.kwargs["session_id"] for c in recorder.record.call_args_list}
        purposes = {c.kwargs["purpose"] for c in recorder.record.call_args_list}
        assert sessions == {"session-a", "session-b"}
        assert purposes == {"chat", "chat_tools"}
        # Each Task's two record() calls (mid-stream + post-loop) kept their
        # own iteration counter, unpolluted by the concurrently-running Task.
        by_session = {"session-a": [], "session-b": []}
        for c in recorder.record.call_args_list:
            by_session[c.kwargs["session_id"]].append(c.kwargs["iteration"])
        assert by_session["session-a"] == [1, 2]
        assert by_session["session-b"] == [1, 2]
