import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from src.features.api_keys.repository import ApiKeyRepository
from src.features.downloads.exceptions import DownloadQueueException
from src.features.elixirs.repository import ElixirRepository
from src.features.generation.exceptions import GenerationDeleteFailedException, GenerationNotFoundException
from src.features.llm.tools.base import ToolServices
from src.features.llm.tools.builtin import admin_domain_tools, elixir_domain_tools, register_builtin_tools
from src.features.llm.tools.domains import DomainServices
from src.features.llm.tools.registry import ToolRegistry
from src.features.mcp.tool_policy import POLICY_ALL_ON, POLICY_READ_ONLY_ON
from src.features.mcp.tool_repository import McpToolRepository
from src.features.notifications.collaborators import NotificationCollaborators
from src.features.notifications.repository import NotificationRepository
from src.features.notifications.routes import NotificationController
from src.features.sessions.dto import Session
from src.features.sessions.repository import SessionRepository
from src.platform.http.base_controller import APIResponse
from src.platform.security.user import AccountType
from tests.features.mcp.support import KEY_ID, api_key, build_collaborators, handle_method
from tests.fixtures.seed import seed_user

ALICE = "alice"
BOB = "bob"
ADMIN = {"is_admin": True, "acts_as_admin": True}
OWNER_ONLY_ADMIN = {"is_admin": True, "acts_as_admin": False}
DESTRUCTIVE = ("delete_generation", "delete_library_item")
READS = (
    "list_elixirs", "describe_elixir", "get_elixir_run", "list_automations", "get_automation_status",
    "search_provider_models", "get_download_status", "list_inspirations", "list_sessions", "get_session",
    "list_notifications",
)
WRITES = ("run_elixir", "run_automation", "download_model", "publish_inspiration", "mark_notifications_read")


def ok(data=None, message=None):
    return APIResponse(success=True, data=data, message=message)


def refused(message):
    return APIResponse(success=False, error="refused", message=message)


class FakeHistory:
    def __init__(self):
        self.deleted = []
        self.owners = {"gen_1": ALICE}
        self.details = {}
        self.fail_with = None

    def delete(self, generation_id, user_id):
        if self.fail_with:
            raise self.fail_with
        if self.owners.get(generation_id) != user_id:
            raise GenerationNotFoundException(generation_id)
        self.deleted.append(generation_id)
        return {"files_deleted_fs": 2, "files_deleted_db": 2}

    async def get_by_id_async(self, generation_id, user_id, include_files=True, include_batch=False):
        if self.owners.get(generation_id) != user_id:
            raise GenerationNotFoundException(generation_id)
        return self.details[generation_id]


@pytest.fixture
def world(mcp_db):
    for user_id in (ALICE, BOB):
        seed_user(mcp_db, user_id)
        McpToolRepository().apply_default(user_id, POLICY_ALL_ON, {})
    ApiKeyRepository().create(api_key(KEY_ID, ALICE))
    McpToolRepository().apply_ceiling({name: True for name in DESTRUCTIVE}, None)
    for user_id in (ALICE, BOB):
        McpToolRepository().apply_default(user_id, POLICY_ALL_ON, {name: True for name in DESTRUCTIVE})

    notifications = NotificationController(NotificationCollaborators(
        repository=NotificationRepository(), users=Mock(), plugins=Mock(), connections=Mock(), settings=Mock(),
    ))
    automation = Mock()
    for name in ("list_automations", "run_now", "get_run", "list_runs"):
        setattr(automation, name, AsyncMock(return_value=ok({})))
    library = Mock(delete_item=AsyncMock(return_value=ok({"id": "item-1", "deleted": True})))
    inspirations = Mock(
        list_feed=AsyncMock(return_value=ok({"items": [], "total": 0})),
        publish=AsyncMock(return_value=ok({"inspiration": {"id": "insp-1"}})),
    )
    downloads = Mock(queue_model_download=AsyncMock(), get_download=Mock(), list_downloads=Mock())
    elixirs = SimpleNamespace(repository=ElixirRepository(), coordinator=Mock())
    history = FakeHistory()
    domains = DomainServices(
        automation=automation, library=library, sessions=SessionRepository(), notifications=notifications,
        inspirations=inspirations, downloads=downloads, elixirs=elixirs,
    )
    registry = ToolRegistry()
    register_builtin_tools(registry)
    llm_repository = Mock()
    llm_repository.get_default_configuration.return_value = None
    collaborators = build_collaborators(registry, ToolServices(
        llm_repository=llm_repository, generation_history_facade=history, domains=domains,
    ))
    return SimpleNamespace(c=collaborators, domains=domains, history=history, db=mcp_db)


