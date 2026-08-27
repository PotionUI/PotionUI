"""
Unit tests for pre-chat actions system.

Tests the PreChatActionRegistry and its registration, discovery,
filtering, and execution logic.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from typing import Dict, Any

from src.features.chat.pre_chat_actions import (
    PreChatAction,
    PreChatActionRegistry,
    PreChatActionResult,
)
from src.features.chat.exceptions import PreChatActionError
from src.features.chat.hooks import CHAT_PRE_ACTIONS_HOOKS


@pytest.fixture
def mock_plugin_registry():
    """Create a mock PluginRegistry."""
    return Mock()


@pytest.fixture
def mock_llm_repository():
    """Create a mock LLM repository."""
    return Mock()


@pytest.fixture
def manager(mock_plugin_registry, mock_llm_repository):
    """Create a PreChatActionRegistry instance."""
    return PreChatActionRegistry(mock_plugin_registry, mock_llm_repository)


@pytest.fixture
def sample_action():
    """Create a sample PreChatAction."""
    async def mock_execute() -> Dict[str, Any]:
        return {"success": True, "message": "Action executed"}

    return PreChatAction(
        id="test_action",
        name="Test Action",
        description="A test action",
        plugin_id="test_plugin",
        execute=mock_execute,
        default_enabled=False,
        blocking=False,
        category="general",
    )


class TestRegisterAction:
    """Tests for action registration."""

    def test_register_action(self, manager, sample_action):
        """Register an action and verify it's in get_all_actions."""
        manager.register_action(sample_action)

        all_actions = manager.get_all_actions()
        assert len(all_actions) == 1
        assert all_actions[0].id == "test_action"
        assert all_actions[0].name == "Test Action"

    def test_register_multiple_actions(self, manager):
        """Register multiple actions and verify all are stored."""
        async def mock_execute1():
            return {"success": True}

        async def mock_execute2():
            return {"success": True}

        action1 = PreChatAction(
            id="action1",
            name="Action 1",
            description="First action",
            plugin_id="plugin1",
            execute=mock_execute1,
        )
        action2 = PreChatAction(
            id="action2",
            name="Action 2",
            description="Second action",
            plugin_id="plugin2",
            execute=mock_execute2,
        )

        manager.register_action(action1)
        manager.register_action(action2)

        all_actions = manager.get_all_actions()
        assert len(all_actions) == 2
        action_ids = {a.id for a in all_actions}
        assert action_ids == {"action1", "action2"}


class TestUnregisterAction:
    """Tests for action unregistration."""

    def test_unregister_action(self, manager, sample_action):
        """Register then unregister an action, verify it's removed."""
        manager.register_action(sample_action)
        assert len(manager.get_all_actions()) == 1

        result = manager.unregister_action("test_action")
        assert result is True
        assert len(manager.get_all_actions()) == 0

    def test_unregister_nonexistent(self, manager):
        """Unregister a non-existent action returns False."""
        result = manager.unregister_action("nonexistent_action")
        assert result is False

    def test_unregister_leaves_others_intact(self, manager):
        """Unregistering one action doesn't affect others."""
        async def mock_execute():
            return {"success": True}

        action1 = PreChatAction(
            id="action1",
            name="Action 1",
            description="First",
            plugin_id="plugin1",
            execute=mock_execute,
        )
        action2 = PreChatAction(
            id="action2",
            name="Action 2",
            description="Second",
            plugin_id="plugin2",
            execute=mock_execute,
        )

        manager.register_action(action1)
        manager.register_action(action2)

        manager.unregister_action("action1")

        all_actions = manager.get_all_actions()
        assert len(all_actions) == 1
        assert all_actions[0].id == "action2"


