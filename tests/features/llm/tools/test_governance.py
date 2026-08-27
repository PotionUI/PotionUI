"""Tests for src.features.llm.tools.governance: the pure composition function
(the effective-tool-set truth table), the per-config repository against a
real (scratch, in-memory) database, and the manager's mutation-time
validation. Governance is per LLM config - the same tool must be governed
independently across two different configs.
"""

from unittest.mock import Mock, patch

import pytest

from tests.conftest import TestDatabase

from src.features.llm.tools.governance import (
    ToolAdminDisabledException,
    ToolGovernanceEditor,
    ToolGovernanceRepository,
    ToolLockedException,
    ToolNotFoundException,
    build_admin_toolset_listing,
    build_user_toolset_listing,
    compute_allowed_tool_names,
)


# ---------------------------------------------------------------------------
# compute_allowed_tool_names — the effective-tool-set truth table
# ---------------------------------------------------------------------------

class TestComputeAllowedToolNames:
    def test_default_passthrough_when_no_governance_at_all(self):
        allowed = compute_allowed_tool_names(["a", "b"], {}, set())
        assert allowed == ["a", "b"]

    def test_unknown_names_in_admin_config_are_ignored(self):
        # A governance row for a tool a removed plugin used to own - it just
        # isn't in mode_tool_names, so it can't affect the result.
        allowed = compute_allowed_tool_names(
            ["a"], {"stale_removed_tool": {"enabled": False, "locked": False}}, set()
        )
        assert allowed == ["a"]

    def test_admin_disabled_beats_everything(self):
        allowed = compute_allowed_tool_names(
            ["a"], {"a": {"enabled": False, "locked": True}}, set()
        )
        assert allowed == []

    def test_admin_disabled_beats_a_user_who_wants_it_enabled(self):
        # A user can only opt OUT, never override an admin disable.
        allowed = compute_allowed_tool_names(
            ["a"], {"a": {"enabled": False, "locked": False}}, set()
        )
        assert allowed == []

    def test_user_disabled_removes_an_unlocked_enabled_tool(self):
        allowed = compute_allowed_tool_names(
            ["a", "b"], {"a": {"enabled": True, "locked": False}}, {"a"}
        )
        assert allowed == ["b"]

    def test_locked_beats_user_disable(self):
        allowed = compute_allowed_tool_names(
            ["a"], {"a": {"enabled": True, "locked": True}}, {"a"}
        )
        assert allowed == ["a"]

    def test_tuple_snapshot_form_is_read_the_same_as_dict_form(self):
        allowed_dict = compute_allowed_tool_names(
            ["a"], {"a": {"enabled": True, "locked": True}}, {"a"}
        )
        allowed_tuple = compute_allowed_tool_names(["a"], {"a": (True, True)}, {"a"})
        assert allowed_dict == allowed_tuple == ["a"]

    def test_order_follows_mode_tool_names_not_governance_map(self):
        allowed = compute_allowed_tool_names(["b", "a"], {}, set())
        assert allowed == ["b", "a"]

    def test_same_tool_independent_across_two_config_snapshots(self):
        # The function itself is config-agnostic - it just reads whichever
        # snapshot dict it's handed. The per-config isolation lives in what
        # the repository returns for each config (see below).
        config_a_snapshot = {"a": {"enabled": False, "locked": False}}
        config_b_snapshot = {"a": {"enabled": True, "locked": False}}
        assert compute_allowed_tool_names(["a"], config_a_snapshot, set()) == []
        assert compute_allowed_tool_names(["a"], config_b_snapshot, set()) == ["a"]


# ---------------------------------------------------------------------------
# Listing builders (pure)
# ---------------------------------------------------------------------------

def _tool(name, label=None, group="Other", description="", requires_approval=False):
    tool = Mock()
    tool.name = name
    tool.label = label or name
    tool.group = group
    tool.user_description = description
    tool.requires_approval = requires_approval
    return tool


class TestBuildAdminToolsetListing:
    def test_defaults_when_no_governance_row(self):
        listing = build_admin_toolset_listing([_tool("a")], {}, source_of=lambda n: "builtin")
        assert listing == [{
            "name": "a", "label": "a", "group": "Other", "user_description": "",
            "requires_approval": False, "enabled": True, "locked": False, "source": "builtin",
        }]

    def test_governance_row_overrides_defaults(self):
        listing = build_admin_toolset_listing(
            [_tool("a")], {"a": {"enabled": False, "locked": True}}, source_of=lambda n: "builtin"
        )
        assert listing[0]["enabled"] is False
        assert listing[0]["locked"] is True

    def test_sorted_by_group_then_label(self):
        tools = [_tool("z", group="B"), _tool("a", group="A"), _tool("m", group="A")]
        listing = build_admin_toolset_listing(tools, {}, source_of=lambda n: "builtin")
        assert [t["name"] for t in listing] == ["a", "m", "z"]


