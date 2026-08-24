"""Advanced tests for hook system - error handling, context manipulation, and performance"""

import unittest
from unittest.mock import MagicMock
import time

from src.platform.plugins.hooks import (
    HookChain,
    HookContext,
    HookResult,
    hooks_registry,
)
from src.features.generation.hooks import GENERATION_HOOKS


class TestHookContextAdvanced(unittest.TestCase):
    """Advanced tests for HookContext"""

    def test_context_immutability_of_metadata(self):
        """Test that metadata can be modified"""
        context = HookContext(
            hook_name="test.hook",
            plugin_id="test-plugin",
            data={"key": "value"},
            metadata={"meta": "original"}
        )

        # Metadata should be modifiable
        context.metadata["meta"] = "modified"
        self.assertEqual(context.metadata["meta"], "modified")

    def test_context_with_nested_data(self):
        """Test context with deeply nested data structures"""
        nested_data = {
            "level1": {
                "level2": {
                    "level3": {
                        "value": "deep"
                    }
                }
            }
        }

        context = HookContext(
            hook_name="test.hook",
            plugin_id="test-plugin",
            data=nested_data
        )

        # Should be able to access nested data
        self.assertEqual(
            context.data["level1"]["level2"]["level3"]["value"],
            "deep"
        )

    def test_context_with_none_values(self):
        """Test context handles None values correctly"""
        context = HookContext(
            hook_name="test.hook",
            plugin_id="test-plugin",
            data={"key": None}
        )

        self.assertIsNone(context.get("key"))
        self.assertTrue(context.has("key"))

    def test_context_update_overwrites_existing(self):
        """Test that update overwrites existing keys"""
        context = HookContext(
            hook_name="test.hook",
            plugin_id="test-plugin",
            data={"key1": "value1", "key2": "value2"}
        )

        context.update({"key1": "new_value1", "key3": "value3"})

        self.assertEqual(context.data["key1"], "new_value1")
        self.assertEqual(context.data["key2"], "value2")
        self.assertEqual(context.data["key3"], "value3")

    def test_context_with_list_data(self):
        """Test context with list data"""
        context = HookContext(
            hook_name="test.hook",
            plugin_id="test-plugin",
            data={"items": [1, 2, 3]}
        )

        items = context.get("items")
        items.append(4)
        context.set("items", items)

        self.assertEqual(len(context.data["items"]), 4)

    def test_context_get_with_callable_default(self):
        """Test get method with callable as default"""
        context = HookContext(
            hook_name="test.hook",
            plugin_id="test-plugin"
        )

        # Default should not be called if using get
        default = lambda: "computed"
        result = context.get("missing", default)

        # Should return the callable, not execute it
        self.assertEqual(result, default)


