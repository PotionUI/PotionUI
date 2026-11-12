from tests.fixtures.kits import inert_kit_expander
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.forms.binding import BoundForm
from src.features.generation.applied_prompt_repository import GenerationAppliedPromptRepository
from src.features.generation.history_facade import GenerationHistoryFacade
from src.features.generation.orchestrator import GenerationOrchestrator
from src.features.generation.output_broadcaster import GenerationOutputBroadcaster
from src.features.generation.output_processor import OutputProcessor
from src.features.generation.pipeline_builder import BuiltPipeline
from src.features.generation.repository import GenerationRepository
from src.features.generation.routes import GenerationController, build_admin_router, build_router
from src.features.generation.run_report_recorder import RunReportRecorder
from src.features.generation.run_report_repository import GenerationRunReportRepository
from src.features.generation.status_tracker import GenerationState
from src.features.presets import operations
from src.features.presets.exceptions import PresetHasMirrorsError
from src.features.presets.fixed_prompt import InvalidFixedPrompt, read_fixed_prompt, write_fixed_prompt
from src.features.presets.fixed_prompt_repository import PresetFixedPromptRepository
from src.pipelines.outputs import ParamGenerationOutput, RenderedPromptGenerationOutput
from src.platform.http.errors import NotFoundError
from src.platform.security.current_user import get_current_active_user
from src.platform.websocket.connection_hub import ConnectionHub
from tests.features.presets.mirror_world import build_world

FILM = {"prefix": "kodachrome still,", "suffix": "warm grain", "negative": "cartoonish", "modes": None}
SECRETS = ("kodachrome", "warm grain", "cartoonish")
USER_PROMPT = {"positive": "a red fox", "negative": "blurry"}
APPLIED = {"positive": "kodachrome still, a red fox warm grain", "negative": "blurry, cartoonish"}


@pytest.fixture(autouse=True)
def bind_form_passthrough():
    def passthrough(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None):
        return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or "custom", coercions=[], stripped=[])

    with patch("src.features.generation.orchestrator.bind_form", side_effect=passthrough):
        yield


class Studio:
    def __init__(self, world):
        self.world = world
        self.fixed = PresetFixedPromptRepository()
        self.applied = GenerationAppliedPromptRepository()
        backend = Mock(backend_id="b1", engine="native", supports_prompt_batch=False)
        backend.name = "Local"
        backend.start_generation = AsyncMock()
        registry = Mock()
        registry.select_backend_for_generation = Mock(return_value=backend)
        registry.get_backend = Mock(return_value=backend)
        self.builder = Mock()
        self.builder.build_pipeline = Mock(side_effect=lambda **kwargs: BuiltPipeline(
            generation_id=kwargs.get("generation_id", "g"), preset_id="p",
            preset_template=Mock(version="1"), pipes=[],
        ))
        settings = Mock()
        settings.get_setting = Mock(return_value="/out")
        self.orchestrator = GenerationOrchestrator(
            pipeline_builder=self.builder,
            backend_registry=registry,
            connection_hub=Mock(),
            settings=settings,
            output_processor=OutputProcessor(Mock()),
            preset_template_loader=world.loader,
            fixed_prompt_repository=self.fixed,
            applied_prompt_repository=self.applied, kit_expander=inert_kit_expander(),
        )
        recorder = RunReportRecorder(GenerationRunReportRepository())
        tracker = self.orchestrator.status_tracker
        self.broadcaster = GenerationOutputBroadcaster(ConnectionHub(), tracker, recorder)
        self.controller = GenerationController(
            Mock(status_tracker=tracker),
            GenerationHistoryFacade(
                generation_repo=GenerationRepository(),
                file_service=Mock(),
                plugin_registry=Mock(),
                run_report_repository=Mock(),
                preset_name_resolver=world.names(),
            ),
            Mock(),
            recorder,
            None,
            self.broadcaster,
            self.applied,
        )

    def mirror(self, offered_modes=("txt2img",)):
        payload = {
            "name": "Film Covers", "description": None, "cover": None,
            "offered_modes": list(offered_modes) if offered_modes else None, "first_mode": None,
        }
        return operations.create_mirror(self.world.collaborators, "krea2", payload)["preset_id"]

    def save_fixed(self, preset_id, **changes):
        return write_fixed_prompt(self.world.loader, self.fixed, preset_id, {**FILM, **changes})

    def generate(self, preset_id, generation_id, mode="txt2img"):
        request = SimpleNamespace(
            preset_id=preset_id, form_data={"prompt": USER_PROMPT["positive"], "quantity": 1, "seed": 5},
            prompts=[dict(USER_PROMPT)], variables=None, prompt_state=None, mode=mode, tag_ids=None,
            source_prompt_id=None, segments=None, collection_ids=None, batch_prompts=False, form_name=None,
            tab_id=None, idempotency_key=None, grid_id=None,
        )

        async def run():
            with patch("src.features.generation.orchestrator.generate_ulid", return_value=generation_id):
                await self.orchestrator.start_generation(request, "ann", output_callback=self.broadcaster.handle_output)
            prompts = self.builder.build_pipeline.call_args.kwargs["prompts"]
            outputs = [
                ParamGenerationOutput(name="positive_prompt", values=[pair["positive"] for pair in prompts]),
                ParamGenerationOutput(name="negative_prompt", values=[pair["negative"] for pair in prompts]),
                RenderedPromptGenerationOutput(index=0, positive=prompts[0]["positive"], negative=prompts[0]["negative"]),
            ]
            for output in outputs:
                await self.orchestrator._handle_generation_output(generation_id, output, "native", self.broadcaster.handle_output)
            await self.orchestrator._handle_generation_output(generation_id, None, "native", self.broadcaster.handle_output)
            return prompts

        return asyncio.run(run())

    def client(self, user):
        container = SimpleNamespace(_generation_controller=self.controller, media_access=Mock())
        app = FastAPI()
        app.include_router(build_router(container))
        app.include_router(build_admin_router(container))
        app.dependency_overrides[get_current_active_user] = lambda: user
        return TestClient(app)

    def details(self, user, generation_id):
        return self.client(user).get(f"/api/generations/history/{generation_id}").json()["data"]

    def admin_view(self, generation_id):
        return self.client(self.world.admin).get(f"/api/admin/generations/{generation_id}").json()["data"]["generation"]


