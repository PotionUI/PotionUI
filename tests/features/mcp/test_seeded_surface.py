from unittest.mock import Mock

import pytest

from src.features.llm.tools.base import BaseTool, ToolResult, ToolServices
from src.features.llm.tools.builtin import register_builtin_tools
from src.features.llm.tools.registry import ToolRegistry
from tests.features.mcp.support import build_collaborators, handle_method
from tests.features.mcp.test_tool_surface import EXPECTED_TOOLS
from tests.fixtures.seed import seed_user
from tests.platform.database._support import load_migration

OLD = "2026-10-01T00:00:00+00:00"


def put(db, sql, params=()):
    with db.get_cursor() as cursor:
        cursor.execute(sql, params)


def seed_key(db, key_id, user_id):
    put(
        db,
        "INSERT INTO api_keys (id, user_id, name, secret_hash, display_prefix, scopes, created_at) "
        "VALUES (?, ?, ?, ?, 'pui_', '[\"mcp\"]', ?)",
        (key_id, user_id, key_id, f"hash-{key_id}", OLD),
    )


async def listed(collaborators, user_id, key_id):
    result = await handle_method(collaborators, "tools/list", {}, user_id, key_id=key_id)
    return {tool["name"] for tool in result["tools"]}


@pytest.fixture
def collaborators(mcp_db):
    registry = ToolRegistry()
    register_builtin_tools(registry)
    llm_repository = Mock()
    llm_repository.get_default_configuration.return_value = None
    return build_collaborators(registry, ToolServices(llm_repository=llm_repository))


@pytest.mark.asyncio
async def test_an_existing_mcp_user_keeps_the_tool_list_their_clients_saw_before_the_upgrade(mcp_db, collaborators):
    seed_user(mcp_db, "u-old")
    seed_user(mcp_db, "u-opted-out")
    seed_user(mcp_db, "u-new")
    seed_key(mcp_db, "k-old", "u-old")
    seed_key(mcp_db, "k-opted-out", "u-opted-out")
    put(mcp_db, "INSERT INTO settings (id, key, value, value_type, type) VALUES ('s-llm', 'llm_default_provider', 'cfg', 'string', 'SYSTEM')")
    put(mcp_db, "INSERT INTO tool_governance (llm_config_id, tool_name, enabled, locked, updated_at) VALUES ('cfg', 'delete_memory', 0, 0, ?)", (OLD,))
    for tool in ("add_prompt", "list_presets"):
        put(mcp_db, "INSERT INTO user_disabled_tools (user_id, tool_name, created_at) VALUES ('u-opted-out', ?, ?)", (tool, OLD))

    load_migration("096_mcp_tool_seed", mcp_db).up()

    everything_before = EXPECTED_TOOLS - {"list_models"}
    assert await listed(collaborators, "u-old", "k-old") == everything_before - {"delete_memory"} | {"list_models"}
    assert await listed(collaborators, "u-opted-out", "k-opted-out") == (
        everything_before - {"delete_memory", "add_prompt", "list_presets"} | {"list_models"}
    )


@pytest.mark.asyncio
async def test_a_user_who_never_used_mcp_gets_only_the_tools_that_neither_write_nor_spend(mcp_db, collaborators):
    seed_user(mcp_db, "u-new")
    seed_key(mcp_db, "k-new", "u-new")
    seed_user(mcp_db, "u-other")

    load_migration("096_mcp_tool_seed", mcp_db).up()
    seed_key(mcp_db, "k-later", "u-other")

    names = await listed(collaborators, "u-other", "k-later")

    assert {"list_presets", "list_models", "read_memory", "write_condition"} <= names
    assert names.isdisjoint({
        "start_generation", "add_prompt", "write_memory", "run_media_tool", "organize_gallery", "enhance_prompt",
    })


@pytest.mark.asyncio
async def test_a_tool_added_after_the_upgrade_arrives_read_only_for_a_seeded_user(mcp_db, collaborators):
    seed_user(mcp_db, "u-old")
    seed_key(mcp_db, "k-old", "u-old")
    load_migration("096_mcp_tool_seed", mcp_db).up()

    class _Later(BaseTool):
        name = "later_write"
        mcp = True

        @property
        def description(self):
            return "d"

        @property
        def parameters(self):
            return {"type": "object", "properties": {}, "required": []}

        async def execute(self, context, **kwargs):
            return ToolResult(success=True, data="")

    class _LaterRead(_Later):
        name = "later_read"
        read_only = True

    collaborators.tool_registry.register(_Later())
    collaborators.tool_registry.register(_LaterRead())

    names = await listed(collaborators, "u-old", "k-old")

    assert "later_read" in names and "later_write" not in names