class TestHookChainAdvanced(unittest.TestCase):
    """Advanced tests for HookChain"""

    def setUp(self):
        """Set up test fixtures"""
        self.chain = HookChain()

    def test_handler_order_preservation(self):
        """Test that handlers execute in registration order"""
        order = []

        def handler1(ctx):
            order.append(1)
            return ctx

        def handler2(ctx):
            order.append(2)
            return ctx

        def handler3(ctx):
            order.append(3)
            return ctx

        self.chain.register("test.hook", "plugin-1", handler1)
        self.chain.register("test.hook", "plugin-2", handler2)
        self.chain.register("test.hook", "plugin-3", handler3)

        self.chain.execute("test.hook")

        self.assertEqual(order, [1, 2, 3])

    def test_handler_can_modify_and_pass_context(self):
        """Test that handlers can modify context for next handler"""
        def handler1(ctx):
            ctx.set("stage", 1)
            ctx.set("data", [1])
            return ctx

        def handler2(ctx):
            self.assertEqual(ctx.get("stage"), 1)
            ctx.set("stage", 2)
            data = ctx.get("data", [])
            data.append(2)
            ctx.set("data", data)
            return ctx

        def handler3(ctx):
            self.assertEqual(ctx.get("stage"), 2)
            ctx.set("stage", 3)
            data = ctx.get("data", [])
            data.append(3)
            ctx.set("data", data)
            return ctx

        self.chain.register("test.hook", "plugin-1", handler1)
        self.chain.register("test.hook", "plugin-2", handler2)
        self.chain.register("test.hook", "plugin-3", handler3)

        context, results = self.chain.execute("test.hook")

        self.assertEqual(context.get("stage"), 3)
        self.assertEqual(context.get("data"), [1, 2, 3])
        self.assertTrue(all(r.success for r in results))

    def test_handler_error_isolation(self):
        """Test that one handler's error doesn't affect others"""
        execution_log = []

        def handler1(ctx):
            execution_log.append("handler1")
            return ctx

        def handler2(ctx):
            execution_log.append("handler2")
            raise ValueError("Handler 2 failed")

        def handler3(ctx):
            execution_log.append("handler3")
            return ctx

        self.chain.register("test.hook", "plugin-1", handler1)
        self.chain.register("test.hook", "plugin-2", handler2)
        self.chain.register("test.hook", "plugin-3", handler3)

        context, results = self.chain.execute("test.hook")

        # All handlers should have attempted execution
        self.assertEqual(execution_log, ["handler1", "handler2", "handler3"])

        # Results should reflect the error
        self.assertEqual(len(results), 3)
        self.assertTrue(results[0].success)
        self.assertFalse(results[1].success)
        self.assertTrue(results[2].success)
        self.assertIsNotNone(results[1].error)

    def test_handler_with_timeout_simulation(self):
        """Test handler that takes time to execute"""
        def slow_handler(ctx):
            time.sleep(0.1)
            ctx.set("slow_completed", True)
            return ctx

        def fast_handler(ctx):
            ctx.set("fast_completed", True)
            return ctx

        self.chain.register("test.hook", "slow-plugin", slow_handler)
        self.chain.register("test.hook", "fast-plugin", fast_handler)

        start = time.time()
        context, results = self.chain.execute("test.hook")
        duration = time.time() - start

        # Should take at least 0.1 seconds
        self.assertGreater(duration, 0.1)

        # Both should complete
        self.assertTrue(context.get("slow_completed"))
        self.assertTrue(context.get("fast_completed"))

    def test_unregister_all_handlers_for_plugin(self):
        """Test unregistering a plugin from multiple hooks"""
        handler = MagicMock(return_value=HookContext("test", "plugin", {}))

        self.chain.register("hook1", "plugin-1", handler)
        self.chain.register("hook2", "plugin-1", handler)
        self.chain.register("hook3", "plugin-1", handler)

        # Unregister from each hook
        self.chain.unregister("hook1", "plugin-1")
        self.chain.unregister("hook2", "plugin-1")
        self.chain.unregister("hook3", "plugin-1")

        # All hooks should be empty for this plugin
        self.assertFalse(self.chain._handlers.get("hook1"))
        self.assertFalse(self.chain._handlers.get("hook2"))
        self.assertFalse(self.chain._handlers.get("hook3"))

    def test_handler_returns_none(self):
        """Test handler that returns None (invalid)"""
        def bad_handler(ctx):
            return None

        self.chain.register("test.hook", "bad-plugin", bad_handler)

        # Should handle gracefully
        context, results = self.chain.execute("test.hook")

        # Should have a result indicating failure or handle gracefully
        self.assertEqual(len(results), 1)

    def test_handler_modifies_original_data(self):
        """Test that handler can modify initial data"""
        initial_data = {"counter": 0, "items": []}

        def handler(ctx):
            counter = ctx.get("counter", 0)
            ctx.set("counter", counter + 1)

            items = ctx.get("items", [])
            items.append("item")
            ctx.set("items", items)

            return ctx

        self.chain.register("test.hook", "plugin-1", handler)

        context, results = self.chain.execute("test.hook", initial_data=initial_data)

        # Context data should have been modified by the handler
        self.assertEqual(context.get("counter"), 1)
        self.assertEqual(len(context.get("items", [])), 1)

    def test_multiple_executions_same_hook(self):
        """Test executing the same hook multiple times"""
        call_count = {"count": 0}

        def handler(ctx):
            call_count["count"] += 1
            ctx.set("execution_number", call_count["count"])
            return ctx

        self.chain.register("test.hook", "plugin-1", handler)

        # Execute multiple times
        for i in range(5):
            context, results = self.chain.execute("test.hook")
            self.assertEqual(context.get("execution_number"), i + 1)

        self.assertEqual(call_count["count"], 5)

    def test_handler_with_large_data(self):
        """Test handler with large data payload"""
        large_data = {"items": list(range(10000))}

        def handler(ctx):
            items = ctx.get("items", [])
            ctx.set("count", len(items))
            return ctx

        self.chain.register("test.hook", "plugin-1", handler)

        context, results = self.chain.execute("test.hook", initial_data=large_data)

        self.assertEqual(context.get("count"), 10000)
        self.assertTrue(results[0].success)

    def test_clear_handlers_after_execution(self):
        """Test clearing handlers after execution"""
        handler = MagicMock(return_value=HookContext("test", "plugin", {}))

        self.chain.register("test.hook", "plugin-1", handler)

        # Execute once
        self.chain.execute("test.hook")
        self.assertEqual(handler.call_count, 1)

        # Clear and execute again
        self.chain.clear_handlers("test.hook")
        self.chain.execute("test.hook")

        # Handler should still have been called only once
        self.assertEqual(handler.call_count, 1)