class TestGetEnabledActions:
    """Tests for filtering enabled actions based on LLM config."""

    def test_get_enabled_actions_explicit_true(self, manager, mock_llm_repository):
        """Action explicitly enabled in provider_options."""
        async def mock_execute():
            return {"success": True}

        action = PreChatAction(
            id="explicit_action",
            name="Explicit Action",
            description="Explicitly enabled",
            plugin_id="plugin1",
            execute=mock_execute,
            default_enabled=False,
        )
        manager.register_action(action)

        # Mock LLM config with action explicitly enabled
        mock_config = Mock()
        mock_config.provider_options = {
            "pre_chat_actions": {
                "explicit_action": True,
            }
        }
        mock_llm_repository.get_configuration.return_value = mock_config

        enabled = manager.get_enabled_actions("test_config")
        assert len(enabled) == 1
        assert enabled[0].id == "explicit_action"

    def test_get_enabled_actions_explicit_false(self, manager, mock_llm_repository):
        """Action explicitly disabled in provider_options."""
        async def mock_execute():
            return {"success": True}

        action = PreChatAction(
            id="disabled_action",
            name="Disabled Action",
            description="Explicitly disabled",
            plugin_id="plugin1",
            execute=mock_execute,
            default_enabled=True,  # Default is True, but explicitly disabled
        )
        manager.register_action(action)

        # Mock LLM config with action explicitly disabled
        mock_config = Mock()
        mock_config.provider_options = {
            "pre_chat_actions": {
                "disabled_action": False,
            }
        }
        mock_llm_repository.get_configuration.return_value = mock_config

        enabled = manager.get_enabled_actions("test_config")
        assert len(enabled) == 0

    def test_get_enabled_actions_default_enabled(self, manager, mock_llm_repository):
        """No explicit setting, uses default_enabled=True."""
        async def mock_execute():
            return {"success": True}

        action = PreChatAction(
            id="default_enabled_action",
            name="Default Enabled",
            description="Default enabled action",
            plugin_id="plugin1",
            execute=mock_execute,
            default_enabled=True,
        )
        manager.register_action(action)

        # Mock LLM config with empty pre_chat_actions
        mock_config = Mock()
        mock_config.provider_options = {"pre_chat_actions": {}}
        mock_llm_repository.get_configuration.return_value = mock_config

        enabled = manager.get_enabled_actions("test_config")
        assert len(enabled) == 1
        assert enabled[0].id == "default_enabled_action"

    def test_get_enabled_actions_default_disabled(self, manager, mock_llm_repository):
        """No explicit setting, default_enabled=False means not returned."""
        async def mock_execute():
            return {"success": True}

        action = PreChatAction(
            id="default_disabled_action",
            name="Default Disabled",
            description="Default disabled action",
            plugin_id="plugin1",
            execute=mock_execute,
            default_enabled=False,
        )
        manager.register_action(action)

        # Mock LLM config with empty pre_chat_actions
        mock_config = Mock()
        mock_config.provider_options = {"pre_chat_actions": {}}
        mock_llm_repository.get_configuration.return_value = mock_config

        enabled = manager.get_enabled_actions("test_config")
        assert len(enabled) == 0

    def test_get_enabled_actions_no_config(self, manager, mock_llm_repository):
        """llm_repository returns None, returns empty list."""
        async def mock_execute():
            return {"success": True}

        action = PreChatAction(
            id="any_action",
            name="Any Action",
            description="Action",
            plugin_id="plugin1",
            execute=mock_execute,
            default_enabled=True,
        )
        manager.register_action(action)

        mock_llm_repository.get_configuration.return_value = None

        enabled = manager.get_enabled_actions("nonexistent_config")
        assert len(enabled) == 0

    def test_get_enabled_actions_no_provider_options(self, manager, mock_llm_repository):
        """Config exists but provider_options is None."""
        async def mock_execute():
            return {"success": True}

        action = PreChatAction(
            id="default_action",
            name="Default Action",
            description="Action",
            plugin_id="plugin1",
            execute=mock_execute,
            default_enabled=True,
        )
        manager.register_action(action)

        mock_config = Mock()
        mock_config.provider_options = None
        mock_llm_repository.get_configuration.return_value = mock_config

        enabled = manager.get_enabled_actions("test_config")
        assert len(enabled) == 1  # Default enabled actions should still work

    def test_get_enabled_actions_mixed_settings(self, manager, mock_llm_repository):
        """Multiple actions with different enable/disable settings."""
        async def mock_execute():
            return {"success": True}

        action1 = PreChatAction(
            id="action1",
            name="Action 1",
            description="Explicitly enabled",
            plugin_id="plugin1",
            execute=mock_execute,
            default_enabled=False,
        )
        action2 = PreChatAction(
            id="action2",
            name="Action 2",
            description="Explicitly disabled",
            plugin_id="plugin2",
            execute=mock_execute,
            default_enabled=True,
        )
        action3 = PreChatAction(
            id="action3",
            name="Action 3",
            description="Default enabled",
            plugin_id="plugin3",
            execute=mock_execute,
            default_enabled=True,
        )
        action4 = PreChatAction(
            id="action4",
            name="Action 4",
            description="Default disabled",
            plugin_id="plugin4",
            execute=mock_execute,
            default_enabled=False,
        )

        manager.register_action(action1)
        manager.register_action(action2)
        manager.register_action(action3)
        manager.register_action(action4)

        mock_config = Mock()
        mock_config.provider_options = {
            "pre_chat_actions": {
                "action1": True,   # Explicitly enabled
                "action2": False,  # Explicitly disabled
                # action3 not specified, uses default_enabled=True
                # action4 not specified, uses default_enabled=False
            }
        }
        mock_llm_repository.get_configuration.return_value = mock_config

        enabled = manager.get_enabled_actions("test_config")
        enabled_ids = {a.id for a in enabled}
        assert enabled_ids == {"action1", "action3"}


