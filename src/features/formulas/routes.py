from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query

from src.features.formulas import operations
from src.features.formulas.collaborators import FormulaCollaborators
from src.features.formulas.dto import CreateFormulaRequest, PlanFormulaRequest, UpdateFormulaRequest
from src.platform.http.base_controller import APIResponse, BaseController
from src.platform.security.api_scopes import ApiScope, key_access, key_scope
from src.platform.security.current_user import get_current_active_user

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class FormulaController(BaseController):
    failure_message = "Formula request failed"

    def __init__(self, collaborators: FormulaCollaborators):
        super().__init__()
        self.collaborators = collaborators

    async def list_formulas(self, user_id: str, preset_id: str, mode: str) -> APIResponse:
        def run(collaborators, *args):
            return [formula.to_dict() for formula in operations.list_formulas(collaborators, *args)]
        return await self.run("list_formulas_failed", run, self.collaborators, user_id, preset_id, mode)

    async def create_formula(self, user_id: str, request: CreateFormulaRequest) -> APIResponse:
        def run(collaborators, *args):
            return operations.create_formula(collaborators, *args).to_dict()
        return await self.run("create_formula_failed", run, self.collaborators, user_id, request)

    async def update_formula(self, user_id: str, formula_id: str, request: UpdateFormulaRequest) -> APIResponse:
        def run(collaborators, *args):
            return operations.update_formula(collaborators, *args).to_dict()
        return await self.run("update_formula_failed", run, self.collaborators, user_id, formula_id, request)

    async def duplicate_formula(self, user_id: str, formula_id: str) -> APIResponse:
        def run(collaborators, *args):
            return operations.duplicate_formula(collaborators, *args).to_dict()
        return await self.run("duplicate_formula_failed", run, self.collaborators, user_id, formula_id)

    async def delete_formula(self, user_id: str, formula_id: str) -> APIResponse:
        def run(collaborators, *args):
            operations.delete_formula(collaborators, *args)
            return None
        return await self.run("delete_formula_failed", run, self.collaborators, user_id, formula_id)

    async def plan_formula(self, user, formula_id: str, request: PlanFormulaRequest) -> APIResponse:
        return await self.run("plan_formula_failed", operations.plan_formula, self.collaborators, user, formula_id, request)


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.formula_controller
    router = APIRouter(prefix="/api/formulas", tags=["Formulas"], dependencies=[key_access(read=ApiScope.READ, write=ApiScope.CONTENT)])

    @router.get("", response_model=APIResponse, summary="List Formulas")
    async def list_formulas(
        preset_id: str = Query(..., min_length=1),
        mode: str = Query(..., min_length=1),
        current_user=Depends(get_current_active_user),
    ):
        return await controller.list_formulas(current_user.id, preset_id, mode)

    @router.post("", response_model=APIResponse, summary="Create Formula")
    async def create_formula(request: CreateFormulaRequest, current_user=Depends(get_current_active_user)):
        return await controller.create_formula(current_user.id, request)

    @router.put("/{formula_id}", response_model=APIResponse, summary="Update Formula")
    async def update_formula(
        formula_id: str, request: UpdateFormulaRequest, current_user=Depends(get_current_active_user)
    ):
        return await controller.update_formula(current_user.id, formula_id, request)

    @router.post("/{formula_id}/duplicate", response_model=APIResponse, summary="Duplicate Formula")
    async def duplicate_formula(formula_id: str, current_user=Depends(get_current_active_user)):
        return await controller.duplicate_formula(current_user.id, formula_id)

    @router.post("/{formula_id}/plan", response_model=APIResponse, summary="Plan Applying a Formula", dependencies=[key_scope(ApiScope.READ)])
    async def plan_formula(
        formula_id: str, request: PlanFormulaRequest, current_user=Depends(get_current_active_user)
    ):
        return await controller.plan_formula(current_user, formula_id, request)

    @router.delete("/{formula_id}", response_model=APIResponse, summary="Delete Formula")
    async def delete_formula(formula_id: str, current_user=Depends(get_current_active_user)):
        return await controller.delete_formula(current_user.id, formula_id)

    return router
