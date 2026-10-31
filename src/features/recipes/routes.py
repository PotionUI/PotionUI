"""Admin-facing recipe endpoints: browse the catalog, inspect one recipe, and
run it.

Everything here is admin-only - a recipe enables plugins, downloads model
files, installs presets and generates. The first-run wizard drives the same
recipes through `/api/setup/...` (see `src.features.setup.routes`); the
difference is the run's `mode`: an onboarding run executes every step, an
admin run skips the ones a recipe marks `onboarding_only`.
"""

import asyncio
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.features.recipes.dto import (
    GrantConsentRequest,
    ModeRemovalRequest,
    RecipePlanRequest,
    RecipeDetail,
    RecipeRunActionRequest,
    RecipeRunView,
    RecipeSummary,
    StartRecipeRunRequest,
    StepKindView,
    VariantDownloadRequest,
)
from src.features.recipes.executors.modes_plan import PRESELECT_KEY
from src.features.recipes.records import MODE_ADMIN, RecipeRun, RecipeRunStatus
from src.features.recipes.removal import ModeRemovalError
from src.features.recipes.slot_variants import VariantDownloadError
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

    def _links():
        return container.recipe_preset_links

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
        return {"recipes": _links().summaries(_catalog().list_recipes())}

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

    @router.get("/variants/preset/{preset_id}", summary="Download variants a preset's recipes offer")
    async def get_preset_slot_variants(
        preset_id: str,
        model_type: Optional[str] = None,
        current_user: User = Depends(get_current_admin_user),
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(container.recipe_slot_variants.for_preset, preset_id, model_type)

    @router.get("/variants/model/{model_id}", summary="The recipe slot an installed model belongs to")
    async def get_model_slot_variants(
        model_id: str,
        current_user: User = Depends(get_current_admin_user),
    ) -> Dict[str, Any]:
        result = await asyncio.to_thread(container.recipe_slot_variants.for_model_id, model_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Not found")
        return result

    @router.post("/variants/download", status_code=202, summary="Download one variant of a recipe slot")
    async def download_slot_variant(
        body: VariantDownloadRequest,
        current_user: User = Depends(get_current_admin_user),
    ) -> Dict[str, Any]:
        try:
            return await asyncio.to_thread(
                container.recipe_slot_variants.queue_download,
                body.recipe_id,
                body.artifact_id,
                body.variant_id,
                current_user.id,
            )
        except VariantDownloadError as e:
            status = {"variant_not_found": 404, "already_installed": 409}.get(e.code, 400)
            raise HTTPException(status_code=status, detail={"code": e.code, "message": e.message})

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
        body: Optional[GrantConsentRequest] = None,
        current_user: User = Depends(get_current_admin_user),
    ) -> RecipeRunView:
        runner = _runner()
        try:
            runner.grant_consent(
                run_id,
                step_key,
                granted_by=current_user.id,
                selections=body.selections if body else None,
                modes=body.modes if body else None,
                extras=body.extras if body else None,
                target=body.target if body else None,
            )
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
        return _links().detail(
            recipe, load_errors=_catalog().load_errors.get(recipe.source_path, [])
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
                safe_input={PRESELECT_KEY: list(body.preselect_modes)} if body.preselect_modes else None,
                created_by=current_user.id,
                reuse_active=False,
            )
        except ActiveRecipeRunExists as e:
            active = e.run
            other_recipe = _catalog().get_recipe(active.recipe_id)
            recipe_name = other_recipe.name if other_recipe is not None else active.recipe_id
            raise HTTPException(
                status_code=409,
                detail={
                    "message": f"{recipe_name} is already running on this instance.",
                    "active_run": {
                        "id": active.id,
                        "recipe_id": active.recipe_id,
                        "recipe_name": recipe_name,
                        "status": active.status.value,
                        "current_step_key": active.current_step,
                    },
                },
            )
        # Drive it forward in the background - a brand-new run is PENDING and
        # nothing else will call `execute_current_step` on its behalf. The
        # response reflects whatever status the run is in right now; the page
        # polls `GET /runs/{id}` for the rest.
        runner.drive_async(run.id)
        run = runner.get_run_or_raise(run.id)
        return run_view(runner, _catalog(), run)

    def _dry_run(recipe, body: RecipePlanRequest, user_id: str) -> Dict[str, Any]:
        registry = _runner().executor_registry
        if registry is None:
            raise HTTPException(status_code=503, detail="Recipe steps are not available on this instance.")
        run = RecipeRun(
            id="preview",
            recipe_id=recipe.id,
            recipe_version=recipe.version,
            scope="instance",
            status=RecipeRunStatus.PENDING,
            safe_input={PRESELECT_KEY: list(body.preselect_modes)},
            created_by=user_id,
            mode=MODE_ADMIN,
        )
        steps = registry.preview(recipe, run, target=body.target)
        consent = next(
            (
                (entry["preview"] or {}).get("consent_request")
                for entry in steps
                if entry["kind"] == "modes.plan" and isinstance(entry["preview"], dict)
            ),
            None,
        )
        for entry in steps:
            if isinstance(entry["preview"], dict):
                entry["preview"] = {k: v for k, v in entry["preview"].items() if k != "consent_request"}
        files = (consent or {}).get("artifacts") or []
        return {
            "recipe_id": recipe.id,
            "steps": steps,
            "consent_request": consent,
            "totals": {
                "download_bytes": (consent or {}).get("total_bytes") if consent else None,
                "file_count": len(files),
            },
        }

    @router.post("/{recipe_id}/plan", summary="Preview a recipe run without starting it")
    async def plan_recipe(
        recipe_id: str,
        body: Optional[RecipePlanRequest] = None,
        current_user: User = Depends(get_current_admin_user),
    ) -> Dict[str, Any]:
        recipe = _require_recipe(recipe_id)
        return await asyncio.to_thread(_dry_run, recipe, body or RecipePlanRequest(), current_user.id)

    def _removal_error(exc: ModeRemovalError) -> HTTPException:
        status = {"not_found": 404, "conflict": 409, "not_local": 400}.get(exc.code, 400)
        return HTTPException(status_code=status, detail={"code": exc.code, "message": exc.message})

    @router.get("/{recipe_id}/modes/{mode}/removal", summary="What removing a mode's files would delete")
    async def preview_mode_removal(
        recipe_id: str,
        mode: str,
        current_user: User = Depends(get_current_admin_user),
    ) -> Dict[str, Any]:
        _require_recipe(recipe_id)
        try:
            return await asyncio.to_thread(container.mode_file_removal.preview, recipe_id, mode)
        except ModeRemovalError as exc:
            raise _removal_error(exc)

    @router.post("/{recipe_id}/modes/{mode}/removal", summary="Delete the files only this mode uses")
    async def remove_mode_files(
        recipe_id: str,
        mode: str,
        body: ModeRemovalRequest,
        current_user: User = Depends(get_current_admin_user),
    ) -> Dict[str, Any]:
        _require_recipe(recipe_id)
        try:
            return await asyncio.to_thread(container.mode_file_removal.remove, recipe_id, mode, body.model_ids)
        except ModeRemovalError as exc:
            raise _removal_error(exc)

    return router
