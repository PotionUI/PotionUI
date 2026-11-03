import asyncio
import logging
import threading
import time

import pytest

import src.features.recipes.executors.generation_smoke as generation_smoke
from src.features.recipes.executors.generation_smoke import GenerationSmokeExecutor
from src.features.recipes.executors.registry import RecipeExecutorRegistry
from src.features.recipes.records import RecipeRunStatus, RecipeStepStatus
from src.features.recipes.runner import RecipeRunner
from src.features.recipes.schema import Recipe, RecipeSmokeRef, RecipeStep
from src.platform.database.database import db as global_db
from tests.fixtures.db_template import copy_template_db


@pytest.fixture
def file_db(tmp_path):
    db_path = tmp_path / "recipe_cancel_smoke.db"
    copy_template_db(db_path)
    original_path = global_db.db_path
    global_db.db_path = db_path
    try:
        yield global_db
    finally:
        global_db.db_path = original_path


@pytest.fixture
def app_loop():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join(5)
    loop.close()


@pytest.fixture(autouse=True)
def _fake_fixture_form(monkeypatch):
    monkeypatch.setattr(generation_smoke, "build_fixture_form_data", lambda *a, **k: {"seed": 1})


class FakePresetTemplate:
    name = "MiniMax H3 Starter"
    modes = {"txt2video": object()}


class FakePresetLoader:
    def load_preset_by_id(self, preset_id):
        return FakePresetTemplate()


class FakeFileRepository:
    def get_generation_files(self, generation_id, **kwargs):
        return []


class FakeCatalog:
    def __init__(self, recipe):
        self.recipe = recipe

    def get_recipe(self, recipe_id, version=None):
        return self.recipe if recipe_id == self.recipe.id else None


class SamplingUntilCancelledOrchestrator:
    def __init__(self):
        self.cancelled = []
        self.stopped = threading.Event()

    async def start_generation(self, request, user_id, output_callback):
        asyncio.get_running_loop().create_task(self._sample(output_callback))
        return {"generation_id": "gen-1"}

    async def _sample(self, output_callback):
        from src.pipelines.outputs import Progress, ProgressGenerationOutput

        while not self.cancelled:
            await output_callback("gen-1", ProgressGenerationOutput(state="sampling", progress=Progress(current=1, max=8)))
            await asyncio.sleep(0.02)
        await output_callback("gen-1", ProgressGenerationOutput(state="sampling", progress=Progress(current=8, max=8)))
        await output_callback("gen-1", None)
        self.stopped.set()

    async def get_generation_status(self, generation_id):
        return {"status": "cancelled" if self.cancelled else "running"}

    async def cancel_generation(self, generation_id):
        self.cancelled.append(generation_id)
        return True


def _smoke_recipe():
    return Recipe(
        id="minimax-h3-starter",
        schema_version=1,
        version=1,
        name="MiniMax H3 Starter",
        engine="native",
        smoke=RecipeSmokeRef(preset_id="p1", mode="txt2video", timeout_seconds=600),
        steps=[
            RecipeStep(
                key="generation.smoke",
                kind="generation.smoke",
                title="Test generation",
                params={"preset_id": "p1", "mode": "txt2video"},
            )
        ],
    )


def _wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def _drive_thread(run_id):
    return next((t for t in threading.enumerate() if t.name == f"recipe-run-drive-{run_id}"), None)


def test_cancelling_a_run_during_its_smoke_cancels_the_generation_and_ends_the_drive(file_db, app_loop, caplog):
    caplog.set_level(logging.INFO)
    orchestrator = SamplingUntilCancelledOrchestrator()
    executor = GenerationSmokeExecutor(
        FakePresetLoader(), object(), orchestrator, FakeFileRepository(), object(), app_loop=lambda: app_loop
    )
    runner = RecipeRunner()
    runner.register_executor_registry(RecipeExecutorRegistry(FakeCatalog(_smoke_recipe()), {"generation.smoke": executor}))
    run = runner.create_run("minimax-h3-starter", created_by="owner-1")

    runner.drive_async(run.id)
    _wait_until(lambda: any(a.progress_current == 1 for a in runner.list_attempts(run.id)))
    drive = _drive_thread(run.id)
    runner.apply_action(run.id, "cancel")
    drive.join(5)

    assert not drive.is_alive()
    assert orchestrator.cancelled == ["gen-1"]
    assert orchestrator.stopped.wait(5)
    run = runner.get_run(run.id)
    assert run.status == RecipeRunStatus.CANCELLED
    assert run.current_step == "generation.smoke"
    attempts = runner.list_attempts(run.id)
    assert len(attempts) == 1
    assert attempts[0].status == RecipeStepStatus.CANCELLED
    assert attempts[0].finished_at is not None
    assert attempts[0].progress_current == 1
    assert attempts[0].error_code == "GENERATION_SMOKE_CANCELLED"
    assert attempts[0].safe_output["generation_id"] == "gen-1"
    assert run.id not in runner._driving
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == []
