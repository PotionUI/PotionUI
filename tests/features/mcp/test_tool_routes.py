from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.api_keys import routes as api_key_routes
from src.features.llm.tools.base import BaseTool, ToolResult
from src.features.llm.tools.registry import ToolRegistry
from src.features.mcp import routes as mcp_routes
from src.features.mcp.operations.tools import counts_for_keys
from src.features.mcp.tool_repository import McpToolRepository
from tests.features.mcp.support import build_collaborators


def _stub(tool_name, read_only, unavailable=None, spends=False):
    spends_flag = spends

    class _Stub(BaseTool):
        name = tool_name
        group = "Group"
        user_description = f"{tool_name} description"
        mcp = unavailable is None
        spends = spends_flag
        mcp_unavailable = unavailable

        @property
        def description(self):
            return tool_name

        @property
        def parameters(self):
            return {"type": "object", "properties": {}, "required": []}

        async def execute(self, context, **kwargs):
            return ToolResult(success=True, data="")

    _Stub.read_only = read_only
    return _Stub()


@pytest.fixture
def tools_client(world):
    registry = ToolRegistry()
    for tool in (
        _stub("read_a", True), _stub("read_b", True), _stub("write_c", False), _stub("write_d", False),
        _stub("live_e", True, "Needs the live Generate form"),
    ):
        registry.register(tool)
    collaborators = build_collaborators(registry)
    container = SimpleNamespace(
        api_key_collaborators=world.stack.collaborators,
        user_repository=world.stack.users,
        mcp_tool_collaborators=collaborators,
        mcp_tool_counts=lambda pairs: counts_for_keys(collaborators, pairs),
    )
    app = FastAPI()
    app.include_router(api_key_routes.build_router(container))
    app.include_router(api_key_routes.build_admin_router(container))
    app.include_router(mcp_routes.build_router(container))
    app.include_router(mcp_routes.build_admin_router(container))
    world.collaborators = collaborators
    world.repository = McpToolRepository()
    world.http = TestClient(app, raise_server_exceptions=False)
    world.stack.settings.set_setting("mcp_enabled", True)
    return world.http


def states(response):
    return {tool["name"]: tool["state"] for tool in response.json()["data"]["tools"]}


def error(response):
    return response.json()["detail"]["error"]


def mine(client, world, name="alice"):
    return client.get("/api/mcp/tools", headers=world.session(name))


def patch(client, world, body, name="alice"):
    return client.patch("/api/mcp/tools", json=body, headers=world.session(name))


class TestUserDefaultListing:
    def test_a_fresh_user_sees_reads_on_and_writes_off_with_everything_unreviewed(self, tools_client, world):
        response = mine(tools_client, world)

        assert response.status_code == 200
        data = response.json()["data"]
        assert (data["level"], data["key_id"], data["policy"], data["ceiling_policy"]) == (
            "default", None, "read_only_on", "all_on",
        )
        assert states(response) == {
            "read_a": "on", "read_b": "on", "write_c": "off", "write_d": "off", "live_e": "unavailable",
        }
        assert data["summary"] == {
            "exposed": 2, "total": 4, "writes_exposed": 0, "unavailable": 1, "locked": 0, "unreviewed": 4,
        }
        assert all(tool["reviewed"] is False for tool in data["tools"])

    def test_a_tool_row_carries_everything_the_screen_draws(self, tools_client, world):
        tool = {t["name"]: t for t in mine(tools_client, world).json()["data"]["tools"]}["live_e"]

        assert tool == {
            "name": "live_e", "label": "Live E", "group": "Group", "description": "live_e description",
            "writes": False, "spends": False, "destructive": False, "unavailable_reason": "Needs the live Generate form",
            "state": "unavailable",
            "reviewed": False,
        }

    def test_the_listing_is_the_callers_own(self, tools_client, world):
        patch(tools_client, world, {"policy": "all_on"})

        assert states(mine(tools_client, world, "alice"))["write_c"] == "on"
        assert states(mine(tools_client, world, "bob"))["write_c"] == "off"

    def test_it_needs_a_login(self, tools_client):
        assert tools_client.get("/api/mcp/tools").status_code == 401