class TestExecuteActions:
    """Tests for action execution."""

    @pytest.mark.asyncio
    async def test_execute_actions_success(self, manager, mock_llm_repository):
        """Async execute returns success."""
        async def mock_execute():
            return {"success": True, "message": "Executed successfully"}

        action = PreChatAction(
            id="success_action",
            name="Success Action",
            description="Always succeeds",
            plugin_id="plugin1",
            execute=mock_execute,
            default_enabled=True,
        )
        manager.register_action(action)

        mock_config = Mock()
        mock_config.provider_options = {}
        mock_llm_repository.get_configuration.return_value = mock_config

        results = await manager.execute_actions("test_config")
        assert len(results) == 1
        assert results[0].action_id == "success_action"
        assert results[0].success is True
        assert results[0].message == "Executed successfully"
        assert results[0].error is None
        assert results[0].duration_ms > 0

    @pytest.mark.asyncio
    async def test_execute_actions_nonblocking_failure(self, manager, mock_llm_repository):
        """Non-blocking action fails, no exception raised, result has success=False."""
        async def mock_execute():
            return {"success": False, "error": "Action failed"}

        action = PreChatAction(
            id="failing_action",
            name="Failing Action",
            description="Returns failure",
            plugin_id="plugin1",
            execute=mock_execute,
            default_enabled=True,
            blocking=False,
        )
        manager.register_action(action)

        mock_config = Mock()
        mock_config.provider_options = {}
        mock_llm_repository.get_configuration.return_value = mock_config

        # Should not raise exception
        results = await manager.execute_actions("test_config")
        assert len(results) == 1
        assert results[0].action_id == "failing_action"
        assert results[0].success is False
        assert results[0].error == "Action failed"

    @pytest.mark.asyncio
    async def test_execute_actions_blocking_failure_raises(self, manager, mock_llm_repository):
        """Blocking action fails, raises PreChatActionError."""
        async def mock_execute():
            return {"success": False, "error": "Critical failure"}

        action = PreChatAction(
            id="blocking_action",
            name="Blocking Action",
            description="Blocking action",
            plugin_id="plugin1",
            execute=mock_execute,
            default_enabled=True,
            blocking=True,
        )
        manager.register_action(action)

        mock_config = Mock()
        mock_config.provider_options = {}
        mock_llm_repository.get_configuration.return_value = mock_config

        with pytest.raises(PreChatActionError) as exc_info:
            await manager.execute_actions("test_config")

        assert "blocking_action" in str(exc_info.value)
        assert "Critical failure" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_actions_exception_in_execute(self, manager, mock_llm_repository):
        """Execute raises Exception, handled gracefully."""
        async def mock_execute():
            raise ValueError("Unexpected error in action")

        action = PreChatAction(
            id="exception_action",
            name="Exception Action",
            description="Raises exception",
            plugin_id="plugin1",
            execute=mock_execute,
            default_enabled=True,
            blocking=False,
        )
        manager.register_action(action)

        mock_config = Mock()
        mock_config.provider_options = {}
        mock_llm_repository.get_configuration.return_value = mock_config

        results = await manager.execute_actions("test_config")
        assert len(results) == 1
        assert results[0].action_id == "exception_action"
        assert results[0].success is False
        assert "Unexpected error in action" in results[0].error
        assert results[0].duration_ms > 0

    @pytest.mark.asyncio
    async def test_execute_actions_exception_blocking_raises(self, manager, mock_llm_repository):
        """Blocking action raises exception, PreChatActionError is raised."""
        async def mock_execute():
            raise RuntimeError("Critical exception")

        action = PreChatAction(
            id="blocking_exception_action",
            name="Blocking Exception",
            description="Raises exception and is blocking",
            plugin_id="plugin1",
            execute=mock_execute,
            default_enabled=True,
            blocking=True,
        )
        manager.register_action(action)

        mock_config = Mock()
        mock_config.provider_options = {}
        mock_llm_repository.get_configuration.return_value = mock_config

        with pytest.raises(PreChatActionError) as exc_info:
            await manager.execute_actions("test_config")

        assert "blocking_exception_action" in str(exc_info.value)
        assert "Critical exception" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_actions_empty(self, manager, mock_llm_repository):
        """No enabled actions returns empty list."""
        # Register action but don't enable it
        async def mock_execute():
            return {"success": True}

        action = PreChatAction(
            id="disabled_action",
            name="Disabled",
            description="Not enabled",
            plugin_id="plugin1",
            execute=mock_execute,
            default_enabled=False,
        )
        manager.register_action(action)

        mock_config = Mock()
        mock_config.provider_options = {}
        mock_llm_repository.get_configuration.return_value = mock_config

        results = await manager.execute_actions("test_config")
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_execute_actions_multiple_concurrent(self, manager, mock_llm_repository):
        """Multiple actions execute concurrently."""
        executed_order = []

        async def mock_execute1():
            executed_order.append("action1_start")
            return {"success": True, "message": "Action 1"}

        async def mock_execute2():
            executed_order.append("action2_start")
            return {"success": True, "message": "Action 2"}

        action1 = PreChatAction(
            id="action1",
            name="Action 1",
            description="First",
            plugin_id="plugin1",
            execute=mock_execute1,
            default_enabled=True,
        )
        action2 = PreChatAction(
            id="action2",
            name="Action 2",
            description="Second",
            plugin_id="plugin2",
            execute=mock_execute2,
            default_enabled=True,
        )

        manager.register_action(action1)
        manager.register_action(action2)

        mock_config = Mock()
        mock_config.provider_options = {}
        mock_llm_repository.get_configuration.return_value = mock_config

        results = await manager.execute_actions("test_config")
        assert len(results) == 2

        result_ids = {r.action_id for r in results}
        assert result_ids == {"action1", "action2"}

        for result in results:
            assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_actions_partial_failure_blocking_stops(self, manager, mock_llm_repository):
        """One blocking action fails, execution stops with exception."""
        async def mock_execute_success():
            return {"success": True}

        async def mock_execute_fail():
            return {"success": False, "error": "Blocking failure"}

        action1 = PreChatAction(
            id="action1",
            name="Action 1",
            description="Non-blocking success",
            plugin_id="plugin1",
            execute=mock_execute_success,
            default_enabled=True,
            blocking=False,
        )
        action2 = PreChatAction(
            id="action2",
            name="Action 2",
            description="Blocking failure",
            plugin_id="plugin2",
            execute=mock_execute_fail,
            default_enabled=True,
            blocking=True,
        )

        manager.register_action(action1)
        manager.register_action(action2)

        mock_config = Mock()
        mock_config.provider_options = {}
        mock_llm_repository.get_configuration.return_value = mock_config

        with pytest.raises(PreChatActionError):
            await manager.execute_actions("test_config")


