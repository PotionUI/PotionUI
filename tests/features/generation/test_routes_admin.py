"""Admin generation endpoints (`/api/admin/generations`).

Controller-level tests exercise the real GenerationHistoryFacade +
GenerationRepository + RunReportRecorder/Repository against a scratch
database (global visibility, the user_id filter, has_run_report, the
run-report detail payload). Route-level tests only check the admin gate,
which doesn't need real data.

``PersistenceTestBase`` is a plain ``unittest.TestCase`` (not
``IsolatedAsyncioTestCase``), and pytest never awaits an ``async def test_*``
on a bare ``TestCase`` - it silently reports the test passed without running
its body (see ``tests/architecture/test_async_unittest_testcase.py``). Test
methods below are therefore synchronous and drive the controller's async
methods with ``asyncio.run``.
"""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tests.fixtures.persistence_base import PersistenceTestBase
from src.platform.http.base_controller import APIResponse
from src.platform.util.ids import generate_ulid
from src.features.generation.records import Generation
from src.features.generation.repository import GenerationRepository
from src.features.generation.history_facade import GenerationHistoryFacade
from src.features.generation.run_report_repository import GenerationRunReportRepository
from src.features.generation.run_report_recorder import RunReportRecorder
from src.features.generation.routes import GenerationController, build_admin_router
from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from src.platform.security.user import User, AccountType

import src.features.generation.run_report_repository as run_report_repository_module


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


class TestAdminGenerationsController(PersistenceTestBase):
    """Real-database checks for the admin controller methods."""

    def setUp(self):
        super().setUp()
        # PersistenceTestBase redirects generation/file repositories; the run
        # report repository is this feature's own addition and isn't on its
        # whitelist, so redirect it here.
        run_report_repository_module.db = self.db

        self.generation_repo = GenerationRepository()
        self.history_facade = GenerationHistoryFacade(
            generation_repo=self.generation_repo,
            file_service=Mock(),
            plugin_registry=Mock(),
        )
        self.run_report_repository = GenerationRunReportRepository()
        self.run_report_recorder = RunReportRecorder(self.run_report_repository)
        self.controller = GenerationController(
            Mock(),
            self.history_facade,
            Mock(),
            self.run_report_recorder,
        )

        self.user_a = self.create_test_user("user-a", "usera", "usera@example.com")
        self.user_b = self.create_test_user("user-b", "userb", "userb@example.com")

        self.gen_a = self._create_generation(self.user_a)
        self.gen_b = self._create_generation(self.user_b)

    def tearDown(self):
        from src.platform.database.database import db as REAL_DB
        run_report_repository_module.db = REAL_DB
        super().tearDown()

    def _create_generation(self, user_id, prompt="a cat"):
        generation = Generation(
            id=generate_ulid(),
            preset_id="preset-1",
            form_data={"prompt": prompt},
            user_id=user_id,
            status="completed",
        )
        return self.generation_repo.create(generation)

    def test_list_is_global_across_users_by_default(self):
        response = asyncio.run(self.controller.admin_list_generations())
        ids = {g['id'] for g in response.data['generations']}
        self.assertIn(self.gen_a.id, ids)
        self.assertIn(self.gen_b.id, ids)

    def test_list_user_id_filters_to_one_user(self):
        response = asyncio.run(self.controller.admin_list_generations(user_id=self.user_a))
        ids = {g['id'] for g in response.data['generations']}
        self.assertIn(self.gen_a.id, ids)
        self.assertNotIn(self.gen_b.id, ids)

    def test_list_rows_carry_owner_and_has_run_report(self):
        self.run_report_recorder.flush(self.gen_a.id, terminal_status="completed")

        response = asyncio.run(self.controller.admin_list_generations())
        by_id = {g['id']: g for g in response.data['generations']}

        self.assertEqual(by_id[self.gen_a.id]['user_id'], self.user_a)
        self.assertTrue(by_id[self.gen_a.id]['has_run_report'])
        self.assertFalse(by_id[self.gen_b.id]['has_run_report'])

    def test_get_generation_returns_report_and_prompt_template(self):
        self.run_report_recorder.record_output(self.gen_a.id, {
            "type": "pipe_artifact", "pipe_id": 0, "pipe_name": "generator",
            "artifact_type": "seed", "artifact_data": {"seed": 7},
        })
        self.run_report_recorder.flush(self.gen_a.id, terminal_status="completed")

        response = asyncio.run(self.controller.admin_get_generation(self.gen_a.id))

        self.assertEqual(response.data['generation']['id'], self.gen_a.id)
        report = response.data['run_report']
        self.assertIsNotNone(report)
        self.assertEqual(report['prompt_template'], "a cat")
        self.assertEqual(len(report['artifacts']), 1)

    def test_get_generation_report_is_null_when_never_flushed(self):
        response = asyncio.run(self.controller.admin_get_generation(self.gen_b.id))
        self.assertIsNone(response.data['run_report'])

    def test_get_generation_404s_on_unknown_id(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(self.controller.admin_get_generation("no-such-generation"))
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.detail["error"], "generation_not_found")


class TestAdminGenerationsRouteGating:
    """Route-level admin gate - doesn't need real data."""

    @pytest.fixture
    def app(self):
        controller = GenerationController(Mock(), Mock(), Mock(), Mock(spec=RunReportRecorder))
        controller.admin_list_generations = AsyncMock(
            return_value=APIResponse(success=True, data={"generations": [], "total": 0})
        )
        container = SimpleNamespace(_generation_controller=controller)
        router = build_admin_router(container)
        app = FastAPI()
        app.include_router(router)
        return app

    def test_non_admin_is_forbidden(self, app):
        app.dependency_overrides[get_current_active_user] = lambda: _user(AccountType.USER, "regular")
        client = TestClient(app)
        resp = client.get("/api/admin/generations")
        assert resp.status_code == 403

    def test_admin_passes_the_gate(self, app):
        app.dependency_overrides[get_current_admin_user] = lambda: _user(AccountType.ADMIN)
        client = TestClient(app)
        resp = client.get("/api/admin/generations")
        assert resp.status_code == 200