class TestUserDefaultPatch:
    def test_the_policy_changes_every_tool_without_a_row(self, tools_client, world):
        response = patch(tools_client, world, {"policy": "all_on"})

        assert response.status_code == 200
        assert response.json()["data"]["policy"] == "all_on"
        assert states(response)["write_c"] == "on"

    def test_one_override_turns_one_tool_on_and_marks_it_reviewed(self, tools_client, world):
        response = patch(tools_client, world, {"overrides": {"write_c": True}})

        tools = {t["name"]: t for t in response.json()["data"]["tools"]}
        assert (tools["write_c"]["state"], tools["write_c"]["reviewed"]) == ("on", True)
        assert tools["write_d"]["state"] == "off"
        assert response.json()["data"]["summary"]["writes_exposed"] == 1

    def test_false_turns_a_reading_tool_off(self, tools_client, world):
        assert states(patch(tools_client, world, {"overrides": {"read_a": False}}))["read_a"] == "off"

    def test_null_clears_the_row_and_the_tool_is_unreviewed_again(self, tools_client, world):
        patch(tools_client, world, {"overrides": {"write_c": True}})

        response = patch(tools_client, world, {"overrides": {"write_c": None}})

        tools = {t["name"]: t for t in response.json()["data"]["tools"]}
        assert (tools["write_c"]["state"], tools["write_c"]["reviewed"]) == ("off", False)
        assert world.repository.default_overrides(world.alice.id) == {}

    def test_a_map_changes_several_tools_at_once(self, tools_client, world):
        response = patch(tools_client, world, {"policy": "all_on", "overrides": {"read_a": False, "write_d": False}})

        assert states(response) == {
            "read_a": "off", "read_b": "on", "write_c": "on", "write_d": "off", "live_e": "unavailable",
        }

    def test_a_name_outside_the_catalogue_is_a_400_and_changes_nothing(self, tools_client, world):
        response = patch(tools_client, world, {"policy": "all_on", "overrides": {"read_a": False, "nope": True}})

        assert (response.status_code, error(response)) == (400, "unknown_tool")
        assert states(mine(tools_client, world))["read_a"] == "on"
        assert mine(tools_client, world).json()["data"]["policy"] == "read_only_on"

    def test_an_unknown_policy_is_a_400(self, tools_client, world):
        response = patch(tools_client, world, {"policy": "most_on"})

        assert (response.status_code, error(response)) == (400, "invalid_policy")

    def test_a_non_boolean_override_is_refused(self, tools_client, world):
        assert patch(tools_client, world, {"overrides": {"read_a": "yes"}}).status_code == 422

    def test_an_empty_patch_is_a_400(self, tools_client, world):
        assert (patch(tools_client, world, {}).status_code, error(patch(tools_client, world, {}))) == (400, "nothing_to_change")

    def test_a_tool_that_cannot_run_is_accepted_and_stores_nothing(self, tools_client, world):
        response = patch(tools_client, world, {"overrides": {"live_e": True}})

        assert response.status_code == 200
        assert states(response)["live_e"] == "unavailable"
        assert world.repository.default_overrides(world.alice.id) == {}


class TestSpendingTools:
    @pytest.fixture(autouse=True)
    def spender(self, tools_client, world):
        world.collaborators.tool_registry.register(_stub("spend_f", True, None, True))

    def test_a_tool_that_spends_is_off_under_the_read_only_default_and_says_so(self, tools_client, world):
        response = mine(tools_client, world)

        tool = {t["name"]: t for t in response.json()["data"]["tools"]}["spend_f"]
        assert (tool["state"], tool["spends"], tool["writes"], tool["reviewed"]) == ("off", True, False, False)
        assert states(response)["read_a"] == "on"

    def test_it_is_on_under_all_on_and_when_the_user_turns_it_on_explicitly(self, tools_client, world):
        assert states(patch(tools_client, world, {"overrides": {"spend_f": True}}))["spend_f"] == "on"
        assert states(patch(tools_client, world, {"overrides": {"spend_f": None}}))["spend_f"] == "off"
        assert states(patch(tools_client, world, {"policy": "all_on"}))["spend_f"] == "on"

    def test_a_key_sees_it_off_in_the_default_and_cannot_turn_it_on(self, tools_client, world):
        key = world.mint(world.alice, scopes=("mcp",), name="assistant")[0]

        listing = tools_client.get(f"/api/mcp/keys/{key.id}/tools", headers=world.session("alice"))
        widen = tools_client.patch(
            f"/api/mcp/keys/{key.id}/tools", json={"overrides": {"spend_f": True}}, headers=world.session("alice")
        )

        assert states(listing)["spend_f"] == "off_in_default"
        assert widen.status_code == 400

    def test_a_read_only_ceiling_locks_it_like_a_writing_tool(self, tools_client, world):
        tools_client.patch("/api/admin/mcp/tools", json={"policy": "read_only_on"}, headers=world.session("root"))

        assert states(mine(tools_client, world))["spend_f"] == "locked_by_admin"

    def test_the_ceiling_listing_carries_the_flag(self, tools_client, world):
        tool = {t["name"]: t for t in tools_client.get("/api/admin/mcp/tools", headers=world.session("root")).json()["data"]["tools"]}

        assert tool["spend_f"]["spends"] is True and tool["read_a"]["spends"] is False