class TestBuildUserToolsetListing:
    def test_admin_disabled_tool_is_omitted_entirely(self):
        listing = build_user_toolset_listing(
            [_tool("a"), _tool("b")], {"a": {"enabled": False, "locked": False}}, set()
        )
        assert [t["name"] for t in listing] == ["b"]

    def test_empty_admin_config_shows_every_tool_unlocked(self):
        # The "no config context" case used by the global preferences GET.
        listing = build_user_toolset_listing([_tool("a"), _tool("b")], {}, set())
        assert [t["name"] for t in listing] == ["a", "b"]
        assert all(t["locked"] is False for t in listing)

    def test_disabled_by_user_flag_reflects_the_users_own_set(self):
        listing = build_user_toolset_listing([_tool("a")], {}, {"a"})
        assert listing[0]["disabled_by_user"] is True

    def test_locked_tool_still_reports_its_lock(self):
        listing = build_user_toolset_listing(
            [_tool("a")], {"a": {"enabled": True, "locked": True}}, set()
        )
        assert listing[0]["locked"] is True


# ---------------------------------------------------------------------------
# ToolGovernanceRepository — against a real scratch database
# ---------------------------------------------------------------------------

@pytest.fixture
def governance_db():
    test_database = TestDatabase()
    with patch("src.platform.database.database.db", test_database), \
         patch("src.platform.database.migration_runner.db", test_database), \
         patch("src.features.llm.tools.governance_repository.db", test_database):
        from src.platform.database.migration_runner import MigrationRunner
        import io
        import sys

        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            MigrationRunner().run_migrations()
        finally:
            sys.stdout = old_stdout

        yield test_database
    test_database.close()


class TestToolGovernanceRepository:
    def test_missing_tool_has_no_config(self, governance_db):
        repo = ToolGovernanceRepository()
        assert repo.get_config("cfg-a", "nope") is None

    def test_upsert_then_get_round_trips(self, governance_db):
        repo = ToolGovernanceRepository()
        repo.upsert_config("cfg-a", "search_gallery", enabled=False, locked=True)
        assert repo.get_config("cfg-a", "search_gallery") == {"enabled": False, "locked": True}

    def test_upsert_merges_partial_updates(self, governance_db):
        repo = ToolGovernanceRepository()
        repo.upsert_config("cfg-a", "search_gallery", enabled=False)
        repo.upsert_config("cfg-a", "search_gallery", locked=True)
        assert repo.get_config("cfg-a", "search_gallery") == {"enabled": False, "locked": True}

    def test_get_all_config_includes_every_row_for_that_config_only(self, governance_db):
        repo = ToolGovernanceRepository()
        repo.upsert_config("cfg-a", "a", enabled=False)
        repo.upsert_config("cfg-a", "b", locked=True)
        repo.upsert_config("cfg-b", "a", enabled=False)  # different config, must not leak in
        assert repo.get_all_config("cfg-a") == {
            "a": {"enabled": False, "locked": False},
            "b": {"enabled": True, "locked": True},
        }

    def test_same_tool_governed_independently_per_config(self, governance_db):
        repo = ToolGovernanceRepository()
        repo.upsert_config("cfg-a", "search_gallery", enabled=False)
        repo.upsert_config("cfg-b", "search_gallery", enabled=True, locked=True)

        assert repo.get_config("cfg-a", "search_gallery") == {"enabled": False, "locked": False}
        assert repo.get_config("cfg-b", "search_gallery") == {"enabled": True, "locked": True}

    def test_snapshot_only_returns_requested_names_for_that_config(self, governance_db):
        repo = ToolGovernanceRepository()
        repo.upsert_config("cfg-a", "a", enabled=False)
        repo.upsert_config("cfg-a", "b", locked=True)
        repo.upsert_config("cfg-b", "a", enabled=False)
        snapshot = repo.get_config_snapshot("cfg-a", ["a"])
        assert snapshot == {"a": (False, False)}

    def test_snapshot_of_empty_names_does_not_query(self, governance_db):
        repo = ToolGovernanceRepository()
        assert repo.get_config_snapshot("cfg-a", []) == {}

    def test_delete_config_drops_only_that_configs_rows(self, governance_db):
        repo = ToolGovernanceRepository()
        repo.upsert_config("cfg-a", "a", enabled=False)
        repo.upsert_config("cfg-b", "a", enabled=False)

        repo.delete_config("cfg-a")

        assert repo.get_config("cfg-a", "a") is None
        assert repo.get_config("cfg-b", "a") == {"enabled": False, "locked": False}

    def test_user_disabled_set_starts_empty(self, governance_db):
        repo = ToolGovernanceRepository()
        assert repo.get_user_disabled("user-1") == set()

    def test_set_user_disabled_true_then_false_round_trips(self, governance_db):
        repo = ToolGovernanceRepository()
        repo.set_user_disabled("user-1", "search_gallery", True)
        assert repo.get_user_disabled("user-1") == {"search_gallery"}

        repo.set_user_disabled("user-1", "search_gallery", False)
        assert repo.get_user_disabled("user-1") == set()

    def test_user_disabled_set_is_isolated_per_user(self, governance_db):
        repo = ToolGovernanceRepository()
        repo.set_user_disabled("user-1", "search_gallery", True)
        assert repo.get_user_disabled("user-2") == set()

    def test_user_disabled_set_is_not_scoped_to_any_config(self, governance_db):
        # The opt-out set has no llm_config_id column at all - this is really
        # just documenting the "global, not per-config" contract.
        repo = ToolGovernanceRepository()
        repo.set_user_disabled("user-1", "search_gallery", True)
        # Nothing config-specific to pass; the same set applies everywhere.
        assert repo.get_user_disabled("user-1") == {"search_gallery"}


