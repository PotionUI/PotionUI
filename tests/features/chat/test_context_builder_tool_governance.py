"""Tests wiring src.features.llm.tools.governance into
ChatContextBuilder.resolve_session_prompt_and_tools: governance actually
filters the tool set, it is scoped to the session's ACTIVE LLM config (a
different config's rows must not leak into a session using another config),
and - the test that must bite - the memoization cache key incorporates
governance state (including the config id itself), so an admin/user toggle is
visible on the very next resolve within the TTL window rather than only after
it expires.
"""

import io
import sys
from unittest.mock import Mock, patch

import pytest

from tests.conftest import TestDatabase

from src.features.chat.context_builder import ChatContextBuilder
from src.features.chat.modes import ChatModeRegistry, build_generation_mode
from src.features.llm.tools.governance import ToolGovernanceRepository


@pytest.fixture
def governance_db():
    test_database = TestDatabase()
    with patch("src.platform.database.database.db", test_database), \
         patch("src.platform.database.migration_runner.db", test_database):
        from src.platform.database.migration_runner import MigrationRunner

        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            MigrationRunner().run_migrations()
        finally:
            sys.stdout = old_stdout

        yield test_database
    test_database.close()


def _mode_registry() -> ChatModeRegistry:
    registry = ChatModeRegistry()
    registry.register(build_generation_mode())
    return registry


def _session(user_id="user-1", llm_config_id="cfg-a", mode="generation", metadata=None):
    session = Mock()
    session.mode = mode
    session.metadata = metadata
    session.user_id = user_id
    session.llm_config_id = llm_config_id
    return session


def _make_builder(governance_repo):
    from src.features.llm.tools.registry import ToolRegistry
    from src.features.llm.tools.builtin.form_context_tool import GetFormStateTool
    from src.features.llm.tools.builtin.active_models_tool import GetActiveModelsTool

    tool_registry = ToolRegistry()
    tool_registry.register(GetFormStateTool())
    tool_registry.register(GetActiveModelsTool())

    manager = Mock()
    manager.chat_mode_registry = _mode_registry()
    tool_executor = Mock()
    tool_executor.tool_registry = tool_registry
    manager.tool_executor = tool_executor
    manager.tool_governance_repository = governance_repo
    return ChatContextBuilder(manager)


class TestGovernanceFiltersTheToolSet:
    def test_admin_disabled_tool_is_removed(self, governance_db):
        repo = ToolGovernanceRepository()
        repo.upsert_config("cfg-a", "get_active_models", enabled=False)
        builder = _make_builder(repo)

        _prompt, allowed, _mode = builder.resolve_session_prompt_and_tools(_session())

        assert allowed == ["get_form_state"]

    def test_user_opt_out_is_removed_for_that_user_only(self, governance_db):
        repo = ToolGovernanceRepository()
        repo.set_user_disabled("user-1", "get_active_models", True)
        builder = _make_builder(repo)

        _prompt, allowed_1, _ = builder.resolve_session_prompt_and_tools(_session(user_id="user-1"))
        _prompt, allowed_2, _ = builder.resolve_session_prompt_and_tools(_session(user_id="user-2"))

        assert "get_active_models" not in allowed_1
        assert "get_active_models" in allowed_2

    def test_locked_tool_overrides_the_users_own_opt_out(self, governance_db):
        repo = ToolGovernanceRepository()
        repo.upsert_config("cfg-a", "get_active_models", locked=True)
        repo.set_user_disabled("user-1", "get_active_models", True)
        builder = _make_builder(repo)

        _prompt, allowed, _ = builder.resolve_session_prompt_and_tools(_session(user_id="user-1"))

        assert "get_active_models" in allowed

    def test_no_real_governance_repository_is_full_passthrough(self):
        # A Mock() manager (no real ToolGovernanceRepository attached, as in
        # plenty of other chat tests that predate this feature) auto-vivifies
        # `tool_governance_repository` into a bare Mock - this must not raise
        # and must not filter anything.
        builder = _make_builder(governance_repo=Mock())

        _prompt, allowed, _ = builder.resolve_session_prompt_and_tools(_session())

        assert set(allowed) == {"get_form_state", "get_active_models"}

    def test_session_with_no_llm_config_id_is_passthrough_on_admin_axis(self, governance_db):
        # A tool disabled for cfg-a must not affect a session that has no
        # config assigned yet.
        repo = ToolGovernanceRepository()
        repo.upsert_config("cfg-a", "get_active_models", enabled=False)
        builder = _make_builder(repo)

        _prompt, allowed, _ = builder.resolve_session_prompt_and_tools(_session(llm_config_id=None))

        assert "get_active_models" in allowed

    def test_session_with_no_llm_config_id_still_honors_the_users_global_opt_out(self, governance_db):
        repo = ToolGovernanceRepository()
        repo.set_user_disabled("user-1", "get_active_models", True)
        builder = _make_builder(repo)

        _prompt, allowed, _ = builder.resolve_session_prompt_and_tools(
            _session(user_id="user-1", llm_config_id=None)
        )

        assert "get_active_models" not in allowed


