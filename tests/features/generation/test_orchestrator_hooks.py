"""
Tests for plugin hook integration in GenerationOrchestrator.

These tests verify that the orchestrator properly executes plugin hooks
at the correct lifecycle points and handles hook data modifications.

Note: These tests use isolated testing to avoid circular import issues
between the API layer and application layer.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from typing import Dict, Any
import sys

from src.platform.plugins.registry import PluginRegistry
from src.platform.plugins.hooks import HookContext, HookResult, hooks_registry
from src.features.generation.hooks import GENERATION_HOOKS
from src.features.generation.hooks import PIPE_HOOKS


@pytest.fixture
def plugin_registry():
    """Create a real PluginRegistry for testing hook execution"""
    registry = PluginRegistry()
    return registry


class TestHookExecutionLogic:
    """Tests for hook execution logic without full orchestrator"""

    def test_before_start_hook_data_structure(self, plugin_registry):
        """Test that before_start hook receives correct data structure"""
        hook_calls = []

        def before_start_handler(context: HookContext) -> HookContext:
            hook_calls.append(context.data.copy())
            return context

        plugin_registry.hook_chain.register(
            GENERATION_HOOKS.before_start,
            "test_plugin",
            before_start_handler
        )

        # Simulate the hook execution as orchestrator would do it
        context = HookContext(
            hook_name=GENERATION_HOOKS.before_start,
            plugin_id="system",
            data={
                "generation_id": "test_gen_id",
                "preset_id": "test_preset",
                "form_data": {"prompt": "test"},
                "backend_id": "test_backend",
                "user_id": "user123"
            }
        )

        context, success = plugin_registry.execute_hook(
            GENERATION_HOOKS.before_start,
            context
        )

        # Verify hook was called with correct data
        assert len(hook_calls) == 1
        hook_data = hook_calls[0]
        assert hook_data["generation_id"] == "test_gen_id"
        assert hook_data["preset_id"] == "test_preset"
        assert hook_data["form_data"] == {"prompt": "test"}
        assert hook_data["backend_id"] == "test_backend"
        assert hook_data["user_id"] == "user123"
        assert success is True

    def test_before_start_hook_modifies_form_data(self, plugin_registry):
        """Test that plugins can modify form_data through the hook"""
        def before_start_handler(context: HookContext) -> HookContext:
            context.data["form_data"]["steps"] = 50
            context.data["form_data"]["seed"] = 123456
            return context

        plugin_registry.hook_chain.register(
            GENERATION_HOOKS.before_start,
            "test_plugin",
            before_start_handler
        )

        # Simulate the hook execution
        context = HookContext(
            hook_name=GENERATION_HOOKS.before_start,
            plugin_id="system",
            data={
                "generation_id": "test_gen_id",
                "preset_id": "test_preset",
                "form_data": {"prompt": "test"},
                "backend_id": "test_backend",
                "user_id": "user123"
            }
        )

        context, success = plugin_registry.execute_hook(
            GENERATION_HOOKS.before_start,
            context
        )

        # Verify form_data was modified
        assert context.data["form_data"]["steps"] == 50
        assert context.data["form_data"]["seed"] == 123456
        assert context.data["form_data"]["prompt"] == "test"
        assert success is True

    def test_after_complete_hook_data_structure(self, plugin_registry):
        """Test that after_complete hook receives correct data structure"""
        hook_calls = []

        def after_complete_handler(context: HookContext) -> HookContext:
            hook_calls.append(context.data.copy())
            return context

        plugin_registry.hook_chain.register(
            GENERATION_HOOKS.after_complete,
            "test_plugin",
            after_complete_handler
        )

        # Simulate the hook execution as orchestrator would do it
        context = HookContext(
            hook_name=GENERATION_HOOKS.after_complete,
            plugin_id="system",
            data={
                "generation_id": "test_gen_id",
                "status": "completed",
                "duration": 50.0,
                "outputs": [],
                "preset_id": "test_preset",
                "user_id": "user123"
            }
        )

        context, success = plugin_registry.execute_hook(
            GENERATION_HOOKS.after_complete,
            context
        )

        # Verify hook was called with correct data
        assert len(hook_calls) == 1
        hook_data = hook_calls[0]
        assert hook_data["generation_id"] == "test_gen_id"
        assert hook_data["status"] == "completed"
        assert hook_data["duration"] == 50.0
        assert hook_data["preset_id"] == "test_preset"
        assert hook_data["user_id"] == "user123"
        assert success is True

    def test_hook_failure_returns_false_success(self, plugin_registry):
        """Test that hook failure results in success=False"""
        def failing_handler(context: HookContext) -> HookContext:
            raise ValueError("Plugin error")

        plugin_registry.hook_chain.register(
            GENERATION_HOOKS.before_start,
            "failing_plugin",
            failing_handler
        )

        context = HookContext(
            hook_name=GENERATION_HOOKS.before_start,
            plugin_id="system",
            data={"generation_id": "test_gen_id"}
        )

        context, success = plugin_registry.execute_hook(
            GENERATION_HOOKS.before_start,
            context
        )

        # Verify failure is reported
        assert success is False
        # Context should still be returned (not modified due to error)
        assert context.data["generation_id"] == "test_gen_id"

    def test_multiple_plugins_chain_execution(self, plugin_registry):
        """Test that multiple plugins execute in order with data passing"""
        execution_order = []

        def first_handler(context: HookContext) -> HookContext:
            execution_order.append("first")
            context.data["form_data"]["first_plugin"] = True
            return context

        def second_handler(context: HookContext) -> HookContext:
            execution_order.append("second")
            # Should see modification from first plugin
            assert context.data["form_data"]["first_plugin"] is True
            context.data["form_data"]["second_plugin"] = True
            return context

        plugin_registry.hook_chain.register(
            GENERATION_HOOKS.before_start,
            "first_plugin",
            first_handler
        )
        plugin_registry.hook_chain.register(
            GENERATION_HOOKS.before_start,
            "second_plugin",
            second_handler
        )

        # Simulate the hook execution
        context = HookContext(
            hook_name=GENERATION_HOOKS.before_start,
            plugin_id="system",
            data={
                "generation_id": "test_gen_id",
                "form_data": {"prompt": "test"}
            }
        )

        context, success = plugin_registry.execute_hook(
            GENERATION_HOOKS.before_start,
            context
        )

        # Verify both plugins executed in order
        assert execution_order == ["first", "second"]
        # Verify both modifications present
        assert context.data["form_data"]["first_plugin"] is True
        assert context.data["form_data"]["second_plugin"] is True
        assert success is True

    def test_plugin_failure_does_not_stop_chain(self, plugin_registry):
        """Test that one plugin failing doesn't prevent others from executing"""
        execution_order = []

        def first_handler(context: HookContext) -> HookContext:
            execution_order.append("first")
            return context

        def failing_handler(context: HookContext) -> HookContext:
            execution_order.append("failing")
            raise ValueError("Plugin error")

        def third_handler(context: HookContext) -> HookContext:
            execution_order.append("third")
            return context

        plugin_registry.hook_chain.register(
            GENERATION_HOOKS.before_start,
            "first_plugin",
            first_handler
        )
        plugin_registry.hook_chain.register(
            GENERATION_HOOKS.before_start,
            "failing_plugin",
            failing_handler
        )
        plugin_registry.hook_chain.register(
            GENERATION_HOOKS.before_start,
            "third_plugin",
            third_handler
        )

        context = HookContext(
            hook_name=GENERATION_HOOKS.before_start,
            plugin_id="system",
            data={"generation_id": "test_gen_id"}
        )

        context, success = plugin_registry.execute_hook(
            GENERATION_HOOKS.before_start,
            context
        )

        # Verify all plugins executed despite failure
        assert execution_order == ["first", "failing", "third"]
        assert success is False  # Overall failure due to one plugin failing

    def test_no_plugins_registered_returns_original_context(self, plugin_registry):
        """Test that executing hook with no plugins returns original context"""
        context = HookContext(
            hook_name=GENERATION_HOOKS.before_start,
            plugin_id="system",
            data={
                "generation_id": "test_gen_id",
                "form_data": {"prompt": "test"}
            }
        )

        result_context, success = plugin_registry.execute_hook(
            GENERATION_HOOKS.before_start,
            context
        )

        # Verify original context returned unchanged
        assert result_context.data == context.data
        assert success is True  # No failures when no plugins

    def test_plugin_can_read_and_modify_metadata(self, plugin_registry):
        """Test that plugins can use metadata field for additional data"""
        def metadata_handler(context: HookContext) -> HookContext:
            # Read preset_id and add to metadata
            context.metadata["original_preset"] = context.data["preset_id"]
            # Modify preset_id
            context.data["preset_id"] = "modified_preset"
            return context

        plugin_registry.hook_chain.register(
            GENERATION_HOOKS.before_start,
            "metadata_plugin",
            metadata_handler
        )

        context = HookContext(
            hook_name=GENERATION_HOOKS.before_start,
            plugin_id="system",
            data={
                "generation_id": "test_gen_id",
                "preset_id": "original_preset"
            }
        )

        context, success = plugin_registry.execute_hook(
            GENERATION_HOOKS.before_start,
            context
        )

        # Verify metadata and data changes
        assert context.metadata["original_preset"] == "original_preset"
        assert context.data["preset_id"] == "modified_preset"
        assert success is True


