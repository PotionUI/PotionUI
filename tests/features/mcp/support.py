from typing import Optional

from src.features.api_keys.records import ApiKey
from src.features.llm.tools.base import ToolServices
from src.features.llm.tools.registry import ToolRegistry
from src.features.mcp import protocol
from src.features.mcp.protocol import McpToolCollaborators
from src.features.mcp.tool_policy import POLICY_ALL_ON
from src.features.mcp.tool_repository import McpToolRepository
from src.platform.security.api_scopes import ApiScope
from src.platform.settings.repository import SettingRepository
from src.platform.settings.settings import Settings
from tests.fixtures.seed import seed_user

KEY_ID = "key-1"


def build_collaborators(registry: ToolRegistry, services: Optional[ToolServices] = None) -> McpToolCollaborators:
    return McpToolCollaborators(
        tool_registry=registry,
        tool_repository=McpToolRepository(),
        settings=Settings(SettingRepository()),
        services=services or ToolServices(),
    )


def user_with_every_tool(db, user_id: str) -> str:
    seed_user(db, user_id)
    McpToolRepository().apply_default(user_id, POLICY_ALL_ON, {})
    return user_id


def api_key(key_id: str, user_id: str) -> ApiKey:
    return ApiKey(
        id=key_id, user_id=user_id, name=key_id, secret_hash=f"hash-{key_id}", display_prefix="pui_",
        last_four=None, scopes=frozenset({ApiScope.MCP}), created_at="2026-11-11T09:00:00+00:00",
    )


async def handle_method(collaborators, method, params, user_id, **kwargs):
    kwargs.setdefault("key_id", KEY_ID)
    return await protocol.handle_method(collaborators, method, params, user_id, **kwargs)