class TestAdminLockOnTheUserRoutes:
    def lock(self, tools_client, world, **overrides):
        return tools_client.patch("/api/admin/mcp/tools", json={"overrides": overrides}, headers=world.session("root"))

    def test_a_tool_the_administrator_turned_off_is_listed_locked(self, tools_client, world):
        self.lock(tools_client, world, read_b=False)

        response = mine(tools_client, world)

        assert states(response)["read_b"] == "locked_by_admin"
        assert response.json()["data"]["summary"]["locked"] == 1
        assert response.json()["data"]["summary"]["total"] == 3

    def test_changing_a_locked_tool_is_a_409(self, tools_client, world):
        self.lock(tools_client, world, read_b=False)

        for value in (True, False, None):
            response = patch(tools_client, world, {"overrides": {"read_b": value}})
            assert (response.status_code, error(response)) == (409, "locked_by_admin")

    def test_a_locked_name_in_a_map_changes_nothing_else(self, tools_client, world):
        self.lock(tools_client, world, read_b=False)

        response = patch(tools_client, world, {"overrides": {"write_c": True, "read_b": True}})

        assert response.status_code == 409
        assert states(mine(tools_client, world))["write_c"] == "off"

    def test_a_read_only_ceiling_locks_every_writing_tool(self, tools_client, world):
        tools_client.patch("/api/admin/mcp/tools", json={"policy": "read_only_on"}, headers=world.session("root"))

        response = mine(tools_client, world)

        assert response.json()["data"]["ceiling_policy"] == "read_only_on"
        assert states(response) == {
            "read_a": "on", "read_b": "on", "write_c": "locked_by_admin", "write_d": "locked_by_admin",
            "live_e": "unavailable",
        }

    def test_the_lock_beats_a_choice_the_user_made_before_it(self, tools_client, world):
        patch(tools_client, world, {"policy": "all_on"})

        self.lock(tools_client, world, write_c=False)

        assert states(mine(tools_client, world))["write_c"] == "locked_by_admin"


class TestKeyListing:
    def make_key(self, world, owner=None, scopes=("mcp",), name="assistant"):
        return world.mint(owner or world.alice, scopes=scopes, name=name)[0]

    def get(self, client, world, key_id, name="alice"):
        return client.get(f"/api/mcp/keys/{key_id}/tools", headers=world.session(name))

    def test_a_key_starts_with_its_owners_default(self, tools_client, world):
        key = self.make_key(world)

        response = self.get(tools_client, world, key.id)

        data = response.json()["data"]
        assert (response.status_code, data["level"], data["key_id"]) == (200, "key", key.id)
        assert "policy" not in data
        assert states(response) == {
            "read_a": "on", "read_b": "on", "write_c": "off_in_default", "write_d": "off_in_default",
            "live_e": "unavailable",
        }

    def test_a_tool_off_in_the_default_is_off_in_default_on_the_key(self, tools_client, world):
        key = self.make_key(world)
        patch(tools_client, world, {"overrides": {"read_b": False}})

        assert states(self.get(tools_client, world, key.id))["read_b"] == "off_in_default"

    def test_a_foreign_key_is_a_404_exactly_like_one_that_does_not_exist(self, tools_client, world):
        bobs = self.make_key(world, owner=world.bob)

        foreign = self.get(tools_client, world, bobs.id)
        missing = self.get(tools_client, world, "01JMISSING")

        assert (foreign.status_code, missing.status_code) == (404, 404)
        assert foreign.json() == missing.json()

    def test_a_key_without_the_mcp_scope_is_a_400(self, tools_client, world):
        key = self.make_key(world, scopes=("read",), name="script")

        response = self.get(tools_client, world, key.id)

        assert (response.status_code, error(response)) == (400, "key_missing_mcp")

    def test_it_needs_a_login(self, tools_client, world):
        key = self.make_key(world)

        assert tools_client.get(f"/api/mcp/keys/{key.id}/tools").status_code == 401


