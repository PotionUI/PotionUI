"""Persistence tests for chat_llm_call_traces (migration 084) and the
settings-gated recorder built on top of it.

Covers:
- create()/list_for_session() round-trip (JSON fields decode back)
- backfill_message_id() stamps only the NULL rows for that session
- delete_for_session() / delete_all()
- prune_older_than() removes expired rows and keeps recent ones
- cascade delete when the owning chat_session is removed
- ChatCallTraceRecorder gates persistence on the chat_llm_call_tracing setting
- ChatCallTraceRecorder throttles its opportunistic prune trigger
"""
import sys
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase

from src.features.chat.records import ChatSession
from src.features.chat.repository import ChatSessionRepository
from src.features.llm.trace_repository import ChatCallTraceRepository
from src.features.llm.trace_recorder import ChatCallTraceRecorder, PRUNE_THROTTLE_SECONDS
from src.platform.settings.repository import SettingRepository
from src.platform.settings.settings import Settings

try:
    from src.platform.util.ids import generate_ulid
except ImportError:
    def generate_ulid():
        import random
        import string
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=26))


class TestChatCallTraceRepository(PersistenceTestBase):
    def setUp(self):
        super().setUp()
        self.repo = ChatCallTraceRepository()
        self.session_repo = ChatSessionRepository()

        import src.features.llm.trace_repository
        src.features.llm.trace_repository.db = self.db
        import src.features.chat.repository
        src.features.chat.repository.db = self.db

        self.user_id = self.create_test_user()
        self.session = self.session_repo.create(ChatSession(
            id=generate_ulid(),
            user_id=self.user_id,
            mode='generation',
            name='Test Session',
            status='active',
            llm_config_id='test-llm',
        ))

    def _create_trace(self, **overrides):
        params = dict(
            session_id=self.session.id,
            user_id=self.user_id,
            purpose="chat",
            iteration=1,
            provider="openai",
            model="gpt-4",
            request_system="be helpful",
            request_messages=[{"role": "user", "content": "hi"}],
            request_params={"temperature": 0.7},
            request_tools=None,
            response_text="hello",
            response_tool_calls=None,
            prompt_tokens=10,
            completion_tokens=5,
            duration_ms=42,
        )
        params.update(overrides)
        return self.repo.create(**params)

    def test_create_and_list_round_trip(self):
        trace_id = self._create_trace()
        rows = self.repo.list_for_session(self.session.id)
        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == trace_id
        assert row["message_id"] is None
        assert row["request_messages"] == [{"role": "user", "content": "hi"}]
        assert row["request_params"] == {"temperature": 0.7}
        assert row["provider"] == "openai"
        assert row["prompt_tokens"] == 10

    def test_list_for_session_orders_by_created_then_iteration(self):
        self._create_trace(iteration=1)
        self._create_trace(iteration=2)
        rows = self.repo.list_for_session(self.session.id)
        assert [r["iteration"] for r in rows] == [1, 2]

    def test_backfill_message_id_only_touches_null_rows(self):
        self._create_trace()
        self._create_trace(iteration=2)
        updated = self.repo.backfill_message_id(self.session.id, "msg-1")
        assert updated == 2
        rows = self.repo.list_for_session(self.session.id)
        assert all(r["message_id"] == "msg-1" for r in rows)

        # A later turn's traces (still NULL) are untouched by an earlier backfill.
        self._create_trace(iteration=1)
        rows = self.repo.list_for_session(self.session.id)
        message_ids = sorted([r["message_id"] for r in rows], key=lambda v: v or "")
        assert message_ids == [None, "msg-1", "msg-1"]

    def test_delete_for_session(self):
        self._create_trace()
        deleted = self.repo.delete_for_session(self.session.id)
        assert deleted == 1
        assert self.repo.list_for_session(self.session.id) == []

    def test_delete_all(self):
        self._create_trace()
        deleted = self.repo.delete_all()
        assert deleted == 1

    def test_cascade_delete_with_session(self):
        self._create_trace()
        self.session_repo.delete(self.session.id)
        assert self.repo.list_for_session(self.session.id) == []

    def test_prune_older_than_deletes_expired_rows_only(self):
        old_id = self._create_trace()
        recent_id = self._create_trace()
        old_created_at = (datetime.now() - timedelta(days=10)).isoformat()
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE chat_llm_call_traces SET created_at = ? WHERE id = ?",
                (old_created_at, old_id),
            )

        deleted = self.repo.prune_older_than(days=7)

        assert deleted == 1
        remaining_ids = [r["id"] for r in self.repo.list_for_session(self.session.id)]
        assert remaining_ids == [recent_id]

    def test_prune_older_than_keeps_rows_inside_the_window(self):
        self._create_trace()
        deleted = self.repo.prune_older_than(days=7)
        assert deleted == 0
        assert len(self.repo.list_for_session(self.session.id)) == 1


