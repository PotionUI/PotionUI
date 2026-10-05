import asyncio
from typing import TYPE_CHECKING, Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.features.plans.dto import AssignBody, PlanBody, SettingsBody
from src.features.plans.errors import PlanError
from src.features.plans.manager import PlansManager
from src.platform.http.base_controller import APIResponse, BaseController
from src.platform.security.current_user import get_current_active_user, get_current_admin_user

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class PlansController(BaseController):

    def __init__(self, manager: PlansManager):
        super().__init__()
        self.manager = manager

    async def run(self, error: str, func: Callable[..., Any], *args: Any) -> APIResponse:
        try:
            return self.success_response(data=await asyncio.to_thread(func, *args))
        except PlanError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.payload())
        except HTTPException:
            raise
        except Exception as exc:
            self.handle_exception(exc, error_code=error, message="Plans request failed")


def build_router(container: "AppContainer") -> APIRouter:
    controller: PlansController = container.plans_controller
    manager = controller.manager
    router = APIRouter(prefix="/api/me/limits", tags=["Plans"])

    @router.get("", response_model=APIResponse, summary="My plan limits and usage")
    async def my_limits(user=Depends(get_current_active_user)):
        return await controller.run("plans_limits_failed", manager.my_limits, user)

    @router.get("/storage", response_model=APIResponse, summary="What is using my storage")
    async def my_storage(user=Depends(get_current_active_user)):
        return await controller.run("plans_storage_failed", manager.my_storage, user)

    return router


def build_admin_router(container: "AppContainer") -> APIRouter:
    controller: PlansController = container.plans_controller
    manager = controller.manager
    router = APIRouter(prefix="/api/admin/plans", tags=["Plans admin"])

    @router.get("", response_model=APIResponse, summary="Plans, kinds and plan settings")
    async def list_plans(admin=Depends(get_current_admin_user)):
        return await controller.run("plans_list_failed", manager.list_plans)

    @router.post("", response_model=APIResponse, summary="Create a plan")
    async def create_plan(body: PlanBody, admin=Depends(get_current_admin_user)):
        return await controller.run("plans_create_failed", manager.create_plan, body)

    @router.get("/kinds", response_model=APIResponse, summary="Registered limit kinds")
    async def kinds(admin=Depends(get_current_admin_user)):
        return await controller.run("plans_kinds_failed", manager.kinds)

    @router.get("/settings", response_model=APIResponse, summary="Plan settings")
    async def get_settings(admin=Depends(get_current_admin_user)):
        return await controller.run("plans_settings_failed", manager.get_settings)

    @router.put("/settings", response_model=APIResponse, summary="Change plan settings")
    async def update_settings(body: SettingsBody, admin=Depends(get_current_admin_user)):
        return await controller.run("plans_settings_failed", manager.update_settings, body, admin.id)

    @router.get("/groups", response_model=APIResponse, summary="Groups and their plans")
    async def list_groups(admin=Depends(get_current_admin_user)):
        return await controller.run("plans_groups_failed", manager.list_groups)

    @router.put("/groups/{group_id}", response_model=APIResponse, summary="Set a group's plan")
    async def assign_group(group_id: str, body: AssignBody, admin=Depends(get_current_admin_user)):
        return await controller.run("plans_assign_failed", manager.assign_group, group_id, body.plan_id, admin.id)

    @router.get("/groups/{group_id}/impact", response_model=APIResponse, summary="Preview a group plan change")
    async def impact(group_id: str, plan_id: Optional[str] = None, admin=Depends(get_current_admin_user)):
        return await controller.run("plans_impact_failed", manager.impact, group_id, plan_id or None)

    @router.get("/users", response_model=APIResponse, summary="Users with usage per limit")
    async def list_users(q: Optional[str] = None, plan_id: Optional[str] = None, sort: Optional[str] = None,
                         limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                         admin=Depends(get_current_admin_user)):
        return await controller.run("plans_users_failed", manager.users, q, plan_id, sort, limit, offset)

    @router.get("/users/{user_id}", response_model=APIResponse, summary="A user's plan resolution and usage")
    async def user_detail(user_id: str, admin=Depends(get_current_admin_user)):
        return await controller.run("plans_user_failed", manager.user_detail, user_id)

    @router.put("/users/{user_id}", response_model=APIResponse, summary="Set a user's personal override")
    async def assign_user(user_id: str, body: AssignBody, admin=Depends(get_current_admin_user)):
        return await controller.run("plans_assign_failed", manager.assign_user, user_id, body.plan_id, admin.id)

    @router.get("/{plan_id}", response_model=APIResponse, summary="A plan with its assignments and usage")
    async def get_plan(plan_id: str, admin=Depends(get_current_admin_user)):
        return await controller.run("plans_get_failed", manager.get_plan, plan_id)

    @router.put("/{plan_id}", response_model=APIResponse, summary="Replace a plan")
    async def update_plan(plan_id: str, body: PlanBody, admin=Depends(get_current_admin_user)):
        return await controller.run("plans_update_failed", manager.update_plan, plan_id, body)

    @router.delete("/{plan_id}", response_model=APIResponse, summary="Delete a plan")
    async def delete_plan(plan_id: str, reassign_to: Optional[str] = None, admin=Depends(get_current_admin_user)):
        return await controller.run("plans_delete_failed", manager.delete_plan, plan_id, reassign_to)

    return router
