"""The chat call-trace writer keeps persistence off the chat response path.

``ChatCallTraceRecorder.record()`` runs inside the chat turn (the provider
clients call it the moment a completion lands), so it may only snapshot and
enqueue. These cover the bounded lifecycle around that: the event loop keeps
running while a slow write is in flight, retention pruning never lands on the
response path, records are written in order from data the caller can no longer
mutate, a saturated queue drops instead of blocking, and shutdown drains.
"""

import asyncio
import json
import threading
import time
from typing import Any, Dict, List, Optional

import pytest

from src.features.llm.trace_recorder import (
    MAX_TRACE_FIELD_BYTES,
    MAX_TRACE_NODES,
    MAX_TRACE_RECORD_BYTES,
    MAX_TRACE_STRING_BYTES,
    ChatCallTraceRecorder,
    _snapshot,
    _TraversalBudget,
)


def stored_json_bytes(value) -> int:
    """Bytes the column receives: the repository encodes with plain json.dumps."""
    return len(json.dumps(value).encode("utf-8"))


class FakeTraceRepository:
    """Repository stand-in with controllable write/prune latency and failures."""

    def __init__(
        self,
        *,
        write_delay: float = 0.0,
        prune_delay: float = 0.0,
        fail_first: int = 0,
        gate: Optional[threading.Event] = None,
    ):
        self.write_delay = write_delay
        self.prune_delay = prune_delay
        self.fail_first = fail_first
        self.gate = gate
        self.created: List[Dict[str, Any]] = []
        self.prune_calls = 0
        self.entered_create = threading.Event()

    def create(self, **kwargs) -> str:
        self.entered_create.set()
        if self.gate is not None:
            self.gate.wait(5.0)
        if self.write_delay:
            time.sleep(self.write_delay)
        if self.fail_first > 0:
            self.fail_first -= 1
            raise RuntimeError("db down")
        self.created.append(kwargs)
        return f"trace-{len(self.created)}"

    def prune_older_than(self, days: int) -> int:
        if self.prune_delay:
            time.sleep(self.prune_delay)
        self.prune_calls += 1
        return 0


class FakeSettings:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def get_setting(self, key: str, default: Any = None) -> Any:
        return self.enabled


def make_recorder(repository, *, enabled: bool = True, queue_maxsize: int = 256):
    return ChatCallTraceRecorder(
        repository, FakeSettings(enabled), queue_maxsize=queue_maxsize,
    )


def record(recorder: ChatCallTraceRecorder, **overrides) -> None:
    payload: Dict[str, Any] = dict(
        session_id="session-1",
        user_id="user-1",
        purpose="chat",
        iteration=1,
        provider="openai",
        model="gpt-4",
        request_system=None,
        request_messages=[{"role": "user", "content": "hi"}],
        request_params={"temperature": 0.7},
        request_tools=None,
        response_text="hello",
        response_tool_calls=None,
        prompt_tokens=None,
        completion_tokens=None,
        duration_ms=1,
    )
    payload.update(overrides)
    recorder.record(**payload)


def wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class TestResponsePathIsNotBlocked:
    def test_event_loop_keeps_ticking_while_slow_writes_are_in_flight(self):
        repository = FakeTraceRepository(write_delay=0.3)
        recorder = make_recorder(repository)

        async def scenario() -> List[float]:
            ticks: List[float] = []

            async def ticker():
                while True:
                    ticks.append(time.monotonic())
                    await asyncio.sleep(0.02)

            task = asyncio.create_task(ticker())
            await asyncio.sleep(0.05)
            for i in range(5):
                record(recorder, iteration=i + 1)
            await asyncio.sleep(0.3)
            task.cancel()
            return ticks

        try:
            ticks = asyncio.run(scenario())
        finally:
            recorder.shutdown(drain=False, timeout=5.0)

        gaps = [b - a for a, b in zip(ticks, ticks[1:])]
        assert gaps, "ticker produced no ticks"
        assert max(gaps) < 0.1, f"event loop stalled for {max(gaps):.3f}s during record()"

    def test_record_returns_before_a_slow_write_completes(self):
        repository = FakeTraceRepository(write_delay=0.3)
        recorder = make_recorder(repository)
        try:
            started = time.monotonic()
            for i in range(5):
                record(recorder, iteration=i + 1)
            elapsed = time.monotonic() - started
        finally:
            recorder.shutdown(drain=False, timeout=5.0)

        assert elapsed < 0.1, f"five record() calls took {elapsed:.3f}s on the response path"

    def test_slow_prune_does_not_delay_record(self):
        repository = FakeTraceRepository(prune_delay=0.3)
        recorder = make_recorder(repository)
        try:
            started = time.monotonic()
            record(recorder)
            elapsed = time.monotonic() - started
            assert wait_until(lambda: repository.prune_calls == 1)
        finally:
            recorder.shutdown(timeout=5.0)

        assert elapsed < 0.1, f"record() took {elapsed:.3f}s with a slow prune"


class TestOrderingAndSnapshotting:
    def test_records_are_written_in_enqueue_order_with_their_own_identity(self):
        repository = FakeTraceRepository()
        recorder = make_recorder(repository)
        for i in range(10):
            record(recorder, iteration=i + 1, session_id=f"session-{i}")
        recorder.shutdown(timeout=5.0)

        assert [c["iteration"] for c in repository.created] == list(range(1, 11))
        assert [c["session_id"] for c in repository.created] == [f"session-{i}" for i in range(10)]

    def test_later_mutation_of_the_history_cannot_change_what_is_persisted(self):
        repository = FakeTraceRepository(write_delay=0.05)
        recorder = make_recorder(repository)
        messages = [{"role": "user", "content": "original"}]
        params = {"temperature": 0.7}

        record(recorder, request_messages=messages, request_params=params)
        messages.append({"role": "assistant", "content": "appended after the call"})
        messages[0]["content"] = "mutated"
        params["temperature"] = 999

        recorder.shutdown(timeout=5.0)

        assert repository.created[0]["request_messages"] == [{"role": "user", "content": "original"}]
        assert repository.created[0]["request_params"] == {"temperature": 0.7}


class TestSettingGate:
    def test_disabled_setting_enqueues_nothing_and_starts_no_worker(self):
        repository = FakeTraceRepository()
        recorder = make_recorder(repository, enabled=False)
        before = threading.active_count()
        for _ in range(50):
            record(recorder)

        assert recorder.stats()["queued"] == 0
        assert threading.active_count() == before
        assert repository.created == []


class TestSaturation:
    def test_full_queue_drops_the_new_record_without_blocking(self):
        gate = threading.Event()
        repository = FakeTraceRepository(gate=gate)
        recorder = make_recorder(repository, queue_maxsize=2)
        try:
            # One record occupies the writer (blocked on the gate), two fill
            # the queue; everything after that has nowhere to go.
            record(recorder, iteration=1)
            assert repository.entered_create.wait(5.0)
            record(recorder, iteration=2)
            record(recorder, iteration=3)
            assert wait_until(lambda: recorder.stats()["queued"] == 2)

            slowest = 0.0
            for i in range(10):
                started = time.monotonic()
                record(recorder, iteration=100 + i)
                slowest = max(slowest, time.monotonic() - started)

            stats = recorder.stats()
            assert stats["dropped"] == 10
            assert slowest < 0.05, f"a dropping record() took {slowest:.3f}s"
        finally:
            gate.set()
            recorder.shutdown(timeout=5.0)

        assert [c["iteration"] for c in repository.created] == [1, 2, 3]


