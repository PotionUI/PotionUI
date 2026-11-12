import json
import logging
from typing import Any, Awaitable, Dict, Optional

from fastapi import HTTPException

from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult
from src.platform.http.base_controller import APIResponse
from src.platform.security.user import AccountType, User

logger = logging.getLogger(__name__)

ADMIN_REQUIRED = (
    "This needs administrator authority: the key's owner must be an administrator and the key must hold the "
    "admin scope."
)


def acting_user(context: ToolContext) -> User:
    return User(
        username=context.user_id,
        email="",
        password_hash="",
        id=context.user_id,
        account_type=AccountType.ADMIN if context.acts_as_admin else AccountType.USER,
        owner_account_type=AccountType.ADMIN if context.is_admin else AccountType.USER,
    )


def failure(message: str) -> ToolResult:
    return ToolResult(success=False, data="", error=message)


def payload(data: Any) -> ToolResult:
    return ToolResult(success=True, data=json.dumps(data, default=str))


def detail_message(detail: Any) -> str:
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("error") or detail.get("code") or "The request was refused.")
    return str(detail or "The request was refused.")


async def run_controller(call: Awaitable[APIResponse]) -> ToolResult:
    try:
        response = await call
    except HTTPException as exc:
        return failure(detail_message(exc.detail))
    if not response.success:
        return failure(response.message or response.error or "The request failed.")
    return payload(response.data if response.data is not None else {"message": response.message or "Done."})


def without(item: Dict[str, Any], *keys: str) -> Dict[str, Any]:
    return {key: value for key, value in item.items() if key not in keys}


class ArgumentError(Exception):
    pass


class DomainTool(BaseTool):
    modes = ["mcp"]
    mcp = True
    admin_only = False
    domain: str = ""

    def refusal(self, context: ToolContext) -> Optional[ToolResult]:
        if self.admin_only and not context.acts_as_admin:
            return failure(ADMIN_REQUIRED)
        if context.domains is None or getattr(context.domains, self.domain, None) is None:
            return failure("This is not available on this server.")
        return None

    def service(self, context: ToolContext) -> Any:
        return getattr(context.domains, self.domain)

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        refused = self.refusal(context)
        if refused is not None:
            return refused
        try:
            return await self.run(context, **kwargs)
        except HTTPException as exc:
            return failure(detail_message(exc.detail))
        except ArgumentError as exc:
            return failure(str(exc))

    async def run(self, context: ToolContext, **kwargs) -> ToolResult:
        raise NotImplementedError


def required_text(kwargs: Dict[str, Any], name: str) -> Optional[str]:
    value = kwargs.get(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def bounded_int(kwargs: Dict[str, Any], name: str, default: int, low: int, high: int) -> int:
    raw = kwargs.get(name)
    try:
        value = default if raw in (None, "") else int(raw)
    except (TypeError, ValueError):
        raise ArgumentError(f"'{name}' must be an integer.") from None
    return min(max(low, value), high)
