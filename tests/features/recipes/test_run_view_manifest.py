"""`RecipeRunView.from_record`'s recipe-aware ordered step manifest.

`RecipeRunRepository.list_attempts` returns rows ordered by step_key (alphabetical),
which is not the recipe's execution order, and only steps that have an attempt
row show up at all. `from_record`'s `recipe_steps` param fixes both: it groups
attempts by step, orders them per the recipe, and synthesizes a "pending" entry
for any step that hasn't been attempted yet. Pure dataclass/pydantic tests, no
DB needed.
"""

from datetime import datetime, timezone

from src.features.recipes.records import RecipeRun, RecipeRunStatus, RecipeStepAttempt, RecipeStepStatus
from src.features.recipes.dto import RecipeRunView


def _run(**overrides) -> RecipeRun:
    defaults = dict(
        id="run-1",
        recipe_id="sdxl-starter",
        recipe_version=1,
        scope="instance",
        status=RecipeRunStatus.RUNNING,
        current_step="backend.ensure",
    )
    defaults.update(overrides)
    return RecipeRun(**defaults)


def _attempt(step_key: str, attempt: int, status: RecipeStepStatus, **overrides) -> RecipeStepAttempt:
    defaults = dict(
        id=f"{step_key}-{attempt}",
        run_id="run-1",
        step_key=step_key,
        attempt=attempt,
        status=status,
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return RecipeStepAttempt(**defaults)


def test_without_recipe_steps_falls_back_to_flat_attempts():
    run = _run()
    attempts = [_attempt("preset.ensure", 1, RecipeStepStatus.SUCCEEDED), _attempt("backend.ensure", 1, RecipeStepStatus.RUNNING)]

    view = RecipeRunView.from_record(run, attempts)

    assert view.steps == []
    assert [a.step_key for a in view.attempts] == ["preset.ensure", "backend.ensure"]


def test_orders_attempts_by_recipe_execution_order_not_alphabetical():
    run = _run()
    # 'preset.ensure' sorts before 'backend.ensure' alphabetically, but the
    # recipe runs backend.ensure first.
    attempts = [
        _attempt("preset.ensure", 1, RecipeStepStatus.SUCCEEDED),
        _attempt("backend.ensure", 1, RecipeStepStatus.SUCCEEDED),
    ]
    recipe_steps = [
        ("backend.ensure", "backend.ensure", "Prepare the backend"),
        ("preset.ensure", "preset.ensure", "Install the preset"),
    ]

    view = RecipeRunView.from_record(run, attempts, recipe_steps=recipe_steps)

    assert [a.step_key for a in view.attempts] == ["backend.ensure", "preset.ensure"]


def test_not_yet_started_steps_render_as_pending():
    run = _run()
    attempts = [_attempt("backend.ensure", 1, RecipeStepStatus.SUCCEEDED)]
    recipe_steps = [
        ("backend.ensure", "backend.ensure", "Prepare the backend"),
        ("preset.ensure", "preset.ensure", "Install the preset"),
        ("pipeline.render", "pipeline.render", "Validate the pipeline"),
    ]

    view = RecipeRunView.from_record(run, attempts, recipe_steps=recipe_steps)

    assert [s.step_key for s in view.steps] == ["backend.ensure", "preset.ensure", "pipeline.render"]
    assert [s.ordinal for s in view.steps] == [0, 1, 2]
    assert view.steps[0].status == "succeeded"
    assert view.steps[1].status == "pending"
    assert view.steps[1].attempts == []
    assert view.steps[2].status == "pending"


def test_retried_step_shows_latest_attempt_status_and_full_history():
    run = _run()
    attempts = [
        _attempt("backend.ensure", 1, RecipeStepStatus.FAILED),
        _attempt("backend.ensure", 2, RecipeStepStatus.SUCCEEDED),
    ]
    recipe_steps = [("backend.ensure", "backend.ensure", "Prepare the backend")]

    view = RecipeRunView.from_record(run, attempts, recipe_steps=recipe_steps)

    step = view.steps[0]
    assert step.status == "succeeded"  # latest attempt wins
    assert [a.attempt for a in step.attempts] == [1, 2]  # full history, in order


def test_attempts_for_a_step_the_recipe_no_longer_declares_are_not_dropped():
    run = _run()
    attempts = [
        _attempt("backend.ensure", 1, RecipeStepStatus.SUCCEEDED),
        _attempt("removed.step", 1, RecipeStepStatus.SUCCEEDED),
    ]
    recipe_steps = [("backend.ensure", "backend.ensure", "Prepare the backend")]

    view = RecipeRunView.from_record(run, attempts, recipe_steps=recipe_steps)

    assert [s.step_key for s in view.steps] == ["backend.ensure"]
    assert "removed.step" in [a.step_key for a in view.attempts]


def test_suggested_repair_promoted_out_of_safe_output_into_dedicated_field():
    attempt = _attempt(
        "backend.ensure",
        1,
        RecipeStepStatus.FAILED,
        safe_output={"engine": "native", "suggested_repair": "Open Administration -> Backends."},
        error_code="NO_BACKEND_FOR_ENGINE",
        safe_error_detail="No backend is configured for the 'native' engine yet.",
    )

    from src.features.recipes.dto import RecipeStepAttemptView

    view = RecipeStepAttemptView.from_record(attempt)

    assert view.safe_suggested_action == "Open Administration -> Backends."
    assert "suggested_repair" not in (view.safe_output or {})
    assert view.safe_output == {"engine": "native"}
    assert view.safe_error_detail == "No backend is configured for the 'native' engine yet."


def test_suggested_action_is_none_when_absent():
    attempt = _attempt("preset.ensure", 1, RecipeStepStatus.SUCCEEDED, safe_output={"preset_id": "x"})

    from src.features.recipes.dto import RecipeStepAttemptView

    view = RecipeStepAttemptView.from_record(attempt)

    assert view.safe_suggested_action is None
    assert view.safe_output == {"preset_id": "x"}
