from typing import TYPE_CHECKING, Any, Callable, Optional

from fastapi import APIRouter, Depends, Query

from src.features.organize.dto import (
    AdminControlsBody,
    AdminUserBody,
    ApplyExistingBody,
    MatchesBody,
    PreviewBody,
    ReorderBody,
    RuleBody,
    RulePatch,
    body_dict,
)
from src.features.organize.admin import OrganizeAdmin
from src.features.organize.backfill import BackfillManager
from src.features.organize.manager import OrganizeManager, instance_viewer
from src.platform.http.base_controller import APIResponse, BaseController
from src.platform.security.current_user import get_current_active_user, get_current_admin_user

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class OrganizeController(BaseController):
    failure_message = "Auto-organize request failed"

    def __init__(self, manager: OrganizeManager, backfill: BackfillManager, admin: OrganizeAdmin):
        super().__init__()
        self.manager = manager
        self.backfill = backfill
        self.admin = admin


def mount_rule_routes(router: APIRouter, controller: OrganizeController, who: Callable[..., Any], scope: str) -> None:
    manager = controller.manager
    backfill = controller.backfill

    @router.get("/catalog", response_model=APIResponse, summary="Facts and actions a rule can use")
    async def catalog(subject: Optional[str] = None, user=Depends(who)):
        return await controller.run("organize_catalog_failed", manager.catalog, user, subject)

    @router.get("/facts/{key}/options", response_model=APIResponse, summary="Choices for a fact")
    async def fact_options(key: str, subject: Optional[str] = None, q: str = "", limit: int = Query(50, ge=1, le=200),
                           user=Depends(who)):
        return await controller.run("organize_options_failed", manager.fact_options, user, key, subject, q, limit)

    @router.get("/templates", response_model=APIResponse, summary="Starter rules")
    async def templates(subject: Optional[str] = None, user=Depends(who)):
        return await controller.run("organize_templates_failed", manager.templates, subject, scope)

    @router.get("/rules", response_model=APIResponse, summary="List rules")
    async def list_rules(subject: Optional[str] = None, user=Depends(who)):
        return await controller.run("organize_list_failed", manager.list_rules, user, subject)

    @router.post("/rules", response_model=APIResponse, summary="Create a rule")
    async def create_rule(body: RuleBody, user=Depends(who)):
        return await controller.run("organize_create_failed", manager.create_rule, user, body_dict(body))

    @router.post("/rules/reorder", response_model=APIResponse, summary="Reorder rules")
    async def reorder(body: ReorderBody, user=Depends(who)):
        return await controller.run("organize_reorder_failed", manager.reorder, user, body.subject, body.rule_ids)

    @router.get("/rules/{rule_id}", response_model=APIResponse, summary="Get a rule")
    async def get_rule(rule_id: str, user=Depends(who)):
        return await controller.run("organize_get_failed", manager.get_rule, user, rule_id)

    @router.put("/rules/{rule_id}", response_model=APIResponse, summary="Replace a rule")
    async def update_rule(rule_id: str, body: RuleBody, user=Depends(who)):
        return await controller.run("organize_update_failed", manager.update_rule, user, rule_id, body_dict(body))

    @router.patch("/rules/{rule_id}", response_model=APIResponse, summary="Switch, rename or set stop on a rule")
    async def patch_rule(rule_id: str, body: RulePatch, user=Depends(who)):
        return await controller.run("organize_patch_failed", manager.patch_rule, user, rule_id, body_dict(body))

    @router.delete("/rules/{rule_id}", response_model=APIResponse, summary="Delete a rule")
    async def delete_rule(rule_id: str, user=Depends(who)):
        return await controller.run("organize_delete_failed", manager.delete_rule, user, rule_id)

    @router.post("/rules/{rule_id}/duplicate", response_model=APIResponse, summary="Duplicate a rule")
    async def duplicate_rule(rule_id: str, user=Depends(who)):
        return await controller.run("organize_duplicate_failed", manager.duplicate, user, rule_id)

    @router.post("/rules/{rule_id}/apply-existing", response_model=APIResponse, summary="Apply a rule to existing items")
    async def apply_existing(rule_id: str, body: Optional[ApplyExistingBody] = None, user=Depends(who)):
        return await controller.run("organize_apply_failed", backfill.start, user, rule_id)

    @router.post("/preview", response_model=APIResponse, summary="Count what a rule would match")
    async def preview(body: PreviewBody, user=Depends(who)):
        return await controller.run("organize_preview_failed", manager.preview, user, body_dict(body))

    @router.post("/preview/matches", response_model=APIResponse, summary="Page through what a rule would match")
    async def preview_matches(body: MatchesBody, user=Depends(who)):
        return await controller.run("organize_matches_failed", manager.preview_matches, user, body_dict(body))

    @router.get("/jobs", response_model=APIResponse, summary="Apply-to-existing jobs")
    async def list_jobs(active: bool = False, user=Depends(who)):
        return await controller.run("organize_jobs_failed", backfill.list_jobs, user, active)

    @router.get("/jobs/{job_id}", response_model=APIResponse, summary="One job")
    async def get_job(job_id: str, user=Depends(who)):
        return await controller.run("organize_job_failed", backfill.get_job, user, job_id)

    @router.post("/jobs/{job_id}/cancel", response_model=APIResponse, summary="Cancel a job")
    async def cancel_job(job_id: str, user=Depends(who)):
        return await controller.run("organize_cancel_failed", backfill.cancel_job, user, job_id)

    @router.get("/activity", response_model=APIResponse, summary="What the rules did")
    async def activity(subject: Optional[str] = None, rule_id: Optional[str] = None, before: Optional[str] = None,
                       limit: int = Query(50, ge=1, le=200), user=Depends(who)):
        return await controller.run("organize_activity_failed", manager.activity, user, subject, rule_id, before, limit)

    @router.get("/runs/{run_id}/items", response_model=APIResponse, summary="What a run did, item by item")
    async def run_items(run_id: str, status: Optional[str] = None, page: int = Query(1, ge=1),
                        page_size: int = Query(25, ge=1, le=100), user=Depends(who)):
        return await controller.run("organize_run_items_failed", manager.run_items, user, run_id, status, page, page_size)

    @router.post("/runs/{run_id}/retry", response_model=APIResponse, summary="Try the waiting items of a run again")
    async def retry_run(run_id: str, user=Depends(who)):
        return await controller.run("organize_retry_failed", backfill.retry_waiting, user, run_id)

    @router.post("/runs/{run_id}/undo", response_model=APIResponse, summary="Undo a run")
    async def undo_run(run_id: str, user=Depends(who)):
        return await controller.run("organize_undo_failed", manager.undo_run, user, run_id)