@pytest.fixture
def studio(mock_db, tmp_path):
    world = build_world(mock_db, tmp_path)
    world.install("krea2")
    return Studio(world)


def stored_parameters(mock_db):
    with mock_db.get_cursor() as cursor:
        cursor.execute("SELECT parameter_value FROM generation_parameters")
        return json.dumps([row[0] for row in cursor.fetchall()])


def test_a_mirror_runs_its_fixed_text_and_only_admins_see_it(studio, mock_db):
    mirror_id = studio.mirror()
    studio.save_fixed(mirror_id)

    ran = studio.generate(mirror_id, "g-mirror")

    assert ran == [APPLIED]
    assert studio.applied.get("g-mirror") == [APPLIED]
    assert all(secret not in stored_parameters(mock_db) for secret in SECRETS)
    owner = studio.details(studio.world.regular, "g-mirror")
    assert owner["preset_id"] == mirror_id
    assert owner["parameters"]["positive_prompt"] == [USER_PROMPT["positive"]]
    assert "fixed_prompt_applied" not in owner
    admin = studio.admin_view("g-mirror")
    assert admin["parameters"]["positive_prompt"] == [APPLIED["positive"]]
    assert admin["parameters"]["negative_prompt"] == [APPLIED["negative"]]
    assert admin["fixed_prompt_applied"] is True


def test_the_source_of_a_mirror_with_fixed_text_runs_the_users_prompt_alone(studio):
    mirror_id = studio.mirror()
    studio.save_fixed(mirror_id)

    ran = studio.generate("krea2", "g-source")

    assert ran == [USER_PROMPT]
    assert studio.applied.get("g-source") is None
    assert read_fixed_prompt(studio.world.loader, studio.fixed, "krea2").is_empty
    assert "fixed_prompt_applied" not in studio.admin_view("g-source")


def test_a_new_mirror_does_not_take_the_fixed_text_of_its_source(studio):
    studio.save_fixed("krea2")

    mirror_id = studio.mirror()

    assert read_fixed_prompt(studio.world.loader, studio.fixed, mirror_id).is_empty
    assert studio.generate(mirror_id, "g-mirror") == [USER_PROMPT]


def test_fixed_text_for_a_mode_the_mirror_no_longer_offers_is_skipped_without_error(studio):
    mirror_id = studio.mirror(offered_modes=None)
    studio.save_fixed(mirror_id, modes=["edit"])
    operations.set_mirror_modes(studio.world.collaborators, mirror_id, {"offered": ["txt2img"], "first": None})

    ran = studio.generate(mirror_id, "g-mirror")

    assert ran == [USER_PROMPT]
    assert studio.applied.get("g-mirror") is None
    assert studio.orchestrator.status_tracker.get("g-mirror").state != GenerationState.FAILED
    with pytest.raises(InvalidFixedPrompt):
        studio.save_fixed(mirror_id, modes=["edit"])


def test_removing_the_source_of_a_mirror_with_fixed_text_is_refused_and_loses_nothing(studio):
    mirror_id = studio.mirror()
    saved = studio.save_fixed(mirror_id)

    with pytest.raises(PresetHasMirrorsError):
        operations.uninstall_preset(studio.world.collaborators, "krea2", studio.world.admin)

    assert studio.world.repo.is_preset_installed("krea2")
    assert studio.fixed.get(mirror_id) == saved
    assert studio.generate(mirror_id, "g-after") == [APPLIED]


def test_deleting_a_mirror_takes_its_fixed_text_and_keeps_old_generations_readable(studio):
    mirror_id = studio.mirror()
    studio.save_fixed(mirror_id)
    studio.generate(mirror_id, "g-old")

    result = operations.uninstall_preset(studio.world.collaborators, mirror_id, studio.world.admin)

    assert result["tombstone"] == {"preset_id": mirror_id, "name": "Film Covers"}
    assert studio.fixed.get(mirror_id) is None
    with pytest.raises(NotFoundError):
        read_fixed_prompt(studio.world.loader, studio.fixed, mirror_id)
    admin = studio.admin_view("g-old")
    assert admin["parameters"]["positive_prompt"] == [APPLIED["positive"]]
    assert admin["fixed_prompt_applied"] is True
    owner = studio.details(studio.world.regular, "g-old")
    assert owner["parameters"]["positive_prompt"] == [USER_PROMPT["positive"]]
    assert studio.world.names().resolve(mirror_id) == "Film Covers"