async def call(world, name, arguments=None, user_id=ALICE, **kwargs):
    return await handle_method(
        world.c, "tools/call", {"name": name, "arguments": arguments or {}}, user_id, key_id=KEY_ID, **kwargs,
    )


def body(result):
    assert result["isError"] is False, result["content"][0]["text"]
    return json.loads(result["content"][0]["text"])


def error(result):
    assert result["isError"] is True
    return result["content"][0]["text"].removeprefix("Error: ")


async def listed(world, user_id=ALICE):
    result = await handle_method(world.c, "tools/list", {}, user_id, key_id=KEY_ID)
    return {tool["name"] for tool in result["tools"]}


class TestPolicyLevels:
    @pytest.mark.asyncio
    async def test_everything_on_lists_every_new_tool(self, world):
        assert set(READS + WRITES + DESTRUCTIVE) <= await listed(world)

    @pytest.mark.asyncio
    async def test_a_read_only_default_keeps_only_the_reads_and_what_was_chosen_explicitly(self, world):
        McpToolRepository().apply_default(ALICE, POLICY_READ_ONLY_ON, {})

        names = await listed(world)

        assert set(READS) <= names
        assert names.isdisjoint(WRITES)

    @pytest.mark.asyncio
    async def test_a_read_only_ceiling_keeps_only_the_reads_even_for_explicit_choices_on_the_rest(self, world):
        world.c.settings.set_setting("mcp_tools_ceiling_policy", "read_only_on")

        names = await listed(world)

        assert set(READS) <= names
        assert names.isdisjoint(WRITES)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", READS + WRITES + DESTRUCTIVE)
    async def test_a_key_that_switched_the_tool_off_is_refused(self, world, name):
        McpToolRepository().apply_key_overrides(KEY_ID, [name], [])

        assert name not in await listed(world)
        with pytest.raises(Exception, match="turned off for this key"):
            await call(world, name, {})

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", DESTRUCTIVE)
    async def test_the_delete_tools_are_off_until_the_admin_and_the_user_both_choose(self, world, name):
        McpToolRepository().apply_ceiling({name: None}, None)

        with pytest.raises(Exception, match="turned off by your administrator"):
            await call(world, name, {})