class TestKeyPatch:
    def setup_key(self, world):
        return world.mint(world.alice, scopes=("mcp",), name="assistant")[0]

    def patch_key(self, client, world, key_id, body, name="alice"):
        return client.patch(f"/api/mcp/keys/{key_id}/tools", json=body, headers=world.session(name))

    def test_a_key_turns_a_tool_off_without_touching_the_default_or_other_keys(self, tools_client, world):
        key, other = self.setup_key(world), world.mint(world.alice, scopes=("mcp",), name="other")[0]

        response = self.patch_key(tools_client, world, key.id, {"overrides": {"read_a": False}})

        assert response.status_code == 200
        assert states(response)["read_a"] == "off"
        assert states(tools_client.get(f"/api/mcp/keys/{other.id}/tools", headers=world.session("alice")))["read_a"] == "on"
        assert states(mine(tools_client, world))["read_a"] == "on"

    def test_true_is_refused_because_a_key_can_only_narrow(self, tools_client, world):
        key = self.setup_key(world)

        response = self.patch_key(tools_client, world, key.id, {"overrides": {"write_c": True}})

        assert (response.status_code, error(response)) == (400, "key_can_only_narrow")

    def test_true_is_refused_even_next_to_a_valid_entry_and_nothing_is_stored(self, tools_client, world):
        key = self.setup_key(world)

        response = self.patch_key(tools_client, world, key.id, {"overrides": {"read_a": False, "write_c": True}})

        assert response.status_code == 400
        assert world.repository.key_disabled(key.id) == frozenset()

    def test_null_clears_one_narrowing(self, tools_client, world):
        key = self.setup_key(world)
        self.patch_key(tools_client, world, key.id, {"overrides": {"read_a": False, "read_b": False}})

        response = self.patch_key(tools_client, world, key.id, {"overrides": {"read_a": None}})

        assert states(response)["read_a"] == "on"
        assert states(response)["read_b"] == "off"

    def test_reset_clears_every_narrowing(self, tools_client, world):
        key = self.setup_key(world)
        self.patch_key(tools_client, world, key.id, {"overrides": {"read_a": False, "read_b": False}})

        response = self.patch_key(tools_client, world, key.id, {"reset": True})

        assert states(response)["read_a"] == states(response)["read_b"] == "on"
        assert world.repository.key_disabled(key.id) == frozenset()

    def test_a_locked_tool_is_a_409(self, tools_client, world):
        key = self.setup_key(world)
        tools_client.patch("/api/admin/mcp/tools", json={"overrides": {"read_a": False}}, headers=world.session("root"))

        response = self.patch_key(tools_client, world, key.id, {"overrides": {"read_a": False}})

        assert (response.status_code, error(response)) == (409, "locked_by_admin")

    def test_a_name_outside_the_catalogue_is_a_400(self, tools_client, world):
        key = self.setup_key(world)

        response = self.patch_key(tools_client, world, key.id, {"overrides": {"nope": False}})

        assert (response.status_code, error(response)) == (400, "unknown_tool")

    def test_someone_elses_key_is_a_404_and_stays_untouched(self, tools_client, world):
        bobs = world.mint(world.bob, scopes=("mcp",), name="bobs")[0]

        response = self.patch_key(tools_client, world, bobs.id, {"overrides": {"read_a": False}})

        assert response.status_code == 404
        assert world.repository.key_disabled(bobs.id) == frozenset()

    def test_a_key_without_the_mcp_scope_is_a_400(self, tools_client, world):
        key = world.mint(world.alice, scopes=("read",), name="script")[0]

        response = self.patch_key(tools_client, world, key.id, {"overrides": {"read_a": False}})

        assert (response.status_code, error(response)) == (400, "key_missing_mcp")

    def test_a_revoked_key_is_a_409(self, tools_client, world):
        key = self.setup_key(world)
        tools_client.delete(f"/api/keys/{key.id}", headers=world.session("alice"))

        response = self.patch_key(tools_client, world, key.id, {"overrides": {"read_a": False}})

        assert (response.status_code, error(response)) == (409, "key_revoked")

    def test_an_empty_patch_and_both_forms_together_are_400s(self, tools_client, world):
        key = self.setup_key(world)

        assert error(self.patch_key(tools_client, world, key.id, {})) == "nothing_to_change"
        both = self.patch_key(tools_client, world, key.id, {"overrides": {"read_a": False}, "reset": True})
        assert (both.status_code, error(both)) == (400, "invalid_request")

    def test_a_change_appends_an_event_naming_what_was_turned_off(self, tools_client, world):
        key = self.setup_key(world)

        self.patch_key(tools_client, world, key.id, {"overrides": {"read_a": False}})
        events = tools_client.get(f"/api/keys/{key.id}/events", headers=world.session("alice")).json()["data"]["items"]

        changed = [e for e in events if e["action"] == "mcp_tools_changed"]
        assert len(changed) == 1
        assert changed[0]["actor"]["id"] == world.alice.id
        assert changed[0]["detail"] == {"before": [], "after": ["read_a"]}
        assert changed[0]["changes"] == {"mcp_tools": {"turned_off": ["read_a"], "turned_on": []}}

    def test_a_later_change_and_a_reset_each_append_one_event(self, tools_client, world):
        key = self.setup_key(world)

        self.patch_key(tools_client, world, key.id, {"overrides": {"read_a": False}})
        self.patch_key(tools_client, world, key.id, {"overrides": {"read_b": False}})
        self.patch_key(tools_client, world, key.id, {"reset": True})
        events = tools_client.get(f"/api/keys/{key.id}/events", headers=world.session("alice")).json()["data"]["items"]

        changes = [e["changes"]["mcp_tools"] for e in reversed(events) if e["action"] == "mcp_tools_changed"]
        assert changes == [
            {"turned_off": ["read_a"], "turned_on": []},
            {"turned_off": ["read_b"], "turned_on": []},
            {"turned_off": [], "turned_on": ["read_a", "read_b"]},
        ]

    def test_a_patch_that_changes_nothing_appends_no_event(self, tools_client, world):
        key = self.setup_key(world)
        self.patch_key(tools_client, world, key.id, {"overrides": {"read_a": False}})

        self.patch_key(tools_client, world, key.id, {"overrides": {"read_a": False}})
        self.patch_key(tools_client, world, key.id, {"overrides": {"read_b": None}})
        events = tools_client.get(f"/api/keys/{key.id}/events", headers=world.session("alice")).json()["data"]["items"]

        assert len([e for e in events if e["action"] == "mcp_tools_changed"]) == 1

    def test_a_default_change_appends_no_key_event(self, tools_client, world):
        key = self.setup_key(world)

        patch(tools_client, world, {"policy": "all_on"})
        events = tools_client.get(f"/api/keys/{key.id}/events", headers=world.session("alice")).json()["data"]["items"]

        assert [e for e in events if e["action"] == "mcp_tools_changed"] == []

    def test_the_key_list_counts_each_key_by_its_own_tools(self, tools_client, world):
        narrow = world.mint(world.alice, scopes=("mcp",), name="narrow")[0]
        wide = world.mint(world.alice, scopes=("mcp",), name="wide")[0]
        script = world.mint(world.alice, scopes=("read",), name="script")[0]
        self.patch_key(tools_client, world, narrow.id, {"overrides": {"read_a": False}})

        keys = {k["name"]: k for k in tools_client.get("/api/keys", headers=world.session("alice")).json()["data"]["keys"]}

        assert keys["narrow"]["mcp_tools"] == {"exposed": 1, "total": 4}
        assert keys["wide"]["mcp_tools"] == {"exposed": 2, "total": 4}
        assert keys["script"]["mcp_tools"] is None
        assert script.id and wide.id


