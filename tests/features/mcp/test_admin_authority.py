import json
from unittest.mock import Mock

import pytest

from src.features.llm.tools.base import ToolServices
from src.features.llm.tools.builtin.start_generation_tool import StartGenerationTool
from src.features.llm.tools.registry import ToolRegistry
from src.platform.security.user import AccountType
from tests.features.generation.test_admin_authority import wire
from tests.features.generation.test_orchestrator_fixed_prompt import (
    bind_form_passthrough,
    make_orchestrator,
    repo,
)
from tests.features.mcp.support import build_collaborators, handle_method, user_with_every_tool


def collaborators_for(orchestrator):
    registry = ToolRegistry()
    registry.register(StartGenerationTool())
    llm_repository = Mock()
    llm_repository.get_default_configuration.return_value = None
    settings = Mock()
    settings.get_setting.return_value = ""
    settings.get_file_storage_directory.return_value = "/out"
    return build_collaborators(registry, ToolServices(
        llm_repository=llm_repository, generation_orchestrator=orchestrator, plugin_registry=Mock(), settings=settings,
    ))


async def start_over_mcp(collaborators, **kwargs):
    result = await handle_method(
        collaborators, "tools/call",
        {"name": "start_generation", "arguments": {"preset_id": "p", "prompt": "a fox"}}, "user-1", **kwargs,
    )
    return result


@pytest.fixture
def mcp_user(mcp_db):
    user_with_every_tool(mcp_db, "user-1")


class TestStartGenerationOverMcp:
    @pytest.mark.asyncio
    async def test_a_key_without_admin_cannot_start_an_administrator_only_preset_and_nothing_is_queued(self, mcp_user, repo):
        orchestrator, _, _ = make_orchestrator()
        wire(orchestrator, AccountType.ADMIN, admin_only_preset=True)

        result = await start_over_mcp(collaborators_for(orchestrator), is_admin=True)

        assert result["isError"] is True
        assert "restricted to administrators" in result["content"][0]["text"]
        repo.create.assert_not_called()
        orchestrator.backend_registry.select_backend_for_generation.return_value.start_generation.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_key_that_holds_admin_can(self, mcp_user, repo):
        orchestrator, _, _ = make_orchestrator()
        wire(orchestrator, AccountType.ADMIN, admin_only_preset=True)

        result = await start_over_mcp(collaborators_for(orchestrator), is_admin=True, acts_as_admin=True)

        assert result["isError"] is False
        assert json.loads(result["content"][0]["text"])["generation_id"]
        repo.create.assert_called()

    @pytest.mark.asyncio
    async def test_an_ordinary_preset_is_unaffected_either_way(self, mcp_user, repo):
        orchestrator, _, _ = make_orchestrator()
        wire(orchestrator, AccountType.ADMIN, admin_only_preset=False)

        result = await start_over_mcp(collaborators_for(orchestrator), is_admin=True)

        assert result["isError"] is False
        repo.create.assert_called()

    @pytest.mark.asyncio
    async def test_a_plain_owner_is_unchanged(self, mcp_user, repo):
        orchestrator, _, _ = make_orchestrator()
        wire(orchestrator, AccountType.USER, admin_only_preset=True)

        result = await start_over_mcp(collaborators_for(orchestrator))

        assert result["isError"] is False


class TestRouteHandsOverTheEffectiveAuthority:
    def authority(self, client, world, owner, *scopes):
        world.stack.settings.set_setting("mcp_enabled", True)
        world.stack.settings.set_setting("api_key_scopes_allowed", ["read", "generate", "content", "mcp", "admin"])
        _, secret = world.mint(owner, scopes=scopes, days=30 if "admin" in scopes else None)
        world.handle_method.reset_mock()
        response = client.post(
            "/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=world.bearer(secret)
        )
        assert response.status_code == 200
        return world.handle_method.call_args.kwargs

    def test_an_admin_owners_key_without_the_admin_scope_gets_entitlement_but_no_authority(self, client, world):
        kwargs = self.authority(client, world, world.root, "mcp")

        assert kwargs["is_admin"] is True and kwargs["acts_as_admin"] is False

    def test_the_same_owner_with_admin_on_the_key_gets_both(self, client, world):
        kwargs = self.authority(client, world, world.root, "mcp", "admin")

        assert kwargs["is_admin"] is True and kwargs["acts_as_admin"] is True

    def test_a_plain_owners_key_gets_neither(self, client, world):
        kwargs = self.authority(client, world, world.alice, "mcp")

        assert kwargs["is_admin"] is False and kwargs["acts_as_admin"] is False

    def test_the_admin_scope_is_dropped_when_the_ceiling_no_longer_allows_it(self, client, world):
        world.stack.settings.set_setting("mcp_enabled", True)
        world.stack.settings.set_setting("api_key_scopes_allowed", ["read", "generate", "content", "mcp", "admin"])
        _, secret = world.mint(world.root, scopes=("mcp", "admin"), days=30)
        world.stack.settings.set_setting("api_key_scopes_allowed", ["read", "generate", "content", "mcp"])
        world.handle_method.reset_mock()

        client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=world.bearer(secret))

        assert world.handle_method.call_args.kwargs["acts_as_admin"] is False
