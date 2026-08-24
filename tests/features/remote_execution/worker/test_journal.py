"""WorkerJournal: cursor ordering, restart survival, replay filtering."""

from datetime import datetime, timezone

from src.features.remote_execution.worker.journal import WorkerJournal
from src.platform.worker_protocol import JobEventV1


def _event(execution_id: str, cursor: int, kind: str = "log") -> JobEventV1:
    return JobEventV1(
        execution_id=execution_id, worker_id="worker-1", cursor=cursor,
        emitted_at=datetime.now(timezone.utc), kind=kind,
    )


def test_start_is_idempotent_and_returns_the_existing_record(tmp_path):
    journal = WorkerJournal(tmp_path)
    first = journal.start("exec-1", "digest-a")
    second = journal.start("exec-1", "digest-b")
    assert first is second
    assert second.request_digest == "digest-a"


def test_events_after_filters_by_cursor_strictly_greater_than(tmp_path):
    journal = WorkerJournal(tmp_path)
    journal.start("exec-1", "digest-a")
    journal.append("exec-1", _event("exec-1", 1))
    journal.append("exec-1", _event("exec-1", 2))
    journal.append("exec-1", _event("exec-1", 3))

    assert [e.cursor for e in journal.events_after("exec-1", 0)] == [1, 2, 3]
    assert [e.cursor for e in journal.events_after("exec-1", 1)] == [2, 3]
    assert [e.cursor for e in journal.events_after("exec-1", 3)] == []


def test_a_new_journal_instance_over_the_same_directory_recovers_state(tmp_path):
    first = WorkerJournal(tmp_path)
    first.start("exec-1", "digest-a")
    first.append("exec-1", _event("exec-1", 1, kind="accepted"))
    first.append("exec-1", _event("exec-1", 2, kind="succeeded"))

    second = WorkerJournal(tmp_path)
    record = second.get("exec-1")

    assert record is not None
    assert record.request_digest == "digest-a"
    assert [e.kind for e in record.events] == ["accepted", "succeeded"]
    assert record.is_terminal is True


def test_unknown_execution_has_no_record(tmp_path):
    journal = WorkerJournal(tmp_path)
    assert journal.get("never-submitted") is None
    assert journal.events_after("never-submitted", 0) == []


def test_next_cursor_increments_from_the_last_appended_event(tmp_path):
    journal = WorkerJournal(tmp_path)
    record = journal.start("exec-1", "digest-a")
    assert record.next_cursor == 1

    journal.append("exec-1", _event("exec-1", 1))
    assert journal.get("exec-1").next_cursor == 2