class TestNotifications:
    def seed(self, user_id, title):
        return NotificationRepository().create(user_id=user_id, category="system", level="info", title=title)

    @pytest.mark.asyncio
    async def test_a_user_reads_only_their_own_notifications_with_the_unread_count(self, world):
        self.seed(ALICE, "mine")
        self.seed(BOB, "his")

        data = body(await call(world, "list_notifications"))

        assert [n["title"] for n in data["notifications"]] == ["mine"]
        assert data["unread_count"] == 1

    @pytest.mark.asyncio
    async def test_unread_only_hides_what_was_read(self, world):
        first, _ = self.seed(ALICE, "old"), self.seed(ALICE, "new")
        NotificationRepository().mark_read(first.id, ALICE)

        data = body(await call(world, "list_notifications", {"unread_only": True}))

        assert [n["title"] for n in data["notifications"]] == ["new"]

    @pytest.mark.asyncio
    async def test_the_limit_is_capped(self, world):
        for index in range(3):
            self.seed(ALICE, str(index))

        data = body(await call(world, "list_notifications", {"limit": 1}))

        assert len(data["notifications"]) == 1

    @pytest.mark.asyncio
    async def test_a_non_numeric_limit_is_refused(self, world):
        assert "'limit' must be an integer" in error(await call(world, "list_notifications", {"limit": "many"}))

    @pytest.mark.asyncio
    async def test_marking_one_read_changes_only_that_one(self, world):
        mine, other = self.seed(ALICE, "a"), self.seed(ALICE, "b")

        body(await call(world, "mark_notifications_read", {"notification_id": mine.id}))

        unread = NotificationRepository().list_for_user(ALICE, unread_only=True)
        assert [n.id for n in unread] == [other.id]

    @pytest.mark.asyncio
    async def test_someone_elses_notification_cannot_be_marked(self, world):
        his = self.seed(BOB, "his")

        result = await call(world, "mark_notifications_read", {"notification_id": his.id})

        assert error(result) == "Notification not found"
        assert NotificationRepository().unread_count(BOB) == 1

    @pytest.mark.asyncio
    async def test_marking_all_read_touches_only_the_callers(self, world):
        self.seed(ALICE, "a")
        self.seed(BOB, "b")

        body(await call(world, "mark_notifications_read", {"all": True}))

        assert NotificationRepository().unread_count(ALICE) == 0
        assert NotificationRepository().unread_count(BOB) == 1

    @pytest.mark.asyncio
    async def test_naming_nothing_is_refused(self, world):
        assert "notification_id" in error(await call(world, "mark_notifications_read", {}))


class TestSessions:
    def seed(self, user_id, session_id, preset="p1", name="s", data=None):
        SessionRepository().create(Session(id=session_id, user_id=user_id, preset_id=preset, name=name, data=data or {}))

    @pytest.mark.asyncio
    async def test_the_list_is_the_callers_sessions_for_the_preset_without_their_payloads(self, world):
        self.seed(ALICE, "s-1", name="warm", data={"txt2img": {"prompt": "fox"}})
        self.seed(ALICE, "s-2", preset="other")
        self.seed(BOB, "s-3")

        data = body(await call(world, "list_sessions", {"preset_id": "p1"}))

        assert [s["id"] for s in data["sessions"]] == ["s-1"]
        assert "data" not in data["sessions"][0]
        assert "user_id" not in data["sessions"][0]

    @pytest.mark.asyncio
    async def test_a_session_reads_with_its_values_and_modes(self, world):
        self.seed(ALICE, "s-1", data={"txt2img": {"prompt": "fox"}, "img2img": {"prompt": "owl"}})

        data = body(await call(world, "get_session", {"session_id": "s-1"}))

        assert data["data"]["txt2img"] == {"prompt": "fox"}
        assert data["modes"] == ["txt2img", "img2img"]
        assert data["preset_id"] == "p1"

    @pytest.mark.asyncio
    async def test_another_users_session_is_not_found(self, world):
        self.seed(ALICE, "s-1")

        assert error(await call(world, "get_session", {"session_id": "s-1"}, user_id=BOB)) == "Session not found"

    @pytest.mark.asyncio
    async def test_an_unknown_session_is_not_found(self, world):
        assert error(await call(world, "get_session", {"session_id": "nope"})) == "Session not found"

    @pytest.mark.asyncio
    async def test_a_missing_preset_is_refused(self, world):
        assert "'preset_id' is required" in error(await call(world, "list_sessions", {}))


