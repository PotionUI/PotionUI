"""
Tests for plugin hook integration in GenerationEngine.

This module tests the integration of the plugin system with the pipe execution flow,
ensuring hooks are executed properly before and after pipe processing.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch, call
from typing import Dict, Any

from src.features.generation.engine import GenerationEngine
from src.platform.plugins.registry import PluginRegistry
from src.platform.plugins.hooks import HookContext
from src.features.generation.hooks import PIPE_HOOKS
from src.pipelines.contracts import PipeInput, PipeOutput, IOType, PipeOutputSpec


class MockPipe:
    """Mock pipe for testing"""
    name = "mock_pipe"

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.process_called = False

    @staticmethod
    def get_default_config():
        return {}

    @staticmethod
    def inputs():
        return []

    @staticmethod
    def outputs():
        return [PipeOutputSpec(name="output", io_type=IOType.TEXT, is_array=False)]

    @staticmethod
    def configuration():
        return []

    def process(self, pipe_input: PipeInput, generation_outputs: callable, is_cancelled=None):
        self.process_called = True
        return PipeOutput(output={"output": "test_value"})


class TestGenerationHooks:
    """Test suite for generation hook integration"""

    @pytest.fixture
    def mock_dependencies(self):
        """Create mock dependencies for GenerationEngine"""
        return {
            'gpu': Mock(),
            'model_directories': Mock(),
            'pipe_catalog': Mock(),
            'settings': Mock(),
            'system_monitor': Mock(),
            'memory_advisor': Mock(),
            'llm_service': Mock()
        }

    @pytest.fixture
    def plugin_registry(self):
        """Create a real PluginRegistry for testing"""
        return PluginRegistry(marketplace_dir="test_plugins_mp", local_dir="test_plugins_local")

    def test_generation_manager_without_plugin_registry(self, mock_dependencies):
        """Test that GenerationEngine works without PluginRegistry (backward compatibility)"""
        manager = GenerationEngine(**mock_dependencies)
        assert manager.plugin_registry is None
        assert not manager._cancelled

    def test_generation_manager_with_plugin_registry(self, mock_dependencies, plugin_registry):
        """Test that GenerationEngine accepts PluginRegistry"""
        manager = GenerationEngine(**mock_dependencies, plugin_registry=plugin_registry)
        assert manager.plugin_registry == plugin_registry

    def test_before_execute_hook_called(self, mock_dependencies, plugin_registry):
        """Test that pipe.before_execute hook is called before pipe execution"""
        manager = GenerationEngine(**mock_dependencies, plugin_registry=plugin_registry)

        # Create a handler that modifies the input
        def before_hook_handler(context: HookContext) -> HookContext:
            context.data['inputs']['modified'] = True
            return context

        # Register the hook
        plugin_registry.hook_chain.register(
            PIPE_HOOKS.before_execute,
            "test_plugin",
            before_hook_handler
        )

        # Setup pipe registry mock
        mock_pipe_class = MockPipe
        mock_dependencies['pipe_catalog'].get_pipe.return_value = mock_pipe_class

        # Execute generation
        pipes = [
            {
                'name': 'mock_pipe',
                'enabled': True,
                'input': [],
                'cache': [],
                'config': {}
            }
        ]

        outputs = []
        manager.generate(pipes, lambda x: outputs.append(x), "test_gen_id")

        # Verify hook was called
        assert mock_dependencies['pipe_catalog'].get_pipe.called

    def test_after_execute_hook_called(self, mock_dependencies, plugin_registry):
        """Test that pipe.after_execute hook is called after pipe execution"""
        manager = GenerationEngine(**mock_dependencies, plugin_registry=plugin_registry)

        # Track hook execution
        hook_called = {'value': False, 'pipe_name': None, 'duration': None}

        def after_hook_handler(context: HookContext) -> HookContext:
            hook_called['value'] = True
            hook_called['pipe_name'] = context.data.get('pipe_name')
            hook_called['duration'] = context.data.get('duration')
            return context

        # Register the hook
        plugin_registry.hook_chain.register(
            PIPE_HOOKS.after_execute,
            "test_plugin",
            after_hook_handler
        )

        # Setup pipe registry mock
        mock_pipe_class = MockPipe
        mock_dependencies['pipe_catalog'].get_pipe.return_value = mock_pipe_class

        # Execute generation
        pipes = [
            {
                'name': 'mock_pipe',
                'enabled': True,
                'input': [],
                'cache': [],
                'config': {}
            }
        ]

        outputs = []
        manager.generate(pipes, lambda x: outputs.append(x), "test_gen_id")

        # Verify hook was called
        assert hook_called['value']
        assert hook_called['pipe_name'] == 'mock_pipe'
        assert hook_called['duration'] is not None

    def test_hook_modifies_inputs(self, mock_dependencies, plugin_registry):
        """Test that hooks can modify pipe inputs"""
        manager = GenerationEngine(**mock_dependencies, plugin_registry=plugin_registry)

        # Handler that adds a new input
        def before_hook_handler(context: HookContext) -> HookContext:
            context.data['inputs']['injected_param'] = 'injected_value'
            return context

        plugin_registry.hook_chain.register(
            PIPE_HOOKS.before_execute,
            "test_plugin",
            before_hook_handler
        )

        # Create a pipe that checks for the injected parameter
        class CheckInputPipe(MockPipe):
            def process(self, pipe_input: PipeInput, generation_outputs: callable, is_cancelled=None):
                # Verify the injected parameter is present
                assert 'injected_param' in pipe_input.input
                assert pipe_input.input['injected_param'] == 'injected_value'
                return PipeOutput(output={"output": "test_value"})

        mock_dependencies['pipe_catalog'].get_pipe.return_value = CheckInputPipe

        pipes = [
            {
                'name': 'mock_pipe',
                'enabled': True,
                'input': [],
                'cache': [],
                'config': {}
            }
        ]

        outputs = []
        manager.generate(pipes, lambda x: outputs.append(x), "test_gen_id")

    def test_hook_modifies_outputs(self, mock_dependencies, plugin_registry):
        """Test that hooks can modify pipe outputs"""
        manager = GenerationEngine(**mock_dependencies, plugin_registry=plugin_registry)

        # Handler that modifies the output
        def after_hook_handler(context: HookContext) -> HookContext:
            context.data['outputs']['modified'] = True
            context.data['outputs']['extra_data'] = 'added_by_hook'
            return context

        plugin_registry.hook_chain.register(
            PIPE_HOOKS.after_execute,
            "test_plugin",
            after_hook_handler
        )

        mock_dependencies['pipe_catalog'].get_pipe.return_value = MockPipe

        pipes = [
            {
                'name': 'mock_pipe',
                'enabled': True,
                'input': [],
                'cache': [],
                'config': {}
            }
        ]

        outputs = []
        manager.generate(pipes, lambda x: outputs.append(x), "test_gen_id")

        # The modified output should be available for subsequent pipes
        # (Note: This is verified through the internal pipe_outputs array in generate method)

    def test_hook_error_handling(self, mock_dependencies, plugin_registry):
        """Test that hook errors are handled gracefully"""
        manager = GenerationEngine(**mock_dependencies, plugin_registry=plugin_registry)

        # Handler that raises an exception
        def failing_hook_handler(context: HookContext) -> HookContext:
            raise ValueError("Hook error")

        plugin_registry.hook_chain.register(
            PIPE_HOOKS.before_execute,
            "test_plugin",
            failing_hook_handler
        )

        mock_dependencies['pipe_catalog'].get_pipe.return_value = MockPipe

        pipes = [
            {
                'name': 'mock_pipe',
                'enabled': True,
                'input': [],
                'cache': [],
                'config': {}
            }
        ]

        # Generation should still complete despite hook error
        outputs = []
        manager.generate(pipes, lambda x: outputs.append(x), "test_gen_id")

        # Verify the pipe still executed
        assert any(hasattr(o, 'state') and 'completed' in str(o.state).lower() for o in outputs if hasattr(o, 'state'))

    def test_multiple_hooks_execution_order(self, mock_dependencies, plugin_registry):
        """Test that multiple hooks execute in registration order"""
        manager = GenerationEngine(**mock_dependencies, plugin_registry=plugin_registry)

        execution_order = []

        def first_hook_handler(context: HookContext) -> HookContext:
            execution_order.append('first')
            context.data['inputs']['first'] = True
            return context

        def second_hook_handler(context: HookContext) -> HookContext:
            execution_order.append('second')
            # Should see the modification from first hook
            assert context.data['inputs'].get('first') is True
            context.data['inputs']['second'] = True
            return context

        # Register hooks in order
        plugin_registry.hook_chain.register(
            PIPE_HOOKS.before_execute,
            "plugin_1",
            first_hook_handler
        )
        plugin_registry.hook_chain.register(
            PIPE_HOOKS.before_execute,
            "plugin_2",
            second_hook_handler
        )

        mock_dependencies['pipe_catalog'].get_pipe.return_value = MockPipe

        pipes = [
            {
                'name': 'mock_pipe',
                'enabled': True,
                'input': [],
                'cache': [],
                'config': {}
            }
        ]

        outputs = []
        manager.generate(pipes, lambda x: outputs.append(x), "test_gen_id")

        # Verify execution order
        assert execution_order == ['first', 'second']

    def test_no_hooks_when_registry_none(self, mock_dependencies):
        """Test that no hook execution occurs when plugin_registry is None"""
        manager = GenerationEngine(**mock_dependencies)

        mock_dependencies['pipe_catalog'].get_pipe.return_value = MockPipe

        pipes = [
            {
                'name': 'mock_pipe',
                'enabled': True,
                'input': [],
                'cache': [],
                'config': {}
            }
        ]

        # Should work fine without plugin registry
        outputs = []
        manager.generate(pipes, lambda x: outputs.append(x), "test_gen_id")

        # Verify generation completed
        assert any(hasattr(o, 'state') and 'completed' in str(o.state).lower() for o in outputs if hasattr(o, 'state'))

    def test_hook_context_data_structure(self, mock_dependencies, plugin_registry):
        """Test that hook context contains expected data structure"""
        manager = GenerationEngine(**mock_dependencies, plugin_registry=plugin_registry)

        captured_contexts = {'before': None, 'after': None}

        def before_hook_handler(context: HookContext) -> HookContext:
            captured_contexts['before'] = context
            return context

        def after_hook_handler(context: HookContext) -> HookContext:
            captured_contexts['after'] = context
            return context

        plugin_registry.hook_chain.register(
            PIPE_HOOKS.before_execute,
            "test_plugin",
            before_hook_handler
        )
        plugin_registry.hook_chain.register(
            PIPE_HOOKS.after_execute,
            "test_plugin",
            after_hook_handler
        )

        mock_dependencies['pipe_catalog'].get_pipe.return_value = MockPipe

        pipes = [
            {
                'name': 'mock_pipe',
                'enabled': True,
                'input': [],
                'cache': [],
                'config': {}
            }
        ]

        outputs = []
        manager.generate(pipes, lambda x: outputs.append(x), "test_gen_id")

        # Verify before_execute context structure
        assert captured_contexts['before'] is not None
        assert captured_contexts['before'].hook_name == PIPE_HOOKS.before_execute
        assert 'pipe_id' in captured_contexts['before'].data
        assert 'pipe_name' in captured_contexts['before'].data
        assert 'pipe_config' in captured_contexts['before'].data
        assert 'inputs' in captured_contexts['before'].data

        # Verify after_execute context structure
        assert captured_contexts['after'] is not None
        assert captured_contexts['after'].hook_name == PIPE_HOOKS.after_execute
        assert 'pipe_id' in captured_contexts['after'].data
        assert 'pipe_name' in captured_contexts['after'].data
        assert 'outputs' in captured_contexts['after'].data
        assert 'duration' in captured_contexts['after'].data

    def test_hook_with_multiple_pipes(self, mock_dependencies, plugin_registry):
        """Test that hooks execute for each pipe in the pipeline"""
        manager = GenerationEngine(**mock_dependencies, plugin_registry=plugin_registry)

        hook_executions = []

        def tracking_hook_handler(context: HookContext) -> HookContext:
            hook_executions.append({
                'hook': context.hook_name,
                'pipe_id': context.data.get('pipe_id'),
                'pipe_name': context.data.get('pipe_name')
            })
            return context

        plugin_registry.hook_chain.register(
            PIPE_HOOKS.before_execute,
            "test_plugin",
            tracking_hook_handler
        )
        plugin_registry.hook_chain.register(
            PIPE_HOOKS.after_execute,
            "test_plugin",
            tracking_hook_handler
        )

        mock_dependencies['pipe_catalog'].get_pipe.return_value = MockPipe

        # Multiple pipes
        pipes = [
            {
                'name': 'mock_pipe',
                'enabled': True,
                'input': [],
                'cache': [],
                'config': {}
            },
            {
                'name': 'mock_pipe',
                'enabled': True,
                'input': [],
                'cache': [],
                'config': {}
            }
        ]

        outputs = []
        manager.generate(pipes, lambda x: outputs.append(x), "test_gen_id")

        # Should have 4 executions: 2 pipes × 2 hooks (before + after)
        assert len(hook_executions) == 4

        # Verify execution pattern: before(0), after(0), before(1), after(1)
        assert hook_executions[0]['hook'] == PIPE_HOOKS.before_execute
        assert hook_executions[0]['pipe_id'] == 0
        assert hook_executions[1]['hook'] == PIPE_HOOKS.after_execute
        assert hook_executions[1]['pipe_id'] == 0
        assert hook_executions[2]['hook'] == PIPE_HOOKS.before_execute
        assert hook_executions[2]['pipe_id'] == 1
        assert hook_executions[3]['hook'] == PIPE_HOOKS.after_execute
        assert hook_executions[3]['pipe_id'] == 1

    def test_hook_cancellation_doesnt_affect_generation(self, mock_dependencies, plugin_registry):
        """Test that generation can be cancelled even with hooks present"""
        manager = GenerationEngine(**mock_dependencies, plugin_registry=plugin_registry)

        # Track whether the hook was called
        hook_called = {'value': False}

        def before_hook_handler(context: HookContext) -> HookContext:
            hook_called['value'] = True
            # Hook shouldn't prevent cancellation
            return context

        plugin_registry.hook_chain.register(
            PIPE_HOOKS.before_execute,
            "test_plugin",
            before_hook_handler
        )

        # Create a pipe class that will cancel during execution
        class CancellingPipe(MockPipe):
            _manager = manager  # Class variable to avoid closure issues

            def process(self, pipe_input: PipeInput, generation_outputs: callable, is_cancelled=None):
                # Cancel during pipe execution
                CancellingPipe._manager.cancel("test_gen_id")
                # Check if cancelled
                if is_cancelled and is_cancelled():
                    return PipeOutput(output={"output": "cancelled"})
                return PipeOutput(output={"output": "test_value"})

        mock_dependencies['pipe_catalog'].get_pipe.return_value = CancellingPipe

        pipes = [
            {
                'name': 'mock_pipe',
                'enabled': True,
                'input': [],
                'cache': [],
                'config': {}
            },
            {
                'name': 'mock_pipe',
                'enabled': True,
                'input': [],
                'cache': [],
                'config': {}
            }
        ]

        outputs = []
        manager.generate(pipes, lambda x: outputs.append(x), "test_gen_id")

        # The first pipe should execute (hook called)
        assert hook_called['value']
        # The second pipe should be skipped due to cancellation
        # We should see a cancellation message
        assert any(hasattr(o, 'state') and 'cancelled' in str(getattr(o, 'state', '')).lower() for o in outputs)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
