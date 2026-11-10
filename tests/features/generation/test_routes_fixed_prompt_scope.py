from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.generation.applied_prompt_repository import GenerationAppliedPromptRepository
from src.features.generation.fixed_prompt import apply_fixed_prompt
from src.features.generation.handlers.param_handler import ParamGenerationOutputHandler
from src.features.generation.history_facade import GenerationHistoryFacade
from src.features.generation.output_broadcaster import GenerationOutputBroadcaster
from src.features.generation.records import Generation
from src.features.generation.repository import GenerationRepository
from src.features.generation.routes import GenerationController, build_admin_router, build_router
from src.features.generation.run_report_recorder import RunReportRecorder
from src.features.generation.run_report_repository import GenerationRunReportRepository
from src.features.generation.status_tracker import GenerationStatusTracker
from src.pipelines.outputs import ParamGenerationOutput
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User
from src.platform.util.ids import generate_ulid
from src.platform.websocket.connection_hub import ConnectionHub
from tests.fixtures.persistence_base import PersistenceTestBase

SECRETS = ("kodachrome", "warm grain", "cartoonish")
VISIBLE = [{"positive": "a red fox", "negative": "blurry"}, {"positive": "a grey wolf", "negative": "blurry"}]
PARTS = [("kodachrome still,", "warm grain", "cartoonish")] * 2


def _user(user_id, account_type):
    return User(
        id=user_id,
        username=user_id,
        email=f"{user_id}@example.com",
        password_hash="$2b$12$x",
        account_type=account_type,
        created_at=datetime.utcnow(),
        last_login=None,
    )


class TestFixedPromptScopeOnGenerationRoutes(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.generation_repo = GenerationRepository()
        self.applied_repo = GenerationAppliedPromptRepository()
        recorder = RunReportRecorder(GenerationRunReportRepository())
        tracker = GenerationStatusTracker()
        self.controller = GenerationController(
            Mock(status_tracker=tracker),
            GenerationHistoryFacade(
                generation_repo=self.generation_repo,
                file_service=Mock(),
                plugin_registry=Mock(),
                run_report_repository=Mock(),
            ),
            Mock(),
            recorder,
            None,
            GenerationOutputBroadcaster(ConnectionHub(), tracker, recorder),
            self.applied_repo,
        )
        self.owner = _user(self.create_test_user("owner-1", "owner", "owner@example.com"), AccountType.USER)
        self.admin = _user(self.create_test_user("admin-1", "admin", "admin@example.com"), AccountType.ADMIN)
        self.run = apply_fixed_prompt(VISIBLE, PARTS)
        self.users_generation = self._generation(self.owner.id)
        self.admins_generation = self._generation(self.admin.id)
        self.plain_generation = self._generation(self.admin.id, fixed=False)

    def _generation(self, user_id, fixed=True):
        generation = self.generation_repo.create(Generation(
            id=generate_ulid(), preset_id="film", form_data={"prompt": "a red fox", "quantity": 2, "seed": 5},
            user_id=user_id, status="completed",
        ))
        run = self.run if fixed else apply_fixed_prompt(VISIBLE, [("", "", "")] * 2)
        positive = ParamGenerationOutput(name="positive_prompt", values=[p["positive"] for p in run.applied])
        negative = ParamGenerationOutput(name="negative_prompt", values=[p["negative"] for p in run.applied])
        for output in (positive, negative):
            run.mask(output)
            ParamGenerationOutputHandler(generation.id, user_id).handle(output)
        if fixed:
            self.applied_repo.save(generation.id, run.records())
        return generation

    def _client(self, user):
        container = SimpleNamespace(_generation_controller=self.controller, media_access=Mock())
        app = FastAPI()
        app.include_router(build_router(container))
        app.include_router(build_admin_router(container))
        app.dependency_overrides[get_current_active_user] = lambda: user
        return TestClient(app)

    def _assert_user_version(self, response):
        for secret in SECRETS:
            self.assertNotIn(secret, response.text)

    def test_a_user_sees_their_own_prompt_in_history_details(self):
        response = self._client(self.owner).get(f"/api/generations/history/{self.users_generation.id}")

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["parameters"]["positive_prompt"], ["a red fox", "a grey wolf"])
        self.assertEqual(data["parameters"]["negative_prompt"], ["blurry", "blurry"])
        self.assertNotIn("fixed_prompt_applied", data)
        self._assert_user_version(response)

    def test_a_user_sees_their_own_prompt_in_parameters_by_index(self):
        client = self._client(self.owner)

        for index, pair in enumerate(VISIBLE):
            response = client.get(f"/api/generations/{self.users_generation.id}/params/{index}")

            self.assertEqual(response.status_code, 200)
            data = response.json()["data"]
            self.assertEqual(data["parameters"]["positive_prompt"], pair["positive"])
            self.assertNotIn("fixed_prompt_applied", data)
            self._assert_user_version(response)

    def test_a_user_cannot_open_the_admin_generation_view(self):
        response = self._client(self.owner).get(f"/api/admin/generations/{self.users_generation.id}")

        self.assertEqual(response.status_code, 403)
        self._assert_user_version(response)

    def test_an_admin_sees_the_applied_prompts_in_history_details(self):
        response = self._client(self.admin).get(f"/api/generations/history/{self.admins_generation.id}")

        data = response.json()["data"]
        self.assertEqual(data["parameters"]["positive_prompt"], [p["positive"] for p in self.run.applied])
        self.assertEqual(data["parameters"]["negative_prompt"], [p["negative"] for p in self.run.applied])
        self.assertIs(data["fixed_prompt_applied"], True)

    def test_an_admin_sees_the_applied_prompt_by_index(self):
        response = self._client(self.admin).get(f"/api/generations/{self.admins_generation.id}/params/1")

        data = response.json()["data"]
        self.assertEqual(data["parameters"]["positive_prompt"], self.run.applied[1]["positive"])
        self.assertEqual(data["parameters"]["negative_prompt"], self.run.applied[1]["negative"])
        self.assertIs(data["fixed_prompt_applied"], True)

    def test_an_admin_sees_the_applied_prompts_of_a_users_generation(self):
        response = self._client(self.admin).get(f"/api/admin/generations/{self.users_generation.id}")

        generation = response.json()["data"]["generation"]
        self.assertEqual(generation["parameters"]["positive_prompt"], [p["positive"] for p in self.run.applied])
        self.assertIs(generation["fixed_prompt_applied"], True)

    def test_a_generation_without_fixed_text_is_unchanged_for_admins(self):
        client = self._client(self.admin)

        details = client.get(f"/api/generations/history/{self.plain_generation.id}").json()["data"]
        admin_view = client.get(f"/api/admin/generations/{self.plain_generation.id}").json()["data"]["generation"]

        self.assertEqual(details["parameters"]["positive_prompt"], ["a red fox", "a grey wolf"])
        self.assertNotIn("fixed_prompt_applied", details)
        self.assertNotIn("fixed_prompt_applied", admin_view)
        self.assertNotIn("parameters", admin_view)