class TestDiscoverActions:
    """Tests for action discovery via plugin hooks."""

    def test_discover_actions_fires_hook(self, manager, mock_plugin_registry):
        """Verify execute_hook is called with correct args."""
        manager.discover_actions()

        mock_plugin_registry.execute_hook.assert_called_once()
        call_args = mock_plugin_registry.execute_hook.call_args

        # Check hook definition
        assert call_args[0][0] == CHAT_PRE_ACTIONS_HOOKS.register

        # Check initial_data contains manager
        initial_data = call_args[1]["initial_data"]
        assert "registry" in initial_data
        assert initial_data["registry"] is manager

    def test_discover_actions_allows_registration(self, manager, mock_plugin_registry):
        """Discover actions can trigger action registration via hook."""
        async def mock_execute():
            return {"success": True}

        # Simulate plugin hook registering an action
        def fake_hook_execute(hook_name, initial_data):
            mgr = initial_data["registry"]
            action = PreChatAction(
                id="discovered_action",
                name="Discovered Action",
                description="Registered via hook",
                plugin_id="discovery_plugin",
                execute=mock_execute,
            )
            mgr.register_action(action)

        mock_plugin_registry.execute_hook.side_effect = fake_hook_execute

        manager.discover_actions()

        # Verify action was registered
        all_actions = manager.get_all_actions()
        assert len(all_actions) == 1
        assert all_actions[0].id == "discovered_action"


