from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.api_keys.operations.keys import ApiKeyCollaborators
from src.features.api_keys.operations.resolver import ApiKeyResolver
from src.features.forms.binding import BoundForm
from src.features.generation.repository import GenerationRepository
from src.features.llm.tools.base import ToolContext, ToolServices
from src.features.llm.tools.builtin.run_generation_tool import RunGenerationTool
from src.features.llm.tools.builtin.start_generation_tool import StartGenerationTool
from src.features.llm.tools.registry import ToolRegistry
from src.features.mcp import routes as mcp_routes
from src.features.mcp.protocol import McpToolCollaborators
from src.features.mcp.tool_policy import POLICY_ALL_ON
from src.features.mcp.tool_repository import McpToolRepository
from src.platform.security import current_user
from src.platform.settings.repository import SettingRepository
from src.platform.settings.settings import Settings
from tests.features.api_keys.conftest import Stack
from tests.features.api_keys.support import World
from tests.features.generation.test_orchestrator_fixed_prompt import FILM, make_orchestrator

FORGED = {"kind": "editor", "edit_id": "forged", "run_id": "er_forged"}


def passthrough(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None, **_):
    return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or "custom", coercions=[], stripped=[])


@pytest.fixture
def rig(keys_db):
    stack = Stack()
    world = World(stack)
    stack.settings.set_setting("mcp_enabled", True)
    auth = Mock()
    auth.get_user_from_token.side_effect = lambda token: world.sessions.get(token)
    previous = (current_user._auth, current_user._key_resolver)
    current_user.set_auth(auth)
    current_user.set_key_resolver(ApiKeyResolver(stack.keys, stack.access, stack.users, stack.settings))
    current_user._key_failures.reset("testclient")

    orchestrator, _, _ = make_orchestrator(FILM)
    orchestrator.preset_template_loader.load_preset_by_id = lambda preset_id: Mock(engine="native", vars={})
    registry = ToolRegistry()
    registry.register(StartGenerationTool())
    registry.register(RunGenerationTool())
    llm_repository = Mock()
    llm_repository.get_default_configuration.return_value = None
    collaborators = McpToolCollaborators(
        tool_registry=registry,
        tool_repository=McpToolRepository(),
        settings=Settings(SettingRepository()),
        services=ToolServices(
            llm_repository=llm_repository, generation_orchestrator=orchestrator, settings=Mock(get_setting=Mock(return_value=""))
        ),
    )
    for user in (world.alice, world.bob):
        McpToolRepository().apply_default(user.id, POLICY_ALL_ON, {})
    container = SimpleNamespace(
        api_key_collaborators=ApiKeyCollaborators(
            keys=stack.keys, events=stack.events, access=stack.access, settings=stack.settings
        ),
        user_repository=stack.users,
        mcp_tool_collaborators=collaborators,
    )
    app = FastAPI()
    app.include_router(mcp_routes.build_router(container))
    ids = iter(f"gen-{n}" for n in range(1, 50))
    with patch("src.features.generation.orchestrator.bind_form", side_effect=passthrough), \
            patch("src.features.generation.orchestrator.generate_ulid", side_effect=lambda: next(ids)), \
            TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(world=world, stack=stack, client=client, orchestrator=orchestrator)
    current_user._auth, current_user._key_resolver = previous
    current_user._key_failures.reset("testclient")


def call(rig, secret, name, arguments):
    return rig.client.post(
        "/api/mcp",
        headers=rig.world.bearer(secret),
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
    ).json()


def started(response):
    import json

    return json.loads(response["result"]["content"][0]["text"])["generation_id"]


def test_start_generation_over_mcp_records_the_key_as_the_origin(rig):
    key, secret = rig.world.mint(rig.world.alice, scopes=("mcp",), name="claude desktop")

    response = call(rig, secret, "start_generation", {"preset_id": "p", "prompt": "a fox"})

    stored = GenerationRepository().get_by_id(started(response))
    assert stored.origin == {"kind": "mcp", "key_id": key.id, "key_name": "claude desktop"}
    assert secret not in str(stored.origin)


def test_an_origin_in_the_tool_arguments_is_ignored(rig):
    key, secret = rig.world.mint(rig.world.alice, scopes=("mcp",), name="agent")

    response = call(rig, secret, "start_generation", {"preset_id": "p", "prompt": "a fox", "origin": FORGED})

    assert GenerationRepository().get_by_id(started(response)).origin["kind"] == "mcp"


def test_a_generation_that_fails_at_once_still_has_its_origin(rig):
    key, secret = rig.world.mint(rig.world.alice, scopes=("mcp",), name="agent")
    rig.orchestrator.backend_registry.get_backend.return_value.start_generation.side_effect = RuntimeError("boom")
    rig.orchestrator.backend_registry.select_backend_for_generation.return_value.start_generation.side_effect = RuntimeError("boom")

    response = call(rig, secret, "start_generation", {"preset_id": "p", "prompt": "a fox"})

    assert response["result"]["isError"] is True
    stored = GenerationRepository().get_by_id("gen-1")
    assert stored.origin == {"kind": "mcp", "key_id": key.id, "key_name": "agent"}


def test_run_generation_is_not_an_mcp_tool(rig):
    _, secret = rig.world.mint(rig.world.alice, scopes=("mcp",))

    response = call(rig, secret, "run_generation", {})

    assert "error" in response


@pytest.mark.asyncio
async def test_run_generation_carries_the_context_origin_to_the_request():
    from tests.fixtures.fake_tab_drafts import drafts_context
    from unittest.mock import AsyncMock

    orchestrator = AsyncMock()
    orchestrator.start_generation.return_value = {"generation_id": "g", "status": {"status": "pending"}}
    context = drafts_context(
        {"form_state": {"preset": "p", "mode": "txt2img", "form_data": {"prompt": "x"}}}, user_id="user-1"
    )
    context.generation_orchestrator = orchestrator
    context.origin = {"kind": "mcp", "key_id": "k", "key_name": "n"}

    await RunGenerationTool().execute_confirmed(context)

    assert orchestrator.start_generation.await_args.kwargs["request"]._origin == context.origin


def test_a_tool_context_has_no_origin_unless_one_is_given():
    assert ToolContext(user_id="u").origin is None