class TestAdminCeilingRoutes:
    def get(self, client, world, name="root"):
        return client.get("/api/admin/mcp/tools", headers=world.session(name))

    def patch_admin(self, client, world, body, name="root"):
        return client.patch("/api/admin/mcp/tools", json=body, headers=world.session(name))

    def test_the_default_ceiling_has_everything_on_and_the_unavailable_marked(self, tools_client, world):
        response = self.get(tools_client, world)

        data = response.json()["data"]
        assert (data["level"], data["policy"]) == ("ceiling", "all_on")
        assert states(response) == {
            "read_a": "on", "read_b": "on", "write_c": "on", "write_d": "on", "live_e": "unavailable",
        }
        assert data["summary"]["exposed"] == 4

    def test_a_read_only_policy_turns_the_writing_tools_off(self, tools_client, world):
        response = self.patch_admin(tools_client, world, {"policy": "read_only_on"})

        assert response.json()["data"]["policy"] == "read_only_on"
        assert states(response)["write_c"] == "off"
        assert states(response)["read_a"] == "on"
        assert world.stack.settings.get_setting("mcp_tools_ceiling_policy", None) == "read_only_on"

    def test_a_row_beats_the_policy_and_null_removes_it(self, tools_client, world):
        self.patch_admin(tools_client, world, {"policy": "read_only_on", "overrides": {"write_c": True}})
        assert states(self.get(tools_client, world))["write_c"] == "on"

        response = self.patch_admin(tools_client, world, {"overrides": {"write_c": None}})

        assert states(response)["write_c"] == "off"
        assert world.repository.ceiling_overrides() == {}

    def test_unknown_names_and_policies_are_400s_and_change_nothing(self, tools_client, world):
        unknown = self.patch_admin(tools_client, world, {"policy": "read_only_on", "overrides": {"nope": False}})
        invalid = self.patch_admin(tools_client, world, {"policy": "none_on"})

        assert (unknown.status_code, error(unknown)) == (400, "unknown_tool")
        assert (invalid.status_code, error(invalid)) == (400, "invalid_policy")
        assert states(self.get(tools_client, world))["write_c"] == "on"

    def test_a_tool_that_cannot_run_is_accepted_and_stores_nothing(self, tools_client, world):
        response = self.patch_admin(tools_client, world, {"overrides": {"live_e": False}})

        assert response.status_code == 200
        assert world.repository.ceiling_overrides() == {}

    def test_an_empty_patch_is_a_400(self, tools_client, world):
        assert error(self.patch_admin(tools_client, world, {})) == "nothing_to_change"

    def test_a_non_admin_session_is_refused_on_both_routes(self, tools_client, world):
        assert self.get(tools_client, world, "alice").status_code == 403
        assert self.patch_admin(tools_client, world, {"policy": "all_on"}, "alice").status_code == 403
        assert states(self.get(tools_client, world))["write_c"] == "on"

    def test_it_needs_a_login(self, tools_client):
        assert tools_client.get("/api/admin/mcp/tools").status_code == 401


