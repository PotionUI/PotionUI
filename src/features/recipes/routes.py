"""Admin-facing recipe endpoints: browse the catalog, inspect one recipe, and
run it.

Everything here is admin-only - a recipe enables plugins, downloads model
files, installs presets and generates. The first-run wizard drives the same
recipes through `/api/setup/...` (see `src.features.setup.routes`); the
difference is the run's `mode`: an onboarding run executes every step, an
admin run skips the ones a recipe marks `onboarding_only`.
"""

from typing import TYPE_CHECKING, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.features.recipes.dto import (
    RecipeDetail,
    RecipeRunActionRequest,
    RecipeRunView,
    RecipeSummary,
    StartRecipeRunRequest,
    StepKindView,
)
from src.features.recipes.records import MODE_ADMIN
from src.features.recipes.runner import (
    ActiveRecipeRunExists,
    IllegalRecipeRunTransition,
    RecipeRunError,
    RecipeRunner,
    RecipeRunNotFound,
)
from src.features.setup.readiness import ReadinessReport, build_readiness_aggregator
from src.platform.security.current_user import get_current_admin_user
from src.platform.security.user import User

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer

#: `grant_consent` targets one step and has its own route, so it is not one of
#: the run-wide actions this router dispatches.
ADMIN_ACTIONS = ("pause", "resume", "cancel", "retry_step")


def run_view(runner: RecipeRunner, recipe_catalog, run) -> RecipeRunView:
    """Build the wire view for `run`, enriched with the ordered step manifest
    its recipe declares for the run's own mode (an admin run's plan omits the
    onboarding-only steps, so the UI never renders steps it will never run)."""
    recipe_steps = None
    if recipe_catalog is not None:
        recipe = recipe_catalog.get_recipe(run.recipe_id, run.recipe_version)
        if recipe is not None:
            recipe_steps = [
                (s.key, s.kind, s.title) for s in recipe.steps_for_mode(run.mode)
            ]
    return RecipeRunView.from_record(run, runner.list_attempts(run.id), recipe_steps=recipe_steps)