class TestFailureHandling:
    def test_a_failing_write_is_counted_and_later_writes_continue(self, caplog):
        repository = FakeTraceRepository(fail_first=1)
        recorder = make_recorder(repository)
        with caplog.at_level("ERROR"):
            record(recorder, iteration=1)
            record(recorder, iteration=2)
            recorder.shutdown(timeout=5.0)

        stats = recorder.stats()
        assert stats["failed"] == 1
        assert stats["written"] == 1
        assert [c["iteration"] for c in repository.created] == [2]
        assert "Failed to persist chat LLM call trace" in caplog.text

    def test_a_failing_prune_does_not_kill_the_writer(self):
        repository = FakeTraceRepository()

        def boom(days):
            repository.prune_calls += 1
            raise RuntimeError("prune exploded")

        repository.prune_older_than = boom
        recorder = make_recorder(repository)
        record(recorder, iteration=1)
        assert wait_until(lambda: repository.prune_calls == 1)
        record(recorder, iteration=2)
        recorder.shutdown(timeout=5.0)

        assert [c["iteration"] for c in repository.created] == [1, 2]


class TestShutdown:
    def test_shutdown_drains_pending_writes_and_stops_the_worker(self):
        repository = FakeTraceRepository(write_delay=0.02)
        recorder = make_recorder(repository)
        for i in range(10):
            record(recorder, iteration=i + 1)

        started = time.monotonic()
        recorder.shutdown(drain=True, timeout=5.0)
        elapsed = time.monotonic() - started

        assert len(repository.created) == 10
        assert elapsed < 5.0
        assert not any(t.name == "chat-trace-writer" for t in threading.enumerate())

    def test_shutdown_without_drain_discards_pending_writes(self):
        repository = FakeTraceRepository(write_delay=0.2)
        recorder = make_recorder(repository)
        for i in range(10):
            record(recorder, iteration=i + 1)
        assert wait_until(lambda: recorder.stats()["queued"] >= 5)

        recorder.shutdown(drain=False, timeout=5.0)

        assert len(repository.created) < 10
        assert not any(t.name == "chat-trace-writer" for t in threading.enumerate())

    def test_record_after_shutdown_is_dropped_and_counted(self):
        repository = FakeTraceRepository()
        recorder = make_recorder(repository)
        recorder.shutdown(timeout=5.0)
        record(recorder)

        assert recorder.stats()["queued"] == 0
        assert recorder.stats()["dropped"] == 1
        assert repository.created == []