class TestElixirs:
    def seed(self, user_id, name="Sharpen"):
        return ElixirRepository().create(user_id, name, "review", {"schema_version": 1, "nodes": [{"id": "n1", "kind": "media", "title": "T"}], "edges": []})

    @pytest.mark.asyncio
    async def test_the_list_is_the_callers_elixirs(self, world):
        mine = self.seed(ALICE)
        self.seed(BOB, "His")

        data = body(await call(world, "list_elixirs"))

        assert [e["id"] for e in data["elixirs"]] == [mine.id]

    @pytest.mark.asyncio
    async def test_describing_one_gives_its_nodes_and_revision(self, world):
        record = self.seed(ALICE)

        data = body(await call(world, "describe_elixir", {"elixir_id": record.id}))

        assert (data["name"], data["revision"], data["run_mode"]) == ("Sharpen", 1, "review")
        assert data["nodes"] == [{"id": "n1", "kind": "media"}]

    @pytest.mark.asyncio
    async def test_another_users_elixir_does_not_exist(self, world):
        record = self.seed(ALICE)

        assert error(await call(world, "describe_elixir", {"elixir_id": record.id}, user_id=BOB)) == "That elixir does not exist."

    @pytest.mark.asyncio
    async def test_running_brews_the_current_revision_as_the_caller(self, world, monkeypatch):
        record = self.seed(ALICE)
        brew = Mock(return_value={"id": "run-1", "status": "pending"})
        monkeypatch.setattr(elixir_domain_tools, "begin_brew", brew)

        data = body(await call(world, "run_elixir", {"elixir_id": record.id, "idempotency_key": "key-12345"}))

        assert data["id"] == "run-1"
        elixirs, user, elixir_id, revision, run_mode, templates, hashes, key = brew.call_args.args
        assert (user.id, elixir_id, revision, run_mode, key) == (ALICE, record.id, 1, None, "key-12345")
        assert (templates, hashes) == ({}, None)
        assert user.account_type == AccountType.USER

    @pytest.mark.asyncio
    async def test_a_key_is_made_up_when_none_is_given(self, world, monkeypatch):
        record = self.seed(ALICE)
        brew = Mock(return_value={"id": "run-1"})
        monkeypatch.setattr(elixir_domain_tools, "begin_brew", brew)

        await call(world, "run_elixir", {"elixir_id": record.id})

        assert len(brew.call_args.args[-1]) >= 8

    @pytest.mark.asyncio
    async def test_a_stale_revision_is_refused_by_the_brew_not_hidden(self, world, monkeypatch):
        record = self.seed(ALICE)
        monkeypatch.setattr(elixir_domain_tools, "begin_brew", Mock(side_effect=HTTPException(
            status_code=409, detail={"code": "revision_conflict", "message": "This elixir changed since you opened it."},
        )))

        result = await call(world, "run_elixir", {"elixir_id": record.id, "expected_revision": 9})

        assert error(result) == "This elixir changed since you opened it."

    @pytest.mark.asyncio
    async def test_someone_elses_elixir_is_not_brewed(self, world, monkeypatch):
        record = self.seed(ALICE)
        brew = Mock()
        monkeypatch.setattr(elixir_domain_tools, "begin_brew", brew)

        result = await call(world, "run_elixir", {"elixir_id": record.id}, user_id=BOB)

        assert error(result) == "That elixir does not exist."
        brew.assert_not_called()

    @pytest.mark.asyncio
    async def test_an_unknown_run_mode_is_refused(self, world):
        assert "'run_mode'" in error(await call(world, "run_elixir", {"elixir_id": "x", "run_mode": "yolo"}))

    @pytest.mark.asyncio
    async def test_a_brew_is_read_through_the_callers_run_store(self, world, monkeypatch):
        store = world.domains.elixirs.coordinator.store
        store.get_run = Mock(return_value={"id": "run-1"})
        monkeypatch.setattr(elixir_domain_tools, "run_detail", lambda coordinator, user, run: {"id": run["id"], "user": user.id})

        data = body(await call(world, "get_elixir_run", {"run_id": "run-1"}, user_id=BOB))

        store.get_run.assert_called_once_with("run-1", BOB)
        assert data == {"id": "run-1", "user": BOB}

    @pytest.mark.asyncio
    async def test_an_unknown_brew_does_not_exist(self, world):
        world.domains.elixirs.coordinator.store.get_run = Mock(return_value=None)

        assert error(await call(world, "get_elixir_run", {"run_id": "x"}, user_id=BOB)) == "That brew does not exist."


