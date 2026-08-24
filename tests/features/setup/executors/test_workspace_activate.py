"""`workspace.activate` against a fake `SetupRunRepository` surface."""

from src.features.setup.executors.base import StepContext
from src.features.setup.executors.workspace_activate import WorkspaceActivateExecutor
from src.features.setup.records import OnboardingStatus, SetupRun, SetupRunStatus, SetupStepAttempt, SetupStepStatus
from src.features.setup.recipe_schema import Recipe, RecipeStep


class FakeRunRepository:
    def __init__(self, attempts=()):
        self.attempts = list(attempts)
        self.upserts = []

    def list_attempts(self, run_id):
        return self.attempts

    def upsert_onboarding_state(self, user_id, *, status=None, first_generation_id=None, dismissed=False):
        self.upserts.append({"user_id": user_id, "status": status, "first_generation_id": first_generation_id})
        return object()


def _recipe(with_smoke=True):
    steps = [RecipeStep(key="workspace.activate", kind="workspace.activate", title="Finish", params={})]
    if with_smoke:
        steps.insert(0, RecipeStep(key="generation.smoke", kind="generation.smoke", title="Smoke", params={}))
    return Recipe(id="x", schema_version=1, version=1, name="X", engine="native", steps=steps)


def _context(recipe, owner="owner-1"):
    run = SetupRun(id="r1", recipe_id="x", recipe_version=1, scope="instance", status=SetupRunStatus.RUNNING, created_by=owner)
    step = recipe.get_step("workspace.activate")
    return StepContext(run=run, recipe=recipe, step=step)


def test_marks_onboarding_complete_and_threads_smoke_generation_id():
    recipe = _recipe(with_smoke=True)
    smoke_attempt = SetupStepAttempt(
        id="a1", run_id="r1", step_key="generation.smoke", attempt=1,
        status=SetupStepStatus.SUCCEEDED, safe_output={"generation_id": "gen-42"},
    )
    repo = FakeRunRepository(attempts=[smoke_attempt])
    executor = WorkspaceActivateExecutor(repo)

    result = executor.execute(_context(recipe))

    assert result.success is True
    assert repo.upserts[0]["status"] == OnboardingStatus.COMPLETED
    assert repo.upserts[0]["first_generation_id"] == "gen-42"


def test_no_owner_fails_clearly():
    recipe = _recipe(with_smoke=False)
    executor = WorkspaceActivateExecutor(FakeRunRepository())

    result = executor.execute(_context(recipe, owner=None))

    assert result.success is False
    assert result.error_code == "OWNER_NOT_FOUND"


def test_no_smoke_step_still_completes_without_generation_id():
    recipe = _recipe(with_smoke=False)
    repo = FakeRunRepository()
    executor = WorkspaceActivateExecutor(repo)

    result = executor.execute(_context(recipe))

    assert result.success is True
    assert repo.upserts[0]["first_generation_id"] is None