class TestByteCaps:
    def test_an_oversized_message_is_capped_and_marked(self):
        repository = FakeTraceRepository()
        recorder = make_recorder(repository)
        huge = "x" * (MAX_TRACE_FIELD_BYTES * 2)
        record(recorder, request_messages=[{"role": "user", "content": huge}])
        recorder.shutdown(timeout=5.0)

        stored = repository.created[0]["request_messages"]
        assert isinstance(stored, list)
        assert stored[0]["role"] == "user"
        assert "truncated" in stored[0]["content"]
        assert stored_json_bytes(stored) <= MAX_TRACE_FIELD_BYTES
        assert recorder.stats()["truncated"] == 1

    def test_every_field_of_an_enormous_trace_stays_within_the_cap(self):
        repository = FakeTraceRepository()
        recorder = make_recorder(repository)
        huge = "y" * (MAX_TRACE_FIELD_BYTES * 4)
        record(
            recorder,
            request_system=huge,
            request_messages=[{"role": "user", "content": huge} for _ in range(20)],
            request_params={"prompt": huge},
            response_text=huge,
        )
        recorder.shutdown(timeout=5.0)

        created = repository.created[0]
        for field in ("request_messages", "request_params"):
            assert stored_json_bytes(created[field]) <= MAX_TRACE_FIELD_BYTES
        for field in ("request_system", "response_text"):
            assert len(created[field].encode("utf-8")) <= MAX_TRACE_FIELD_BYTES
        assert recorder.stats()["truncated"] == 1

    def test_many_tiny_entries_stay_within_the_serialized_field_cap(self):
        # Every entry is far below the per-string cap, so the field only goes
        # over through sheer count plus the separators between entries.
        repository = FakeTraceRepository()
        recorder = make_recorder(repository)
        messages = [{"role": "user", "content": str(i)} for i in range(20000)]
        record(recorder, request_messages=messages)
        recorder.shutdown(timeout=5.0)

        stored = repository.created[0]["request_messages"]
        assert stored_json_bytes(stored) <= MAX_TRACE_FIELD_BYTES
        assert "earlier entries dropped" in stored[0]["content"]
        assert stored[-1]["content"] == "19999"

    def test_non_ascii_text_is_capped_by_bytes_not_code_points(self):
        repository = FakeTraceRepository()
        recorder = make_recorder(repository)
        # Each of these code points is 3 UTF-8 bytes, and six ASCII bytes once
        # JSON-escaped, so a code-point cap would overshoot several times over.
        multibyte = "日" * (MAX_TRACE_FIELD_BYTES - 100)
        record(
            recorder,
            request_system=multibyte,
            request_messages=[{"role": "user", "content": multibyte}],
            response_text=multibyte,
        )
        recorder.shutdown(timeout=5.0)

        created = repository.created[0]
        assert stored_json_bytes(created["request_messages"]) <= MAX_TRACE_FIELD_BYTES
        assert len(created["request_system"].encode("utf-8")) <= MAX_TRACE_FIELD_BYTES
        assert len(created["response_text"].encode("utf-8")) <= MAX_TRACE_FIELD_BYTES
        assert "truncated" in created["response_text"]

    def test_a_nested_string_is_capped_by_bytes(self):
        repository = FakeTraceRepository()
        recorder = make_recorder(repository)
        record(recorder, request_params={"prompt": "é" * MAX_TRACE_STRING_BYTES})
        recorder.shutdown(timeout=5.0)

        stored = repository.created[0]["request_params"]["prompt"]
        assert len(stored.encode("utf-8")) <= MAX_TRACE_STRING_BYTES + 100
        assert "truncated" in stored

    def test_an_oversized_response_text_keeps_its_head(self):
        repository = FakeTraceRepository()
        recorder = make_recorder(repository)
        record(recorder, response_text="a" * (MAX_TRACE_FIELD_BYTES + 500))
        recorder.shutdown(timeout=5.0)

        stored = repository.created[0]["response_text"]
        assert stored.startswith("aaa")
        assert "truncated" in stored
        assert len(stored.encode("utf-8")) <= MAX_TRACE_FIELD_BYTES


class TestStopAdmission:
    def test_a_timed_out_shutdown_still_stops_the_worker_and_a_later_join_succeeds(self):
        gate = threading.Event()
        repository = FakeTraceRepository(gate=gate)
        recorder = make_recorder(repository, queue_maxsize=2)
        try:
            # Writer stuck inside a write, queue full behind it: there is no
            # room for a stop token, so stopping cannot depend on one.
            record(recorder, iteration=1)
            assert repository.entered_create.wait(5.0)
            record(recorder, iteration=2)
            record(recorder, iteration=3)
            assert wait_until(lambda: recorder.stats()["queued"] == 2)

            recorder.shutdown(drain=True, timeout=0.01)
            assert any(t.name == "chat-trace-writer" for t in threading.enumerate())
        finally:
            gate.set()

        assert wait_until(lambda: not any(t.name == "chat-trace-writer" for t in threading.enumerate()))
        recorder.shutdown(timeout=5.0)
        assert [c["iteration"] for c in repository.created] == [1, 2, 3]

    def test_nothing_is_admitted_after_stop_begins_and_every_attempt_is_counted(self):
        gate = threading.Event()
        repository = FakeTraceRepository(gate=gate)
        recorder = make_recorder(repository, queue_maxsize=8)
        try:
            record(recorder, iteration=1)
            assert repository.entered_create.wait(5.0)
            record(recorder, iteration=2)
            assert wait_until(lambda: recorder.stats()["queued"] == 1)

            recorder.shutdown(drain=True, timeout=0.01)
            for i in range(5):
                record(recorder, iteration=100 + i)

            assert recorder.stats()["queued"] == 1, "a record was admitted after stop"
            assert recorder.stats()["dropped"] == 5
        finally:
            gate.set()

        recorder.shutdown(timeout=5.0)
        assert [c["iteration"] for c in repository.created] == [1, 2]
        stats = recorder.stats()
        assert stats["written"] + stats["dropped"] + stats["discarded"] == 7

    def test_a_non_draining_shutdown_counts_what_it_throws_away(self):
        repository = FakeTraceRepository(write_delay=0.2)
        recorder = make_recorder(repository)
        for i in range(10):
            record(recorder, iteration=i + 1)
        assert wait_until(lambda: recorder.stats()["queued"] >= 5)

        recorder.shutdown(drain=False, timeout=5.0)

        stats = recorder.stats()
        assert stats["discarded"] > 0
        assert stats["written"] + stats["discarded"] + stats["failed"] == 10


class TestBoundedTraversal:
    def _walk(self, prefix_entries):
        """Snapshot a history whose tail is fixed and whose head grows."""
        messages = (
            [{"role": "user", "content": f"discarded {i:06d}"} for i in range(prefix_entries)]
            + [{"role": "user", "content": "kept " + "k" * 4000} for _ in range(16)]
        )
        budget = _TraversalBudget()
        snapshot, cut = _snapshot(messages, budget)
        return budget, snapshot, cut

    def test_a_fixed_tail_costs_the_same_however_long_the_discarded_prefix(self):
        walks = {n: self._walk(n) for n in (100, 1000, 10000)}
        counts = {n: (b.nodes_visited, b.bytes_charged) for n, (b, _, _) in walks.items()}

        assert len(set(counts.values())) == 1, f"traversal cost tracks the prefix: {counts}"
        assert len({len(snapshot) for _, snapshot, _ in walks.values()}) == 1
        for prefix, (_, snapshot, cut) in walks.items():
            assert cut
            kept = len(snapshot) - 1
            assert f"{prefix + 16 - kept} earlier entries dropped" in snapshot[0]["content"]
            assert snapshot[-1]["content"].startswith("kept ")

    def test_the_node_cap_stops_a_field_that_is_within_its_byte_budget(self):
        budget = _TraversalBudget()
        snapshot, cut = _snapshot([{} for _ in range(MAX_TRACE_NODES * 4)], budget)

        assert cut
        assert budget.nodes_visited <= MAX_TRACE_NODES
        assert "earlier entries dropped" in snapshot[0]["content"]

    def test_a_deeply_nested_value_does_not_recurse_without_bound(self):
        nested: Any = "leaf"
        for _ in range(500):
            nested = [nested]
        snapshot, cut = _snapshot(nested, _TraversalBudget())

        assert cut
        assert snapshot is not None


class TestRecordBudget:
    def test_an_oversized_identity_field_drops_the_whole_record(self, caplog):
        repository = FakeTraceRepository()
        recorder = make_recorder(repository)
        with caplog.at_level("WARNING"):
            record(recorder, session_id="s" * (MAX_TRACE_RECORD_BYTES + 1))
        recorder.shutdown(timeout=5.0)

        assert recorder.stats()["dropped"] == 1
        assert recorder.stats()["queued"] == 0
        assert repository.created == []
        assert "refused" in caplog.text

    def test_ordinary_identity_is_kept_verbatim(self):
        repository = FakeTraceRepository()
        recorder = make_recorder(repository)
        record(
            recorder,
            session_id="01J8ZQ", user_id="user-42", purpose="chat_tools",
            provider="ollama", model="qwen3:30b-a3b",
        )
        recorder.shutdown(timeout=5.0)

        created = repository.created[0]
        assert created["session_id"] == "01J8ZQ"
        assert created["user_id"] == "user-42"
        assert created["purpose"] == "chat_tools"
        assert created["provider"] == "ollama"
        assert created["model"] == "qwen3:30b-a3b"
        assert recorder.stats()["dropped"] == 0


@pytest.fixture(autouse=True)
def _no_leaked_writers():
    yield
    leaked = [t for t in threading.enumerate() if t.name == "chat-trace-writer"]
    assert not leaked, "a trace writer thread outlived its test"