ADMIN_ONLY = (
    ("list_automations", {}),
    ("run_automation", {"automation_id": "a1"}),
    ("get_automation_status", {"automation_id": "a1"}),
    ("search_provider_models", {"query": "flux"}),
    ("download_model", {"url": "https://example.test/m.safetensors"}),
    ("get_download_status", {}),
)


class TestAdministratorOnly:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("name,arguments", ADMIN_ONLY)
    async def test_an_ordinary_user_is_refused_and_nothing_is_touched(self, world, name, arguments):
        result = await call(world, name, arguments)

        assert "administrator authority" in error(result)
        world.domains.automation.run_now.assert_not_called()
        world.domains.downloads.queue_model_download.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name,arguments", ADMIN_ONLY)
    async def test_an_administrator_whose_key_lacks_the_admin_scope_is_refused(self, world, name, arguments):
        result = await call(world, name, arguments, **OWNER_ONLY_ADMIN)

        assert "administrator authority" in error(result)


class TestAutomation:
    @pytest.mark.asyncio
    async def test_the_list_leaves_the_graph_out(self, world):
        world.domains.automation.list_automations.return_value = ok([{"id": "a1", "name": "Nightly", "enabled": True, "graph": {"nodes": [1]}}])

        data = body(await call(world, "list_automations", **ADMIN))

        assert data == {"automations": [{"id": "a1", "name": "Nightly", "enabled": True}]}
        assert world.domains.automation.list_automations.call_args.args[0].account_type == AccountType.ADMIN

    @pytest.mark.asyncio
    async def test_running_triggers_it_with_the_given_node_and_payload(self, world):
        world.domains.automation.run_now.return_value = ok({"run_id": "r1"})

        data = body(await call(world, "run_automation", {"automation_id": "a1", "node_id": "t", "payload": {"x": 1}}, **ADMIN))

        assert data == {"run_id": "r1"}
        automation_id, request, user = world.domains.automation.run_now.call_args.args
        assert (automation_id, request.node_id, request.payload, user.id) == ("a1", "t", {"x": 1}, ALICE)

    @pytest.mark.asyncio
    async def test_a_refusal_from_the_controller_reaches_the_client(self, world):
        world.domains.automation.run_now.return_value = refused("Automation not found or has no trigger node")

        assert error(await call(world, "run_automation", {"automation_id": "a1"}, **ADMIN)) == "Automation not found or has no trigger node"

    @pytest.mark.asyncio
    async def test_status_with_a_run_reads_that_run(self, world):
        world.domains.automation.get_run.return_value = ok({"id": "r1", "status": "completed"})

        data = body(await call(world, "get_automation_status", {"automation_id": "a1", "run_id": "r1"}, **ADMIN))

        assert data["status"] == "completed"
        assert world.domains.automation.get_run.call_args.args[:2] == ("a1", "r1")

    @pytest.mark.asyncio
    async def test_status_without_a_run_lists_the_latest_with_a_bounded_limit(self, world):
        world.domains.automation.list_runs.return_value = ok([])

        body(await call(world, "get_automation_status", {"automation_id": "a1", "limit": 9999}, **ADMIN))

        assert world.domains.automation.list_runs.call_args.kwargs["limit"] == admin_domain_tools.RUNS_MAX


class FakeProvidersRegistry:
    def __init__(self):
        self.searched = []

    def get_providers_with_capability(self, capability):
        return [SimpleNamespace(get_metadata=lambda: SimpleNamespace(id="civitai"))]

    async def search_models(self, provider_id, query, model_type=None, limit=20, offset=0):
        self.searched.append((provider_id, query, model_type, limit))
        return [
            SimpleNamespace(nsfw=False, to_dict=lambda: {"provider_id": provider_id, "name": "Safe", "nsfw": False, "thumbnail_url": "t", "download_url": "u"}),
            SimpleNamespace(nsfw=True, to_dict=lambda: {"provider_id": provider_id, "name": "Adult", "nsfw": True, "thumbnail_url": "t", "download_url": "u"}),
        ]


