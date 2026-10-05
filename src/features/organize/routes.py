import asyncio
from typing import TYPE_CHECKING, Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.features.organize.dto import (
    AdminControlsBody,
    AdminUserBody,
    ApplyExistingBody,
    PreviewBody,
    ReorderBody,
    RuleBody,
    RulePatch,
    body_dict,
)
from src.features.organize.errors import OrganizeError
from src.features.organize.manager import OrganizeManager
from src.platform.http.base_controller import APIResponse, BaseController
from src.platform.security.current_user import get_current_active_user, get_current_admin_user

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class OrganizeController(BaseController):

    def __init__(self, manager: OrganizeManager):
        super().__init__()
        self.manager = manager

    async def run(self, error: str, func: Callable[..., Any], *args: Any) -> APIResponse:
        try:
            return self.success_response(data=await asyncio.to_thread(func, *args))
        except OrganizeError as exc:
            detail = {"success": False, "error": exc.code, "message": exc.message, **exc.extra}
            self.logger.info(f"Auto-organize request refused: {exc.code}")
            raise HTTPException(status_code=exc.status_code, detail=detail)
        except HTTPException:
            raise
        except Exception as exc:
            self.handle_exception(exc, error_code=error, message="Auto-organize request failed")


def build_router(container: "AppContainer") -> APIRouter:
    controller: OrganizeController = container.organize_controller
    manager = controller.manager
    router = APIRouter(prefix="/api/organize", tags=["Auto-organize"])

    @router.get("/catalog", response_model=APIResponse, summary="Facts and actions a rule can use")
    async def catalog(subject: Optional[str] = None, user=Depends(get_current_active_user)):
        return await controller.run("organize_catalog_failed", manager.catalog, user, subject)

    @router.get("/facts/{key}/options", response_model=APIResponse, summary="Choices for a fact")
    async def fact_options(key: str, subject: Optional[str] = None, q: str = "", limit: int = Query(50, ge=1, le=200),
                           user=Depends(get_current_active_user)):
        return await controller.run("organize_options_failed", manager.fact_options, user, key, subject, q, limit)

    @router.get("/templates", response_model=APIResponse, summary="Starter rules")
    async def templates(subject: Optional[str] = None, user=Depends(get_current_active_user)):
        return await controller.run("organize_templates_failed", manager.templates, subject)

    @router.get("/summary", response_model=APIResponse, summary="Rule counts for the sidebar")
    async def summary(user=Depends(get_current_active_user)):
        return await controller.run("organize_summary_failed", manager.summary, user)

    @router.get("/rules", response_model=APIResponse, summary="List my rules")
    async def list_rules(subject: Optional[str] = None, user=Depends(get_current_active_user)):
        return await controller.run("organize_list_failed", manager.list_rules, user, subject)

    @router.post("/rules", response_model=APIResponse, summary="Create a rule")
    async def create_rule(body: RuleBody, user=Depends(get_current_active_user)):
        return await controller.run("organize_create_failed", manager.create_rule, user, body_dict(body))

    @router.post("/rules/reorder", response_model=APIResponse, summary="Reorder rules")
    async def reorder(body: ReorderBody, user=Depends(get_current_active_user)):
        return await controller.run("organize_reorder_failed", manager.reorder, user, body.subject, body.rule_ids)

    @router.get("/rules/{rule_id}", response_model=APIResponse, summary="Get a rule")
    async def get_rule(rule_id: str, user=Depends(get_current_active_user)):
        return await controller.run("organize_get_failed", manager.get_rule, user, rule_id)

    @router.put("/rules/{rule_id}", response_model=APIResponse, summary="Replace a rule")
    async def update_rule(rule_id: str, body: RuleBody, user=Depends(get_current_active_user)):
        return await controller.run("organize_update_failed", manager.update_rule, user, rule_id, body_dict(body))

    @router.patch("/rules/{rule_id}", response_model=APIResponse, summary="Switch, rename or set stop on a rule")
    async def patch_rule(rule_id: str, body: RulePatch, user=Depends(get_current_active_user)):
        return await controller.run("organize_patch_failed", manager.patch_rule, user, rule_id, body_dict(body))

    @router.delete("/rules/{rule_id}", response_model=APIResponse, summary="Delete a rule")
    async def delete_rule(rule_id: str, user=Depends(get_current_active_user)):
        return await controller.run("organize_delete_failed", manager.delete_rule, user, rule_id)

    @router.post("/rules/{rule_id}/duplicate", response_model=APIResponse, summary="Duplicate a rule")
    async def duplicate_rule(rule_id: str, user=Depends(get_current_active_user)):
        return await controller.run("organize_duplicate_failed", manager.duplicate, user, rule_id)

    @router.post("/rules/{rule_id}/apply-existing", response_model=APIResponse, summary="Apply a rule to existing items")
    async def apply_existing(rule_id: str, body: Optional[ApplyExistingBody] = None, user=Depends(get_current_active_user)):
        return await controller.run("organize_apply_failed", manager.start_backfill, user, rule_id)

    @router.post("/preview", response_model=APIResponse, summary="Count what a rule would match")
    async def preview(body: PreviewBody, user=Depends(get_current_active_user)):
        return await controller.run("organize_preview_failed", manager.preview, user, body_dict(body))

    @router.get("/jobs", response_model=APIResponse, summary="My apply-to-existing jobs")
    async def list_jobs(active: bool = False, user=Depends(get_current_active_user)):
        return await controller.run("organize_jobs_failed", manager.list_jobs, user, active)

    @router.get("/jobs/{job_id}", response_model=APIResponse, summary="One job")
    async def get_job(job_id: str, user=Depends(get_current_active_user)):
        return await controller.run("organize_job_failed", manager.get_job, user, job_id)

    @router.post("/jobs/{job_id}/cancel", response_model=APIResponse, summary="Cancel a job")
    async def cancel_job(job_id: str, user=Depends(get_current_active_user)):
        return await controller.run("organize_cancel_failed", manager.cancel_job, user, job_id)

    @router.get("/activity", response_model=APIResponse, summary="What my rules did")
    async def activity(subject: Optional[str] = None, rule_id: Optional[str] = None, before: Optional[str] = None,
                       limit: int = Query(50, ge=1, le=200), user=Depends(get_current_active_user)):
        return await controller.run("organize_activity_failed", manager.activity, user, subject, rule_id, before, limit)

    @router.post("/runs/{run_id}/undo", response_model=APIResponse, summary="Undo a run")
    async def undo_run(run_id: str, user=Depends(get_current_active_user)):
        return await controller.run("organize_undo_failed", manager.undo_run, user, run_id)

    @router.get("/provenance/{item_type}/{item_id}", response_model=APIResponse, summary="Which rules filed an item")
    async def provenance(item_type: str, item_id: str, user=Depends(get_current_active_user)):
        return await controller.run("organize_provenance_failed", manager.provenance, user, item_type, item_id)

    @router.get("/collections/{scope}/{collection_id}/rules", response_model=APIResponse,
                summary="Rules that fill a collection")
    async def collection_rules(scope: str, collection_id: str, user=Depends(get_current_active_user)):
        return await controller.run("organize_collection_rules_failed", manager.collection_rules, user, scope,
                                    collection_id)

    return router


def build_admin_router(container: "AppContainer") -> APIRouter:
    controller: OrganizeController = container.organize_controller
    manager = controller.manager
    router = APIRouter(prefix="/api/admin/organize", tags=["Auto-organize admin"])

    @router.get("/overview", response_model=APIResponse, summary="Auto-organize counts across users")
    async def overview(admin=Depends(get_current_admin_user)):
        return await controller.run("organize_overview_failed", manager.admin_overview)

    @router.put("/controls", response_model=APIResponse, summary="Kill switch, default cap and hourly limit")
    async def controls(body: AdminControlsBody, admin=Depends(get_current_admin_user)):
        return await controller.run("organize_controls_failed", manager.admin_controls, body_dict(body))

    @router.put("/users/{user_id}", response_model=APIResponse, summary="Pause or cap one user")
    async def user_controls(user_id: str, body: AdminUserBody, admin=Depends(get_current_admin_user)):
        return await controller.run("organize_user_controls_failed", manager.admin_user, user_id, body_dict(body))

    return router
