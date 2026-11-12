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

ADDED_AFTER_SEED_READS = frozenset({
    "list_models", "get_generation_status", "wait_for_generation", "get_generation_results",
    "list_elixirs", "describe_elixir", "get_elixir_run", "list_automations", "get_automation_status", "search_provider_models", "get_download_status", "list_inspirations", "list_sessions", "list_notifications"
})
ADDED_AFTER_SEED_WRITES = frozenset({
    "cancel_generation", "upload_media",
    "run_elixir", "run_automation", "download_model", "delete_generation", "delete_library_item", "publish_inspiration", "mark_notifications_read"
})
ADDED_AFTER_SEED = ADDED_AFTER_SEED_READS | ADDED_AFTER_SEED_WRITES
DESTRUCTIVE_TOOLS = frozenset({"delete_generation", "delete_library_item"})


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
