"""Run-report and chat-trace retention against a scratch database."""

import os
import sys
from datetime import datetime, timedelta

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase

from src.features.generation.run_report_recorder import RunReportRecorder
from src.features.generation.run_report_repository import GenerationRunReportRepository
from src.features.housekeeping.tasks import prune_llm_traces, prune_run_reports
from src.features.llm.trace_repository import ChatCallTraceRepository


class TestRunReportRetention(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.repository = GenerationRunReportRepository()
        self.recorder = RunReportRecorder(self.repository)
        self.create_test_user()

    def _report(self, generation_id: str, age_days: int) -> None:
        self.create_test_generation(generation_id=generation_id)
        self.repository.save(generation_id, {"generation_id": generation_id})
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE generation_run_reports SET created_at = datetime('now', ?) "
                "WHERE generation_id = ?",
                (f"-{age_days} days", generation_id),
            )

    def _ids(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT generation_id FROM generation_run_reports ORDER BY generation_id")
            return [row["generation_id"] for row in cursor.fetchall()]

    def test_only_reports_past_the_window_are_listed_and_counted(self):
        self._report("old", 45)
        self._report("fresh", 2)

        assert self.repository.list_older_than(30) == ["old"]
        assert self.repository.count_older_than(30) == 1

    def test_the_pass_removes_the_old_report_and_keeps_the_recent_one(self):
        self._report("old", 45)
        self._report("fresh", 2)

        result = prune_run_reports(self.repository, self.recorder, 30)

        assert result == {"rows_removed": 1, "errors": []}
        assert self._ids() == ["fresh"]

    def test_zero_days_removes_nothing(self):
        self._report("old", 900)

        result = prune_run_reports(self.repository, self.recorder, 0)

        assert result == {"rows_removed": 0, "errors": []}
        assert self._ids() == ["old"]

    def test_the_generation_row_the_report_hangs_off_survives_the_pass(self):
        self._report("old", 45)

        prune_run_reports(self.repository, self.recorder, 30)

        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM generations WHERE id = 'old'")
            assert cursor.fetchone()["n"] == 1

    def test_a_report_that_will_not_delete_is_reported_not_raised(self):
        self._report("old", 45)
        self._report("older", 60)

        def explode(generation_id):
            if generation_id == "old":
                raise RuntimeError("locked")
            return self.repository.delete(generation_id)

        self.recorder.delete_report = explode
        result = prune_run_reports(self.repository, self.recorder, 30)

        assert result["rows_removed"] == 1
        assert result["errors"] == ["old: locked"]


class TestChatTraceRetention(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.repository = ChatCallTraceRepository()
        self.user_id = self.create_test_user()
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO chat_sessions (id, user_id, mode, name, status) "
                "VALUES ('s1', ?, 'generation', 'S', 'active')",
                (self.user_id,),
            )

    def _trace(self, trace_id: str, age_days: int) -> None:
        created_at = (datetime.now() - timedelta(days=age_days)).isoformat()
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO chat_llm_call_traces "
                "(id, session_id, purpose, iteration, provider, model, created_at) "
                "VALUES (?, 's1', 'chat', 1, 'openai', 'gpt-4', ?)",
                (trace_id, created_at),
            )

    def _ids(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT id FROM chat_llm_call_traces ORDER BY id")
            return [row["id"] for row in cursor.fetchall()]

    def test_the_count_agrees_with_what_the_prune_removes(self):
        self._trace("old", 30)
        self._trace("fresh", 1)

        before = self.repository.count_older_than(7)
        result = prune_llm_traces(self.repository, 7)

        assert before == 1
        assert result == {"rows_removed": 1, "errors": []}
        assert self._ids() == ["fresh"]

    def test_zero_days_removes_nothing(self):
        self._trace("old", 900)

        result = prune_llm_traces(self.repository, 0)

        assert result == {"rows_removed": 0, "errors": []}
        assert self._ids() == ["old"]

    def test_a_trace_written_moments_ago_is_never_pruned_at_the_shortest_window(self):
        self._trace("now", 0)

        prune_llm_traces(self.repository, 1)

        assert self._ids() == ["now"]
