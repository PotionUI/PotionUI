import asyncio
import logging
from typing import TYPE_CHECKING, Any, Callable

from fastapi import APIRouter, Depends, Query

from src.features.models import operations as model_operations
from src.features.models.exceptions import ModelNotFoundException
from src.features.models.form_refs import is_model_ref, model_id_of
from src.features.prompt.resource_sources import read_source_members
from src.platform.http.base_controller import APIResponse, BaseController
from src.platform.http.error_pipeline import Fail, guarded
from src.platform.plugins.prompt_resource_sources import PromptResourceCaller, PromptResourceSourceRegistry
from src.platform.security.api_scopes import ApiScope, key_access
from src.platform.security.current_user import get_current_active_user
from src.platform.security.failure_text import failure_detail
from src.platform.security.user import AccountType, User

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer

logger = logging.getLogger(__name__)


class PromptResourceSourceController(BaseController):
    def __init__(
        self,
        sources: PromptResourceSourceRegistry,
        model_collaborators: Any,
        model_file: Callable[[str], Any],
    ) -> None:
        super().__init__()
        self.sources = sources
        self.model_collaborators = model_collaborators
        self.model_file = model_file

    @guarded(
        {ModelNotFoundException: Fail("item_not_found", status=404)},
        Fail("prompt_resource_members_failed", "Failed to read the item's members", detail=failure_detail),
        admin="user",
    )
    async def get_members(self, source_id: str, item: str, user: User = None) -> APIResponse:
        definition = self.sources.get(source_id)
        if definition is None:
            self.error_response(
                error="prompt_resource_source_not_found",
                message=f"No prompt resource source named '{source_id}'",
                status_code=404,
            )
        if not is_model_ref(item):
            raise ModelNotFoundException(f"Item '{item}' not found")
        is_admin = bool(user and user.account_type == AccountType.ADMIN)
        await model_operations.get_model_by_id(self.model_collaborators, model_id_of(item), user=user, admin=is_admin)
        caller = PromptResourceCaller(user_id=getattr(user, "id", None), is_admin=is_admin, model_file=self.model_file)
        members = None
        try:
            members = await asyncio.to_thread(read_source_members, self.sources, source_id, item, caller)
        except ValueError as error:
            logger.warning("Prompt resource %s from %s cannot be read: %s", item, source_id, error)
        if members is None:
            self.error_response(
                error="prompt_resource_unreadable",
                message=f"This {definition.label} is missing or cannot be read",
                status_code=422,
            )
        return self.success_response(data={"members": members})


def build_router(container: "AppContainer") -> APIRouter:
    controller = PromptResourceSourceController(
        container.prompt_resource_source_registry,
        container.model_index_manager,
        container.model_locator.path_for_model,
    )
    router = APIRouter(prefix="/api/prompt-resources", tags=["Prompt resources"], dependencies=[key_access(read=ApiScope.READ, write=None)])

    @router.get("/sources/{source_id}/members", response_model=APIResponse, summary="Get an item's members")
    async def get_members(
        source_id: str,
        item: str = Query(..., min_length=1),
        current_user: User = Depends(get_current_active_user),
    ) -> APIResponse:
        return await controller.get_members(source_id, item, current_user)

    return router