class TestChatCallTraceRecorderSettingGate(PersistenceTestBase):
    def setUp(self):
        super().setUp()
        self.session_repo = ChatSessionRepository()

        import src.features.llm.trace_repository
        src.features.llm.trace_repository.db = self.db
        import src.features.chat.repository
        src.features.chat.repository.db = self.db
        import src.platform.settings.repository
        src.platform.settings.repository.db = self.db

        self.user_id = self.create_test_user()
        self.session = self.session_repo.create(ChatSession(
            id=generate_ulid(),
            user_id=self.user_id,
            mode='generation',
            name='Test Session',
            status='active',
            llm_config_id='test-llm',
        ))

        self.repository = ChatCallTraceRepository()
        self.settings = Settings(SettingRepository())

    def _record(self, recorder: ChatCallTraceRecorder):
        recorder.record(
            session_id=self.session.id,
            user_id=self.user_id,
            purpose="chat",
            iteration=1,
            provider="openai",
            model="gpt-4",
            request_system=None,
            request_messages=[],
            request_params={},
            request_tools=None,
            response_text="hi",
            response_tool_calls=None,
            prompt_tokens=None,
            completion_tokens=None,
            duration_ms=1,
        )

    def test_records_when_setting_defaults_missing_to_enabled(self):
        # No 'chat_llm_call_tracing' row seeded in this bare test DB — the
        # recorder's default (True) must still let it through.
        recorder = ChatCallTraceRecorder(self.repository, self.settings)
        self._record(recorder)
        assert len(self.repository.list_for_session(self.session.id)) == 1

    def test_does_not_record_when_setting_disabled(self):
        recorder = ChatCallTraceRecorder(self.repository, self.settings)
        # Migration 084 seeds chat_llm_call_tracing='true'; flip it off (insert
        # a row if some earlier-migrations tree never seeded one).
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO settings (id, key, value, value_type, description, type)
                VALUES ('s1', 'chat_llm_call_tracing', 'false', 'boolean', 'x', 'SYSTEM')
                ON CONFLICT(key) DO UPDATE SET value = 'false'
            """)
        self._record(recorder)
        assert self.repository.list_for_session(self.session.id) == []


class TestChatCallTraceRecorderPruneThrottle(unittest.TestCase):
    """Recorder triggers prune_older_than() off its write path, throttled by clock."""

    def setUp(self):
        self.repository = Mock()
        self.settings = Mock()
        self.settings.get_setting.return_value = True
        self.now = 1000.0
        self.recorder = ChatCallTraceRecorder(
            self.repository, self.settings, clock=lambda: self.now,
        )

    def _record(self):
        self.recorder.record(
            session_id="s1", user_id=None, purpose="chat", iteration=1,
            provider="openai", model="gpt-4", request_system=None,
            request_messages=[], request_params={}, request_tools=None,
            response_text=None, response_tool_calls=None,
            prompt_tokens=None, completion_tokens=None, duration_ms=1,
        )

    def test_prunes_on_first_record(self):
        self._record()
        self.repository.prune_older_than.assert_called_once_with()

    def test_does_not_prune_again_within_throttle_window(self):
        self._record()
        self.now += PRUNE_THROTTLE_SECONDS - 1
        self._record()
        self._record()
        assert self.repository.prune_older_than.call_count == 1

    def test_prunes_again_once_throttle_window_elapses(self):
        self._record()
        self.now += PRUNE_THROTTLE_SECONDS
        self._record()
        assert self.repository.prune_older_than.call_count == 2

    def test_does_not_prune_when_tracing_disabled(self):
        self.settings.get_setting.return_value = False
        self._record()
        self.repository.create.assert_not_called()
        self.repository.prune_older_than.assert_not_called()