class TestDownloads:
    @pytest.fixture
    def providers(self, monkeypatch):
        registry = FakeProvidersRegistry()
        monkeypatch.setattr(admin_domain_tools, "ensure_providers_discovered", AsyncMock(return_value=registry))
        return registry

    @pytest.mark.asyncio
    async def test_search_asks_every_provider_that_can_and_drops_adult_results(self, world, providers):
        data = body(await call(world, "search_provider_models", {"query": "flux", "model_type": "lora"}, **ADMIN))

        assert providers.searched == [("civitai", "flux", "lora", 10)]
        assert data == {"results": [{"provider_id": "civitai", "name": "Safe", "download_url": "u"}]}

    @pytest.mark.asyncio
    async def test_search_can_name_one_provider(self, world, providers):
        body(await call(world, "search_provider_models", {"query": "flux", "provider_id": "hf"}, **ADMIN))

        assert providers.searched[0][0] == "hf"

    @pytest.mark.asyncio
    async def test_search_needs_a_query(self, world, providers):
        assert "'query' is required" in error(await call(world, "search_provider_models", {}, **ADMIN))

    @pytest.mark.asyncio
    async def test_download_queues_the_url_as_the_caller(self, world):
        download = Mock()
        download.to_dict.return_value = {"id": "d1", "status": "queued"}
        world.domains.downloads.queue_model_download.return_value = download

        data = body(await call(world, "download_model", {
            "url": "https://example.test/m.safetensors", "model_type": "lora", "tags": ["x"], "ignored": 1,
        }, **ADMIN))

        assert data == {"id": "d1", "status": "queued"}
        kwargs = world.domains.downloads.queue_model_download.call_args.kwargs
        assert (kwargs["url"], kwargs["model_type"], kwargs["tags"], kwargs["created_by"]) == (
            "https://example.test/m.safetensors", "lora", ["x"], ALICE,
        )

    @pytest.mark.asyncio
    async def test_download_needs_a_url(self, world):
        assert "'url' is required" in error(await call(world, "download_model", {}, **ADMIN))
        world.domains.downloads.queue_model_download.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_refused_download_says_why(self, world):
        world.domains.downloads.queue_model_download.side_effect = DownloadQueueException("Destination is not allowed")

        assert error(await call(world, "download_model", {"url": "https://x.test/m"}, **ADMIN)) == "Destination is not allowed"

    @pytest.mark.asyncio
    async def test_status_of_one_download(self, world):
        world.domains.downloads.get_download.return_value = SimpleNamespace(to_dict=lambda: {"id": "d1", "progress": 40})

        assert body(await call(world, "get_download_status", {"download_id": "d1"}, **ADMIN)) == {"id": "d1", "progress": 40}

    @pytest.mark.asyncio
    async def test_status_lists_recent_downloads_with_a_bounded_limit(self, world):
        world.domains.downloads.list_downloads.return_value = {"downloads": [], "total": 0}

        body(await call(world, "get_download_status", {"status": "failed", "limit": 9999}, **ADMIN))

        assert world.domains.downloads.list_downloads.call_args.kwargs == {
            "status": "failed", "limit": admin_domain_tools.DOWNLOADS_MAX,
        }


