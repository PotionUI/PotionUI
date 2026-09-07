"""User-scoped run-report read (`GET /api/generations/{id}/run-report`).

The admin detail endpoint returns the same report bundled with the whole
generation record; this one exists so the history detail can show a run's
artifacts without admin rights, and it must be scoped to the caller's own
generations.

``PersistenceTestBase`` is a plain ``unittest.TestCase``, and pytest never
awaits an ``async def test_*`` on a bare ``TestCase`` (see
``tests/architecture/test_async_unittest_testcase.py``), so the tests below are
synchronous and drive the controller's async methods with ``asyncio.run``.
"""

import asyncio
from datetime import datetime
from unittest.mock import Mock

from fastapi import HTTPException

from tests.fixtures.persistence_base import PersistenceTestBase
from src.platform.util.ids import generate_ulid
from src.features.generation.records import Generation
from src.features.generation.repository import GenerationRepository
from src.features.generation.history_facade import GenerationHistoryFacade
from src.features.generation.run_report_repository import GenerationRunReportRepository
from src.features.generation.run_report_recorder import RunReportRecorder
from src.features.generation.routes import GenerationController
from src.platform.security.user import User, AccountType


def _user(user_id: str, account_type=AccountType.USER) -> User:
    return User(
        id=user_id,
        username=user_id,
        email=f"{user_id}@example.com",
        password_hash="$2b$12$x",
        account_type=account_type,
        created_at=datetime.utcnow(),
        last_login=None,
    )


class TestUserRunReportRead(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.generation_repo = GenerationRepository()
        self.run_report_recorder = RunReportRecorder(GenerationRunReportRepository())
        self.controller = GenerationController(
            Mock(),
            GenerationHistoryFacade(
                generation_repo=self.generation_repo,
                file_service=Mock(),
                plugin_registry=Mock(),
                run_report_repository=Mock(),
            ),
            Mock(),
            self.run_report_recorder,
        )

        self.owner_id = self.create_test_user("user-a", "usera", "usera@example.com")
        self.other_id = self.create_test_user("user-b", "userb", "userb@example.com")
        self.owner = _user(self.owner_id)
        self.other = _user(self.other_id)

        self.generation = self._create_generation(self.owner_id)

    def _create_generation(self, user_id):
        return self.generation_repo.create(Generation(
            id=generate_ulid(),
            preset_id="preset-1",
            form_data={"prompt": "a cat"},
            user_id=user_id,
            status="completed",
        ))

    def _record_seed_artifact(self):
        self.run_report_recorder.record_output(self.generation.id, {
            "type": "pipe_artifact",
            "pipe_id": "generator",
            "artifact_type": "seed",
            "artifact_data": {"seed": 424242},
        })
        self.run_report_recorder.flush(self.generation.id, terminal_status="completed")

    def test_owner_reads_the_recorded_artifacts(self):
        self._record_seed_artifact()

        response = asyncio.run(self.controller.get_run_report(self.generation.id, self.owner))

        report = response.data['run_report']
        self.assertEqual(
            [a['artifact_type'] for a in report['artifacts']],
            ['seed'],
        )
        self.assertEqual(report['artifacts'][0]['artifact_data']['seed'], 424242)

    def test_owner_gets_the_prompt_template_the_run_was_rendered_from(self):
        self._record_seed_artifact()

        response = asyncio.run(self.controller.get_run_report(self.generation.id, self.owner))

        self.assertEqual(response.data['run_report']['prompt_template'], "a cat")

    def test_report_is_null_when_the_run_never_flushed_one(self):
        response = asyncio.run(self.controller.get_run_report(self.generation.id, self.owner))

        self.assertIsNone(response.data['run_report'])

    def test_another_users_generation_404s_rather_than_403s(self):
        self._record_seed_artifact()

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(self.controller.get_run_report(self.generation.id, self.other))

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.detail["error"], "generation_not_found")

    def test_unknown_generation_404s(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(self.controller.get_run_report("no-such-generation", self.owner))

        self.assertEqual(ctx.exception.status_code, 404)

    def test_admin_may_read_another_users_report(self):
        self._record_seed_artifact()
        admin = _user("admin-1", AccountType.ADMIN)

        response = asyncio.run(self.controller.get_run_report(self.generation.id, admin))

        self.assertEqual(len(response.data['run_report']['artifacts']), 1)