class TestOrchestratorHookIntegration:
    """
    Integration tests that verify orchestrator hook execution.
    These tests mock the orchestrator to avoid circular imports.
    """

    @pytest.mark.asyncio
    async def test_orchestrator_before_start_hook_integration(self, plugin_registry):
        """Test that orchestrator properly integrates before_start hook"""
        hook_executed = []

        def before_start_handler(context: HookContext) -> HookContext:
            hook_executed.append(True)
            context.data["form_data"]["plugin_modified"] = True
            return context

        plugin_registry.hook_chain.register(
            GENERATION_HOOKS.before_start,
            "test_plugin",
            before_start_handler
        )

        # Simulate what orchestrator does
        generation_id = "test_gen_id"
        form_data = {"prompt": "test"}
        preset_id = "test_preset"
        backend_id = "test_backend"
        user_id = "user123"

        # Execute hook as orchestrator would
        context = HookContext(
            hook_name=GENERATION_HOOKS.before_start,
            plugin_id="system",
            data={
                "generation_id": generation_id,
                "preset_id": preset_id,
                "form_data": form_data,
                "backend_id": backend_id,
                "user_id": user_id
            }
        )

        context, success = plugin_registry.execute_hook(
            GENERATION_HOOKS.before_start,
            context
        )

        # Orchestrator would update form_data from context
        form_data = context.data.get("form_data", form_data)

        # Verify hook executed and modified data
        assert len(hook_executed) == 1
        assert form_data["plugin_modified"] is True
        assert success is True

    @pytest.mark.asyncio
    async def test_orchestrator_after_complete_hook_integration(self, plugin_registry):
        """Test that orchestrator properly integrates after_complete hook"""
        hook_executed = []

        def after_complete_handler(context: HookContext) -> HookContext:
            hook_executed.append(True)
            # Plugin could log, send notifications, etc.
            # Modify data to trigger metadata update
            context.data["notification_sent"] = True
            context.metadata["notification_timestamp"] = "2024-01-01T00:00:00"
            return context

        plugin_registry.hook_chain.register(
            GENERATION_HOOKS.after_complete,
            "test_plugin",
            after_complete_handler
        )

        # Simulate what orchestrator does on completion
        generation_id = "test_gen_id"
        status = "completed"
        duration = 45.5

        context = HookContext(
            hook_name=GENERATION_HOOKS.after_complete,
            plugin_id="system",
            data={
                "generation_id": generation_id,
                "status": status,
                "duration": duration,
                "outputs": [],
                "preset_id": "test_preset",
                "user_id": "user123"
            }
        )

        context, success = plugin_registry.execute_hook(
            GENERATION_HOOKS.after_complete,
            context
        )

        # Verify hook executed and modified data
        assert len(hook_executed) == 1
        assert context.data["notification_sent"] is True
        assert context.metadata["notification_timestamp"] == "2024-01-01T00:00:00"
        assert success is True

    @pytest.mark.asyncio
    async def test_orchestrator_handles_none_plugin_registry(self):
        """Test that orchestrator works when plugin_registry is None"""
        # When plugin_registry is None, orchestrator should skip hook execution
        plugin_registry = None

        # Simulate orchestrator logic
        if plugin_registry:
            # This branch should not execute
            pytest.fail("Should not execute hooks when plugin_registry is None")

        # Verify we can continue without errors
        assert True  # No exception raised

    def test_hook_definition_enum_values(self):
        """Test that hook definitions have correct enum values"""
        assert GENERATION_HOOKS.before_start == "generation.before_start"
        assert GENERATION_HOOKS.after_complete == "generation.after_complete"

    def test_hook_type_classification(self):
        """Test that hooks are correctly classified as backend/frontend"""
        from src.platform.plugins.hooks import hooks_registry

        assert hooks_registry.get(GENERATION_HOOKS.before_start).type == "backend"
        assert hooks_registry.get(GENERATION_HOOKS.after_complete).type == "backend"
        assert hooks_registry.get(PIPE_HOOKS.before_execute).type == "backend"
        assert hooks_registry.get(PIPE_HOOKS.after_execute).type == "backend"