class TestDeletes:
    @pytest.mark.asyncio
    async def test_a_generation_of_the_callers_is_deleted(self, world):
        data = body(await call(world, "delete_generation", {"generation_id": "gen_1"}))

        assert data == {"generation_id": "gen_1", "deleted": True, "files_removed": 2}
        assert world.history.deleted == ["gen_1"]

    @pytest.mark.asyncio
    async def test_someone_elses_generation_is_not_deleted(self, world):
        result = await call(world, "delete_generation", {"generation_id": "gen_1"}, user_id=BOB)

        assert "No generation 'gen_1' of yours" in error(result)
        assert world.history.deleted == []

    @pytest.mark.asyncio
    async def test_a_plugin_veto_is_reported(self, world):
        world.history.fail_with = GenerationDeleteFailedException("Deletion blocked by plugin")

        assert error(await call(world, "delete_generation", {"generation_id": "gen_1"})) == "Deletion blocked by plugin"

    @pytest.mark.asyncio
    async def test_a_library_item_is_deleted_as_the_caller(self, world):
        data = body(await call(world, "delete_library_item", {"item_id": "item-1"}))

        assert data == {"id": "item-1", "deleted": True}
        item_id, user = world.domains.library.delete_item.call_args.args
        assert (item_id, user.id) == ("item-1", ALICE)

    @pytest.mark.asyncio
    async def test_a_library_item_that_is_not_the_callers_is_refused_by_the_library(self, world):
        world.domains.library.delete_item.side_effect = HTTPException(
            status_code=404, detail={"error": "not_found", "message": "Library item not found"},
        )

        assert error(await call(world, "delete_library_item", {"item_id": "item-9"}, user_id=BOB)) == "Library item not found"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name,argument", [("delete_generation", "generation_id"), ("delete_library_item", "item_id")])
    async def test_an_id_is_required(self, world, name, argument):
        assert f"'{argument}' is required" in error(await call(world, name, {}))


class TestSharing:
    @pytest.mark.asyncio
    async def test_the_feed_is_read_as_the_caller_with_bounded_paging(self, world):
        body(await call(world, "list_inspirations", {"query": "fox", "saved": True, "limit": 9999, "offset": 5}))

        user, query, limit, offset, collection, author, saved, descendants, unsorted = world.domains.inspirations.list_feed.call_args.args
        assert (user.id, query, limit, offset, saved) == (ALICE, "fox", 50, 5, True)

    @pytest.mark.asyncio
    async def test_publishing_shares_every_final_file_by_default(self, world):
        world.history.details["gen_1"] = {"files": [
            {"file_path": "generations/d/gen_1/0.png", "is_final": True},
            {"file_path": "generations/d/gen_1/draft.png", "is_final": False},
        ]}

        body(await call(world, "publish_inspiration", {"generation_id": "gen_1", "title": "Fox"}))

        request, user = world.domains.inspirations.publish.call_args.args
        assert (request.filenames, request.title, request.generation_id, user.id) == (["0.png"], "Fox", "gen_1", ALICE)

    @pytest.mark.asyncio
    async def test_publishing_can_name_the_files(self, world):
        body(await call(world, "publish_inspiration", {"generation_id": "gen_1", "title": "Fox", "filenames": ["1.png"], "description": "d"}))

        request, _ = world.domains.inspirations.publish.call_args.args
        assert (request.filenames, request.description) == (["1.png"], "d")

    @pytest.mark.asyncio
    async def test_someone_elses_generation_is_not_published(self, world):
        result = await call(world, "publish_inspiration", {"generation_id": "gen_1", "title": "Fox"}, user_id=BOB)

        assert "No generation 'gen_1' of yours" in error(result)
        world.domains.inspirations.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_title_is_required(self, world):
        assert "'title' are required" in error(await call(world, "publish_inspiration", {"generation_id": "gen_1"}))


class TestWhenAServiceIsMissing:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("name,arguments", [("list_elixirs", {}), ("list_notifications", {}), ("get_session", {"session_id": "x"})])
    async def test_the_tool_says_it_is_not_available(self, world, name, arguments):
        bare = build_collaborators(world.c.tool_registry, ToolServices(llm_repository=world.c.services.llm_repository))

        result = await handle_method(bare, "tools/call", {"name": name, "arguments": arguments}, ALICE, key_id=KEY_ID)

        assert error(result) == "This is not available on this server."