# ---------------------------------------------------------------------------
# ToolGovernanceEditor — mutation-time validation
# ---------------------------------------------------------------------------

class TestToolGovernanceEditor:
    def _manager(self, registered_names=("search_gallery",)):
        repo = Mock(spec=ToolGovernanceRepository)
        registry = Mock()
        registry.get.side_effect = lambda name: object() if name in registered_names else None
        return ToolGovernanceEditor(repository=repo, tool_registry=registry), repo

    def test_set_admin_config_rejects_an_unregistered_ungoverned_tool(self):
        manager, repo = self._manager(registered_names=())
        repo.get_config.return_value = None
        with pytest.raises(ToolNotFoundException):
            manager.set_admin_config("cfg-a", "ghost", enabled=False)

    def test_set_admin_config_allows_a_removed_plugin_tool_that_still_has_a_row(self):
        # The tool isn't currently registered (plugin disabled) but a
        # governance row for THIS config from when it was still exists.
        manager, repo = self._manager(registered_names=())
        repo.get_config.return_value = {"enabled": True, "locked": False}
        manager.set_admin_config("cfg-a", "ghost", enabled=False)
        repo.upsert_config.assert_called_once_with("cfg-a", "ghost", enabled=False, locked=None)
        repo.get_config.assert_called_once_with("cfg-a", "ghost")

    def test_set_user_preference_with_config_rejects_that_configs_admin_disabled_tool(self):
        manager, repo = self._manager()
        repo.get_config.return_value = {"enabled": False, "locked": False}
        with pytest.raises(ToolAdminDisabledException):
            manager.set_user_preference("user-1", "search_gallery", True, llm_config_id="cfg-a")

    def test_set_user_preference_with_config_rejects_that_configs_locked_tool(self):
        manager, repo = self._manager()
        repo.get_config.return_value = {"enabled": True, "locked": True}
        with pytest.raises(ToolLockedException):
            manager.set_user_preference("user-1", "search_gallery", True, llm_config_id="cfg-a")

    def test_set_user_preference_with_config_succeeds_for_an_unlocked_enabled_tool(self):
        manager, repo = self._manager()
        repo.get_config.return_value = {"enabled": True, "locked": False}
        manager.set_user_preference("user-1", "search_gallery", True, llm_config_id="cfg-a")
        repo.set_user_disabled.assert_called_once_with("user-1", "search_gallery", True)

    def test_set_user_preference_with_config_defaults_to_enabled_unlocked_with_no_row(self):
        manager, repo = self._manager()
        repo.get_config.return_value = None
        manager.set_user_preference("user-1", "search_gallery", True, llm_config_id="cfg-a")
        repo.set_user_disabled.assert_called_once_with("user-1", "search_gallery", True)

    def test_set_user_preference_requires_llm_config_id(self):
        # The caller (the chat composer) always knows its active session's
        # config - there is no config-agnostic path anymore.
        manager, _repo = self._manager()
        with pytest.raises(TypeError):
            manager.set_user_preference("user-1", "search_gallery", True)

    def test_locking_in_one_config_does_not_lock_another(self):
        # A single manager talking to a real-shaped stub, driven twice with
        # different llm_config_id values and different stubbed rows.
        manager, repo = self._manager()

        repo.get_config.return_value = {"enabled": True, "locked": True}
        with pytest.raises(ToolLockedException):
            manager.set_user_preference("user-1", "search_gallery", True, llm_config_id="cfg-a")

        repo.get_config.return_value = {"enabled": True, "locked": False}
        manager.set_user_preference("user-1", "search_gallery", True, llm_config_id="cfg-b")
        repo.set_user_disabled.assert_called_once_with("user-1", "search_gallery", True)