class TestGovernanceIsScopedToTheSessionsConfig:
    def test_same_tool_disabled_in_one_config_but_enabled_in_another(self, governance_db):
        repo = ToolGovernanceRepository()
        repo.upsert_config("cfg-a", "get_active_models", enabled=False)
        repo.upsert_config("cfg-b", "get_active_models", enabled=True)
        builder = _make_builder(repo)

        _prompt, allowed_a, _ = builder.resolve_session_prompt_and_tools(_session(llm_config_id="cfg-a"))
        _prompt, allowed_b, _ = builder.resolve_session_prompt_and_tools(_session(llm_config_id="cfg-b"))

        assert "get_active_models" not in allowed_a
        assert "get_active_models" in allowed_b

    def test_lock_in_one_config_does_not_lock_another(self, governance_db):
        repo = ToolGovernanceRepository()
        repo.upsert_config("cfg-a", "get_active_models", locked=True)
        repo.set_user_disabled("user-1", "get_active_models", True)
        builder = _make_builder(repo)

        _prompt, allowed_a, _ = builder.resolve_session_prompt_and_tools(
            _session(user_id="user-1", llm_config_id="cfg-a")
        )
        _prompt, allowed_b, _ = builder.resolve_session_prompt_and_tools(
            _session(user_id="user-1", llm_config_id="cfg-b")
        )

        # Locked in cfg-a: the user's opt-out is overridden, tool stays.
        assert "get_active_models" in allowed_a
        # Not locked in cfg-b: the user's (global) opt-out applies normally.
        assert "get_active_models" not in allowed_b


class TestGovernanceCacheKeyBites:
    """The cache is a short-TTL memoization keyed on (mode, enabled-tools,
    system message, unavailable, config id, ...). Flipping a governance row
    must show up on the very next resolve - i.e. it must be part of the key -
    not only after the TTL expires."""

    def test_admin_toggle_is_visible_on_the_next_resolve_within_ttl(self, governance_db):
        repo = ToolGovernanceRepository()
        builder = _make_builder(repo)
        session = _session()

        _prompt, before, _ = builder.resolve_session_prompt_and_tools(session)
        assert "get_active_models" in before

        repo.upsert_config("cfg-a", "get_active_models", enabled=False)

        _prompt, after, _ = builder.resolve_session_prompt_and_tools(session)

        assert "get_active_models" not in after

    def test_user_opt_out_toggle_is_visible_on_the_next_resolve_within_ttl(self, governance_db):
        repo = ToolGovernanceRepository()
        builder = _make_builder(repo)
        session = _session(user_id="user-1")

        _prompt, before, _ = builder.resolve_session_prompt_and_tools(session)
        assert "get_active_models" in before

        repo.set_user_disabled("user-1", "get_active_models", True)

        _prompt, after, _ = builder.resolve_session_prompt_and_tools(session)

        assert "get_active_models" not in after

    def test_two_sessions_on_different_configs_do_not_share_a_cache_entry(self, governance_db):
        repo = ToolGovernanceRepository()
        repo.upsert_config("cfg-a", "get_active_models", enabled=False)
        builder = _make_builder(repo)

        _prompt, allowed_a, _ = builder.resolve_session_prompt_and_tools(_session(llm_config_id="cfg-a"))
        _prompt, allowed_b, _ = builder.resolve_session_prompt_and_tools(_session(llm_config_id="cfg-b"))

        assert "get_active_models" not in allowed_a
        assert "get_active_models" in allowed_b