def build_router(container: "AppContainer") -> APIRouter:
    controller: OrganizeController = container.organize_controller
    manager = controller.manager
    router = APIRouter(prefix="/api/organize", tags=["Auto-organize"])

    @router.get("/summary", response_model=APIResponse, summary="Rule counts for the sidebar")
    async def summary(user=Depends(get_current_active_user)):
        return await controller.run("organize_summary_failed", manager.summary, user)

    @router.get("/provenance/{item_type}/{item_id}", response_model=APIResponse, summary="Which rules filed an item")
    async def provenance(item_type: str, item_id: str, user=Depends(get_current_active_user)):
        return await controller.run("organize_provenance_failed", manager.provenance, user, item_type, item_id)

    @router.get("/collections/{scope}/{collection_id}/rules", response_model=APIResponse,
                summary="Rules that fill a collection")
    async def collection_rules(scope: str, collection_id: str, user=Depends(get_current_active_user)):
        return await controller.run("organize_collection_rules_failed", manager.collection_rules, user, scope,
                                    collection_id)

    mount_rule_routes(router, controller, get_current_active_user, "user")
    return router


def build_admin_router(container: "AppContainer") -> APIRouter:
    controller: OrganizeController = container.organize_controller
    ops = controller.admin
    router = APIRouter(prefix="/api/admin/organize", tags=["Auto-organize admin"])

    @router.get("/overview", response_model=APIResponse, summary="Auto-organize counts across users")
    async def overview(admin=Depends(get_current_admin_user)):
        return await controller.run("organize_overview_failed", ops.overview)

    @router.put("/controls", response_model=APIResponse, summary="Kill switch, default cap and hourly limit")
    async def controls(body: AdminControlsBody, admin=Depends(get_current_admin_user)):
        return await controller.run("organize_controls_failed", ops.controls, body_dict(body))

    @router.put("/users/{user_id}", response_model=APIResponse, summary="Pause or cap one user")
    async def user_controls(user_id: str, body: AdminUserBody, admin=Depends(get_current_admin_user)):
        return await controller.run("organize_user_controls_failed", ops.user_controls, user_id, body_dict(body))

    @router.put("/instance/controls", response_model=APIResponse, summary="Pause or cap the instance rules")
    async def instance_controls(body: AdminUserBody, admin=Depends(get_current_admin_user)):
        return await controller.run("organize_instance_controls_failed", ops.instance_controls, body_dict(body))

    async def instance_scope(admin=Depends(get_current_admin_user)):
        return instance_viewer(admin)

    instance = APIRouter(prefix="/instance")
    mount_rule_routes(instance, controller, instance_scope, "instance")
    router.include_router(instance)
    return router
