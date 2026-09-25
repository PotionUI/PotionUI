from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.chat import routes as chat_mod
from src.features.chat.repository import ChatRepository
from src.features.chat.routes import ChatController
from src.features.chat.runtime import ChatRuntime
from src.features.chat.turns import ChatTurnRegistry
from src.features.llm.trace_repository import ChatCallTraceRepository
from src.platform.http.base_controller import APIResponse
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User


def _user(account_type=AccountType.ADMIN, user_id="admin-1"):
    return User(
        id=user_id,
        username=user_id,
        email=f"{user_id}@example.com",
        password_hash="$2b$12$x",
        account_type=account_type,
        created_at=datetime.utcnow(),
        last_login=None,
    )


def _plugins():
    plugins = Mock()
    ctx = Mock()
    ctx.data = {}
    plugins.execute_hook.return_value = (ctx, [])
    return plugins


class TestAdminBulkDeleteSessionsController(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.repo = ChatRepository()
        self.runtime = ChatRuntime(
            chat_repository=self.repo,
            llm_service=Mock(),
            response_processor=Mock(),
            plugin_registry=_plugins(),
            chat_mode_registry=Mock(),
        )
        self.controller = ChatController(self.runtime, ChatTurnRegistry())
        self.user_a = self.create_test_user("user-a", "usera", "usera@example.com")
        self.user_b = self.create_test_user("user-b", "userb", "userb@example.com")
        self.session_a = self._session_with_content(self.user_a)
        self.session_b = self._session_with_content(self.user_b)

    def _session_with_content(self, user_id):
        session = self.repo.create_session(user_id=user_id, name="s")
        self.repo.add_message(session.id, "user", "hello")
        ChatCallTraceRepository().create(
            session_id=session.id,
            user_id=user_id,
            purpose="chat",
            iteration=1,
            provider="p",
            model="m",
            request_system=None,
            request_messages=[],
            request_params={},
            request_tools=None,
            response_text="hi",
            response_tool_calls=None,
            prompt_tokens=1,
            completion_tokens=1,
            duration_ms=1,
        )
        return session

    def _count(self, table, session_id):
        column = "id" if table == "chat_sessions" else "session_id"
        with self.db.get_cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (session_id,))
            return cursor.fetchone()[0]

    def _assert_gone(self, session_id):
        for table in ("chat_sessions", "chat_messages", "chat_llm_call_traces"):
            self.assertEqual(self._count(table, session_id), 0, table)

    def _assert_present(self, session_id):
        for table in ("chat_sessions", "chat_messages", "chat_llm_call_traces"):
            self.assertEqual(self._count(table, session_id), 1, table)

    def test_admin_deletes_sessions_of_other_users_with_messages_and_traces(self):
        response = self.controller.admin_bulk_delete_sessions(
            [self.session_a.id, self.session_b.id], _user()
        )

        self.assertTrue(response.success)
        self.assertEqual(response.data["deleted_count"], 2)
        self.assertEqual(response.data["failed_count"], 0)
        self._assert_gone(self.session_a.id)
        self._assert_gone(self.session_b.id)

    def test_unknown_ids_are_reported_as_failed_without_blocking_others(self):
        response = self.controller.admin_bulk_delete_sessions(
            [self.session_a.id, "missing-session"], _user()
        )

        self.assertEqual(response.data["deleted_count"], 1)
        self.assertEqual(response.data["failed_count"], 1)
        self.assertEqual(response.data["failed_ids"], ["missing-session"])
        self._assert_gone(self.session_a.id)
        self._assert_present(self.session_b.id)

    def test_user_path_still_refuses_another_users_session(self):
        response = self.controller.delete_session(
            self.session_b.id, _user(AccountType.USER, self.user_a)
        )

        self.assertFalse(response.success)
        self.assertEqual(response.error, "access_denied")
        self._assert_present(self.session_b.id)

    def test_user_path_deletes_own_session(self):
        response = self.controller.delete_session(
            self.session_a.id, _user(AccountType.USER, self.user_a)
        )

        self.assertTrue(response.success)
        self._assert_gone(self.session_a.id)


@pytest.fixture
def make_client():
    def _make(user, controller=None):
        app = FastAPI()
        app.include_router(chat_mod.build_router(SimpleNamespace(chat_controller=controller or Mock())))
        app.dependency_overrides[get_current_active_user] = lambda: user
        return TestClient(app, raise_server_exceptions=False)
    return _make


class TestAdminBulkDeleteSessionsRoute:

    def test_non_admin_is_forbidden(self, make_client):
        stub = Mock()
        client = make_client(_user(AccountType.USER, "regular"), controller=stub)
        resp = client.post("/api/chat/admin/sessions/bulk-delete", json={"session_ids": ["s1"]})
        assert resp.status_code == 403
        stub.admin_bulk_delete_sessions.assert_not_called()

    def test_admin_reaches_controller(self, make_client):
        stub = Mock()
        stub.admin_bulk_delete_sessions.return_value = APIResponse(
            success=True, data={"deleted_count": 1, "failed_count": 0, "failed_ids": []}
        )
        admin = _user()
        client = make_client(admin, controller=stub)
        resp = client.post("/api/chat/admin/sessions/bulk-delete", json={"session_ids": ["s1"]})
        assert resp.status_code == 200
        assert resp.json()["data"]["deleted_count"] == 1
        args = stub.admin_bulk_delete_sessions.call_args.args
        assert args[0] == ["s1"]
        assert args[1].id == admin.id

    def test_empty_id_list_is_rejected(self, make_client):
        stub = Mock()
        client = make_client(_user(), controller=stub)
        resp = client.post("/api/chat/admin/sessions/bulk-delete", json={"session_ids": []})
        assert resp.status_code == 422
        stub.admin_bulk_delete_sessions.assert_not_called()