class TestPreChatActionResult:
    """Tests for PreChatActionResult dataclass."""

    def test_result_creation(self):
        """Create a result with all fields."""
        result = PreChatActionResult(
            action_id="test_action",
            success=True,
            duration_ms=123.45,
            message="Action completed",
            error=None,
        )

        assert result.action_id == "test_action"
        assert result.success is True
        assert result.duration_ms == 123.45
        assert result.message == "Action completed"
        assert result.error is None

    def test_result_default_values(self):
        """Result has default values for optional fields."""
        result = PreChatActionResult(
            action_id="test_action",
            success=False,
        )

        assert result.duration_ms == 0.0
        assert result.message == ""
        assert result.error is None


class TestPreChatAction:
    """Tests for PreChatAction dataclass."""

    def test_action_creation(self):
        """Create an action with all fields."""
        async def mock_execute():
            return {"success": True}

        action = PreChatAction(
            id="test_action",
            name="Test Action",
            description="A test action",
            plugin_id="test_plugin",
            execute=mock_execute,
            default_enabled=True,
            blocking=True,
            category="security",
        )

        assert action.id == "test_action"
        assert action.name == "Test Action"
        assert action.description == "A test action"
        assert action.plugin_id == "test_plugin"
        assert action.execute == mock_execute
        assert action.default_enabled is True
        assert action.blocking is True
        assert action.category == "security"

    def test_action_default_values(self):
        """Action has default values for optional fields."""
        async def mock_execute():
            return {"success": True}

        action = PreChatAction(
            id="test_action",
            name="Test Action",
            description="A test action",
            plugin_id="test_plugin",
            execute=mock_execute,
        )

        assert action.default_enabled is False
        assert action.blocking is False
        assert action.category == "general"