class TestHookRegistryAdvanced(unittest.TestCase):
    """Advanced tests for the open HookRegistry"""

    def test_all_hooks_have_types(self):
        """Test that all declared hooks have proper types"""
        for spec in hooks_registry.all():
            self.assertIn(spec.type, ["backend", "frontend"])

    def test_hook_name_uniqueness(self):
        """Test that all declared hook names are unique"""
        hook_names = [spec.name for spec in hooks_registry.all()]
        self.assertEqual(len(hook_names), len(set(hook_names)))

    def test_backend_hook_count(self):
        """Test that we have registered backend hooks"""
        backend_hooks = [spec for spec in hooks_registry.all() if spec.type == "backend"]
        self.assertGreater(len(backend_hooks), 0)

    def test_frontend_hook_count(self):
        """Test that we have registered frontend hooks"""
        frontend_hooks = [spec for spec in hooks_registry.all() if spec.type == "frontend"]
        self.assertGreater(len(frontend_hooks), 0)

    def test_hook_can_be_used_as_string(self):
        """Test that declared hooks are plain strings"""
        hook = GENERATION_HOOKS.before_start
        hook_str = str(hook)

        self.assertIsInstance(hook_str, str)
        self.assertTrue(len(hook_str) > 0)


class TestHookResultAdvanced(unittest.TestCase):
    """Advanced tests for HookResult"""

    def test_result_with_execution_time(self):
        """Test that result tracks execution metadata"""
        result = HookResult(
            plugin_id="test-plugin",
            success=True,
            modified=True,
            error=None
        )

        self.assertEqual(result.plugin_id, "test-plugin")
        self.assertTrue(result.success)
        self.assertTrue(result.modified)
        self.assertIsNone(result.error)

    def test_result_with_error_details(self):
        """Test result with detailed error information"""
        error_msg = "Detailed error message"
        result = HookResult(
            plugin_id="failing-plugin",
            success=False,
            modified=False,
            error=error_msg
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error, error_msg)

    def test_result_success_without_modification(self):
        """Test that handler can succeed without modifying context"""
        result = HookResult(
            plugin_id="read-only-plugin",
            success=True,
            modified=False,
            error=None
        )

        self.assertTrue(result.success)
        self.assertFalse(result.modified)


if __name__ == '__main__':
    unittest.main()