def build_router(container: "AppContainer") -> APIRouter:
    router = APIRouter(prefix="/api/recipes", tags=["Recipes"])

    def _runner() -> RecipeRunner:
        return container.recipe_runner

    def _catalog():
        return container.recipe_catalog

    def _preset_name(recipe) -> Optional[str]:
        loader = getattr(container, "preset_template_loader", None)
        if loader is None or not recipe.presets:
            return None
        template = loader.load_preset_by_id(recipe.presets[0].preset_id)
        return template.name if template is not None else None

    def _require_recipe(recipe_id: str):
        recipe = _catalog().get_recipe(recipe_id)
        if recipe is None:
            raise HTTPException(status_code=404, detail="Not found")
        return recipe

    # --- catalog ----------------------------------------------------------
    # `/runs` and `/step-kinds` are registered ahead of `/{recipe_id}` so they
    # are never swallowed as a literal recipe id.

    @router.get("", summary="Available recipes")
    async def list_recipes(
        current_user: User = Depends(get_current_admin_user),
    ) -> Dict[str, List[RecipeSummary]]:
        runner = _runner()
        summaries: List[RecipeSummary] = []
        for recipe in _catalog().list_recipes():
            completed = runner.get_latest_completed_run(recipe.id)
            summaries.append(
                RecipeSummary.from_recipe(
                    recipe,
                    preset_name=_preset_name(recipe),
                    last_completed_at=completed.completed_at if completed else None,
                )
            )
        return {"recipes": summaries}

    @router.get(
        "/step-kinds",
        summary="Recipe step kinds this instance can run",
    )
    async def list_step_kinds(
        current_user: User = Depends(get_current_admin_user),
    ) -> Dict[str, List[StepKindView]]:
        registry = _runner().executor_registry
        if registry is None:
            return {"kinds": []}
        return {
            "kinds": [
                StepKindView(kind=k.kind, source=k.source, plugin_id=k.plugin_id)
                for k in registry.list_kinds()
            ]
        }

    # --- runs -------------------------------------------------------------

    @router.get("/runs", summary="Recipe runs, newest first")
    async def list_recipe_runs(
        recipe_id: Optional[str] = None,
        limit: int = Query(default=50, ge=1, le=200),
        current_user: User = Depends(get_current_admin_user),
    ) -> Dict[str, List[RecipeRunView]]:
        runner = _runner()
        catalog = _catalog()
        runs = runner.list_runs(recipe_id=recipe_id, limit=limit)
        return {"runs": [run_view(runner, catalog, r) for r in runs]}

    @router.get("/runs/{run_id}", response_model=RecipeRunView, summary="Recipe-run detail")
    async def get_recipe_run(
        run_id: str,
        current_user: User = Depends(get_current_admin_user),
    ) -> RecipeRunView:
        runner = _runner()
        run = runner.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Not found")
        return run_view(runner, _catalog(), run)

    @router.post(
        "/runs/{run_id}/actions",
        response_model=RecipeRunView,
        summary="Apply a recipe-run action (pause|resume|cancel|retry_step)",
    )
    async def act_on_recipe_run(
        run_id: str,
        body: RecipeRunActionRequest,
        current_user: User = Depends(get_current_admin_user),
    ) -> RecipeRunView:
        runner = _runner()
        if body.action not in ADMIN_ACTIONS:
            raise HTTPException(status_code=400, detail="Unknown action")
        try:
            run = runner.apply_action(run_id, body.action)
            if body.action in ("resume", "retry_step"):
                # Keep going in the background: the run may have several
                # already-approved steps left, and one of them can be a real
                # download. See `RecipeRunner.drive_async`.
                runner.drive_async(run_id)
                run = runner.get_run_or_raise(run_id)
        except RecipeRunNotFound:
            raise HTTPException(status_code=404, detail="Not found")
        except IllegalRecipeRunTransition as e:
            raise HTTPException(status_code=409, detail=str(e))
        except RecipeRunError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return run_view(runner, _catalog(), run)

    @router.post(
        "/runs/{run_id}/consent/{step_key}",
        response_model=RecipeRunView,
        summary="Approve the step a recipe run is parked on",
    )
    async def grant_recipe_run_consent(
        run_id: str,
        step_key: str,
        current_user: User = Depends(get_current_admin_user),
    ) -> RecipeRunView:
        runner = _runner()
        try:
            runner.grant_consent(run_id, step_key, granted_by=current_user.id)
            runner.drive_async(run_id)
            run = runner.get_run_or_raise(run_id)
        except RecipeRunNotFound:
            raise HTTPException(status_code=404, detail="Not found")
        except IllegalRecipeRunTransition as e:
            raise HTTPException(status_code=409, detail=str(e))
        except RecipeRunError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return run_view(runner, _catalog(), run)

    # --- one recipe -------------------------------------------------------

    @router.get("/{recipe_id}", response_model=RecipeDetail, summary="Recipe detail")
    async def get_recipe(
        recipe_id: str,
        current_user: User = Depends(get_current_admin_user),
    ) -> RecipeDetail:
        recipe = _require_recipe(recipe_id)
        completed = _runner().get_latest_completed_run(recipe.id)
        return RecipeDetail.from_recipe(
            recipe,
            preset_name=_preset_name(recipe),
            last_completed_at=completed.completed_at if completed else None,
            load_errors=_catalog().load_errors.get(recipe.source_path, []),
        )

    @router.get(
        "/{recipe_id}/readiness",
        response_model=ReadinessReport,
        summary="Whether this instance is ready to run a recipe",
    )
    async def get_recipe_readiness(
        recipe_id: str,
        current_user: User = Depends(get_current_admin_user),
    ) -> ReadinessReport:
        _require_recipe(recipe_id)
        return await build_readiness_aggregator(container).evaluate(
            current_user, recipe_id=recipe_id
        )

    @router.post(
        "/{recipe_id}/runs",
        response_model=RecipeRunView,
        status_code=201,
        summary="Start a recipe run from the admin page",
    )
    async def start_recipe_run(
        recipe_id: str,
        body: StartRecipeRunRequest,
        current_user: User = Depends(get_current_admin_user),
    ) -> RecipeRunView:
        recipe = _require_recipe(recipe_id)
        runner = _runner()
        try:
            run = runner.create_run(
                recipe.id,
                recipe_version=body.recipe_version or recipe.version,
                mode=MODE_ADMIN,
                created_by=current_user.id,
                reuse_active=False,
            )
        except ActiveRecipeRunExists:
            raise HTTPException(
                status_code=409,
                detail="Another recipe run is already in progress on this instance.",
            )
        # Drive it forward in the background - a brand-new run is PENDING and
        # nothing else will call `execute_current_step` on its behalf. The
        # response reflects whatever status the run is in right now; the page
        # polls `GET /runs/{id}` for the rest.
        runner.drive_async(run.id)
        run = runner.get_run_or_raise(run.id)
        return run_view(runner, _catalog(), run)

    return router