class TestSessionOnly:
    def routes(self, key_id):
        return (
            ("GET", "/api/mcp/tools", None),
            ("PATCH", "/api/mcp/tools", {"policy": "all_on"}),
            ("GET", f"/api/mcp/keys/{key_id}/tools", None),
            ("PATCH", f"/api/mcp/keys/{key_id}/tools", {"overrides": {"read_a": False}}),
            ("GET", "/api/admin/mcp/tools", None),
            ("PATCH", "/api/admin/mcp/tools", {"policy": "all_on"}),
        )

    def call(self, client, method, path, body, headers):
        return client.request(method, path, json=body, headers=headers)

    def test_an_mcp_key_cannot_call_any_of_them(self, tools_client, world):
        key, secret = world.mint(world.alice, scopes=("read", "generate", "content", "mcp"), name="all")

        for method, path, body in self.routes(key.id):
            response = self.call(tools_client, method, path, body, world.bearer(secret))
            assert (response.status_code, error(response)) == (403, "key_not_allowed"), (method, path)

    def test_an_admin_key_cannot_call_any_of_them(self, tools_client, world):
        world.stack.settings.set_setting("api_key_scopes_allowed", ["read", "generate", "content", "admin", "mcp"])
        key, secret = world.mint(world.root, scopes=("admin", "mcp"), days=30, name="ops")

        for method, path, body in self.routes(key.id):
            response = self.call(tools_client, method, path, body, world.bearer(secret))
            assert (response.status_code, error(response)) == (403, "key_not_allowed"), (method, path)

    def test_a_key_cannot_widen_itself_or_its_owner_default(self, tools_client, world):
        key, secret = world.mint(world.alice, scopes=("mcp",), name="assistant")

        tools_client.patch("/api/mcp/tools", json={"policy": "all_on"}, headers=world.bearer(secret))
        tools_client.patch(
            f"/api/mcp/keys/{key.id}/tools", json={"overrides": {"write_c": False}}, headers=world.bearer(secret)
        )

        assert world.repository.default_policy(world.alice.id) is None
        assert world.repository.key_disabled(key.id) == frozenset()

    def test_a_session_still_reaches_all_of_them(self, tools_client, world):
        key = world.mint(world.alice, scopes=("mcp",), name="assistant")[0]

        for method, path, body in self.routes(key.id)[:4]:
            response = self.call(tools_client, method, path, body, world.session("alice"))
            assert response.status_code == 200, (method, path)
