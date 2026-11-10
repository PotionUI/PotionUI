from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.generation.applied_prompt_repository import GenerationAppliedPromptRepository
from src.features.generation.fixed_prompt import apply_fixed_prompt
from src.features.generation.history_facade import GenerationHistoryFacade
from src.features.generation.output_broadcaster import GenerationOutputBroadcaster
from src.features.generation.parameter_repository import GenerationParameterRepository
from src.features.generation.repository import GenerationRepository
from src.features.generation.routes import GenerationController, build_admin_router, build_router
from src.features.generation.run_report_recorder import RunReportRecorder
from src.features.generation.run_report_repository import GenerationRunReportRepository
from src.features.generation.status_tracker import GenerationStatusTracker
from src.pipelines.outputs import ParamGenerationOutput
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User
from src.platform.websocket.connection_hub import ConnectionHub
from tests.features.generation.test_prompt_batch_split import COUNT, PER_PROMPT, SplitFixture

VISIBLE = [{"positive": f"p{i}", "negative": ""} for i in range(PER_PROMPT * COUNT)]
PARTS = [(f"FIXED{i}", "", "cartoonish") for i in range(PER_PROMPT * COUNT)]
SECRETS = ("FIXED", "cartoonish")


class TestSplitChildrenKeepTheirAppliedPrompts(SplitFixture):
    def setUp(self):
        super().setUp()
        self.applied_repo = GenerationAppliedPromptRepository()
        self.run = apply_fixed_prompt(VISIBLE, PARTS)
        self.applied_repo.save(self.generation_id, self.run.records())
        parameters = GenerationParameterRepository()
        for name, channel in (("positive_prompt", "positive"), ("negative_prompt", "negative")):
            output = ParamGenerationOutput(name=name, values=[pair[channel] for pair in self.run.applied])
            self.run.mask(output)
            parameters.create_batch(self.generation_id, output.name, output.values)
        self.new_ids = self.split()

    def _client(self, account_type):
        tracker = GenerationStatusTracker()
        recorder = RunReportRecorder(GenerationRunReportRepository())
        controller = GenerationController(
            Mock(status_tracker=tracker),
            GenerationHistoryFacade(
                generation_repo=GenerationRepository(), file_service=Mock(), plugin_registry=Mock(),
                run_report_repository=Mock(),
            ),
            Mock(),
            recorder,
            None,
            GenerationOutputBroadcaster(ConnectionHub(), tracker, recorder),
            self.applied_repo,
        )
        viewer = User(
            id=self.user_id, username="viewer", email="viewer@example.com", password_hash="$2b$12$x",
            account_type=account_type, created_at=datetime.utcnow(), last_login=None,
        )
        app = FastAPI()
        container = SimpleNamespace(_generation_controller=controller, media_access=Mock())
        app.include_router(build_router(container))
        app.include_router(build_admin_router(container))
        app.dependency_overrides[get_current_active_user] = lambda: viewer
        return TestClient(app)

    def _own_slice(self, prompt_index):
        return self.run.records()[prompt_index * PER_PROMPT:(prompt_index + 1) * PER_PROMPT]

    def test_each_entry_keeps_only_its_own_images_applied_prompts(self):
        self.assertEqual(self.applied_repo.get(self.generation_id), self._own_slice(0))
        for prompt_index, child in enumerate(self.new_ids, start=1):
            self.assertEqual(self.applied_repo.get(child), self._own_slice(prompt_index))

    def test_an_admin_sees_the_applied_text_on_a_split_entry(self):
        client = self._client(AccountType.ADMIN)
        child = self.new_ids[0]
        expected = [entry["positive"] for entry in self._own_slice(1)]

        details = client.get(f"/api/generations/history/{child}").json()["data"]
        by_index = client.get(f"/api/generations/{child}/params/1").json()["data"]
        admin_view = client.get(f"/api/admin/generations/{child}").json()["data"]["generation"]

        self.assertEqual(details["parameters"]["positive_prompt"], expected)
        self.assertIs(details["fixed_prompt_applied"], True)
        self.assertEqual(by_index["parameters"]["positive_prompt"], expected[1])
        self.assertEqual(admin_view["parameters"]["positive_prompt"], expected)

    def test_a_regular_user_never_sees_the_fixed_text_on_any_split_entry(self):
        client = self._client(AccountType.USER)

        for prompt_index, generation_id in enumerate([self.generation_id, *self.new_ids]):
            details = client.get(f"/api/generations/history/{generation_id}")
            by_index = [client.get(f"/api/generations/{generation_id}/params/{i}") for i in range(PER_PROMPT)]

            own = [pair["positive"] for pair in VISIBLE[prompt_index * PER_PROMPT:(prompt_index + 1) * PER_PROMPT]]
            self.assertEqual(details.json()["data"]["parameters"]["positive_prompt"], own)
            for response in (details, *by_index):
                self.assertNotIn("fixed_prompt_applied", response.json()["data"])
                for secret in SECRETS:
                    self.assertNotIn(secret, response.text)


class TestSplitWithoutFixedPrompt(SplitFixture):
    def test_a_split_without_an_applied_record_writes_none(self):
        new_ids = self.split()

        self.assertEqual(len(new_ids), COUNT - 1)
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM generation_applied_prompts")
            self.assertEqual(cursor.fetchone()[0], 0)
