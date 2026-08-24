"""Tests for the hook system"""

import unittest
from unittest.mock import MagicMock

from src.platform.plugins.hooks import (
    HookChain,
    HookContext,
    HookResult,
    HookRegistry,
    execute_hook,
)
from src.features.generation.hooks import GENERATION_HOOKS, PIPE_HOOKS
from src.features.providers.hooks import PROVIDER_HOOKS
from src.features.backends.hooks import BACKEND_HOOKS
from src.platform.plugins.frontend_hooks import WORKBENCH_HOOKS


class TestHookRegistry(unittest.TestCase):
    """Test the open HookRegistry (declare/get/all)"""

    def test_backend_hooks(self):
        """Test that domain-declared backend hooks are registered as such"""
        backend_hooks = [
            GENERATION_HOOKS.before_start,
            GENERATION_HOOKS.after_complete,
            PIPE_HOOKS.before_execute,
            PIPE_HOOKS.after_execute,
            PROVIDER_HOOKS.register,
            PROVIDER_HOOKS.settings_panel,
            BACKEND_HOOKS.register,
        ]

        from src.platform.plugins.hooks import hooks_registry
        for hook_name in backend_hooks:
            spec = hooks_registry.get(hook_name)
            self.assertIsNotNone(spec, f"{hook_name} should be declared")
            self.assertEqual(spec.type, "backend")

    def test_frontend_hooks(self):
        """Test that domain-declared frontend hooks are registered as such"""
        frontend_hooks = [
            WORKBENCH_HOOKS.actions,
            WORKBENCH_HOOKS.image_click,
            WORKBENCH_HOOKS.tools,
        ]

        from src.platform.plugins.hooks import hooks_registry
        for hook_name in frontend_hooks:
            spec = hooks_registry.get(hook_name)
            self.assertIsNotNone(spec, f"{hook_name} should be declared")
            self.assertEqual(spec.type, "frontend")

    def test_declare_returns_full_names(self):
        """Test that declare() returns a namespace of dotted hook name strings"""
        registry = HookRegistry()
        ns = registry.declare("widget", "backend", "before_create", "after_create")

        self.assertEqual(ns.before_create, "widget.before_create")
        self.assertEqual(ns.after_create, "widget.after_create")

    def test_declare_is_idempotent(self):
        """Test that redeclaring the same hook with the same type is a no-op"""
        registry = HookRegistry()
        registry.declare("widget", "backend", "before_create")
        registry.declare("widget", "backend", "before_create")

        self.assertIsNotNone(registry.get("widget.before_create"))

    def test_declare_conflict_raises(self):
        """Test that redeclaring a hook with a different type raises"""
        registry = HookRegistry()
        registry.declare("widget", "backend", "before_create")

        with self.assertRaises(ValueError):
            registry.declare("widget", "frontend", "before_create")

    def test_get_unknown_hook_returns_none(self):
        """Test that looking up an undeclared hook returns None"""
        registry = HookRegistry()
        self.assertIsNone(registry.get("does.not.exist"))

    def test_declare_with_specs_populates_structured_docs(self):
        """Test that `specs=` populates payload/mutable/use_when/example"""
        registry = HookRegistry()
        registry.declare(
            "widget", "backend", "before_create",
            specs={
                "before_create": {
                    "description": "Fires before a widget is created",
                    "payload": {"widget_id": {"type": "str", "description": "The widget id"}},
                    "mutable": ["form_data"],
                    "use_when": ["Rewrite form data before creation"],
                    "example": "def handler(ctx): ...",
                },
            },
        )
        spec = registry.get("widget.before_create")
        self.assertEqual(spec.description, "Fires before a widget is created")
        self.assertEqual(spec.payload, {"widget_id": {"type": "str", "description": "The widget id"}})
        self.assertEqual(spec.mutable, ("form_data",))
        self.assertEqual(spec.use_when, ("Rewrite form data before creation",))
        self.assertEqual(spec.example, "def handler(ctx): ...")

    def test_declare_specs_normalizes_lists_to_tuples(self):
        """Test that list values for mutable/use_when are normalized to tuples"""
        registry = HookRegistry()
        registry.declare(
            "widget", "backend", "before_create",
            specs={"before_create": {"mutable": ["a", "b"], "use_when": ["x"]}},
        )
        spec = registry.get("widget.before_create")
        self.assertIsInstance(spec.mutable, tuple)
        self.assertIsInstance(spec.use_when, tuple)
        self.assertEqual(spec.mutable, ("a", "b"))

    def test_declare_specs_unknown_key_raises(self):
        """Test that an unrecognized key in a spec dict raises ValueError (typo guard)"""
        registry = HookRegistry()
        with self.assertRaises(ValueError):
            registry.declare(
                "widget", "backend", "before_create",
                specs={"before_create": {"descriptoin": "typo"}},
            )

    def test_declare_specs_override_descriptions(self):
        """Test that `specs` description overrides `descriptions` for the same name"""
        registry = HookRegistry()
        registry.declare(
            "widget", "backend", "before_create",
            descriptions={"before_create": "from descriptions"},
            specs={"before_create": {"description": "from specs"}},
        )
        self.assertEqual(registry.get("widget.before_create").description, "from specs")

    def test_declare_one_accepts_structured_fields(self):
        """Test that declare_one accepts payload/mutable/use_when/example"""
        registry = HookRegistry()
        registry.declare_one(
            "plugin.custom_event",
            "backend",
            description="Custom plugin hook",
            payload={"value": {"type": "int", "description": "The value"}},
            mutable=["value"],
            use_when=["Adjust the value before it is used"],
            example="hooks.backend: [{hook: plugin.custom_event, handler: mod.fn}]",
        )
        spec = registry.get("plugin.custom_event")
        self.assertEqual(spec.payload, {"value": {"type": "int", "description": "The value"}})
        self.assertEqual(spec.mutable, ("value",))
        self.assertEqual(spec.use_when, ("Adjust the value before it is used",))
        self.assertTrue(spec.example.startswith("hooks.backend"))

    def test_redeclare_with_different_docs_last_wins_no_error(self):
        """Test that redeclaring with the same type but different docs is allowed (last-declare-wins)"""
        registry = HookRegistry()
        registry.declare("widget", "backend", "before_create", specs={"before_create": {"description": "v1"}})
        registry.declare("widget", "backend", "before_create", specs={"before_create": {"description": "v2"}})
        self.assertEqual(registry.get("widget.before_create").description, "v2")

    def test_redeclare_conflicting_type_still_raises(self):
        """Test that a type conflict still raises even with structured specs present"""
        registry = HookRegistry()
        registry.declare("widget", "backend", "before_create")
        with self.assertRaises(ValueError):
            registry.declare("widget", "frontend", "before_create", specs={"before_create": {"description": "x"}})


class TestHookContext(unittest.TestCase):
    """Test HookContext dataclass"""

    def test_create_context(self):
        """Test creating a hook context"""
        context = HookContext(
            hook_name="test.hook",
            plugin_id="test-plugin",
            data={"key": "value"},
            metadata={"meta": "data"}
        )

        self.assertEqual(context.hook_name, "test.hook")
        self.assertEqual(context.plugin_id, "test-plugin")
        self.assertEqual(context.data["key"], "value")
        self.assertEqual(context.metadata["meta"], "data")

    def test_get_method(self):
        """Test get method with default value"""
        context = HookContext(
            hook_name="test.hook",
            plugin_id="test-plugin",
            data={"key": "value"}
        )

        self.assertEqual(context.get("key"), "value")
        self.assertEqual(context.get("missing", "default"), "default")
        self.assertIsNone(context.get("missing"))

    def test_set_method(self):
        """Test set method"""
        context = HookContext(
            hook_name="test.hook",
            plugin_id="test-plugin"
        )

        context.set("key", "value")
        self.assertEqual(context.data["key"], "value")

    def test_has_method(self):
        """Test has method"""
        context = HookContext(
            hook_name="test.hook",
            plugin_id="test-plugin",
            data={"key": "value"}
        )

        self.assertTrue(context.has("key"))
        self.assertFalse(context.has("missing"))

    def test_update_method(self):
        """Test update method"""
        context = HookContext(
            hook_name="test.hook",
            plugin_id="test-plugin",
            data={"key1": "value1"}
        )

        context.update({"key2": "value2", "key3": "value3"})
        self.assertEqual(context.data["key1"], "value1")
        self.assertEqual(context.data["key2"], "value2")
        self.assertEqual(context.data["key3"], "value3")


class TestHookChain(unittest.TestCase):
    """Test HookChain functionality"""

    def setUp(self):
        """Set up test fixtures"""
        self.chain = HookChain()

    def test_register_handler(self):
        """Test registering a handler"""
        handler = MagicMock(return_value=HookContext("test", "plugin", {}))

        self.chain.register("test.hook", "plugin-1", handler)

        handlers = self.chain._handlers.get("test.hook", [])
        self.assertEqual(len(handlers), 1)
        self.assertEqual(handlers[0][0], "plugin-1")

    def test_register_multiple_handlers(self):
        """Test registering multiple handlers for same hook"""
        handler1 = MagicMock(return_value=HookContext("test", "plugin1", {}))
        handler2 = MagicMock(return_value=HookContext("test", "plugin2", {}))

        self.chain.register("test.hook", "plugin-1", handler1)
        self.chain.register("test.hook", "plugin-2", handler2)

        handlers = self.chain._handlers.get("test.hook", [])
        self.assertEqual(len(handlers), 2)

    def test_replace_existing_handler(self):
        """Test that registering same plugin twice replaces the handler"""
        handler1 = MagicMock(return_value=HookContext("test", "plugin", {}))
        handler2 = MagicMock(return_value=HookContext("test", "plugin", {}))

        self.chain.register("test.hook", "plugin-1", handler1)
        self.chain.register("test.hook", "plugin-1", handler2)

        handlers = self.chain._handlers.get("test.hook", [])
        self.assertEqual(len(handlers), 1)
        self.assertEqual(handlers[0][1], handler2)

    def test_unregister_handler(self):
        """Test unregistering a handler"""
        handler = MagicMock(return_value=HookContext("test", "plugin", {}))

        self.chain.register("test.hook", "plugin-1", handler)
        self.assertTrue(self.chain._handlers.get("test.hook"))

        result = self.chain.unregister("test.hook", "plugin-1")
        self.assertTrue(result)
        self.assertFalse(self.chain._handlers.get("test.hook"))

    def test_unregister_nonexistent_handler(self):
        """Test unregistering a handler that doesn't exist"""
        result = self.chain.unregister("test.hook", "plugin-1")
        self.assertFalse(result)

    def test_execute_no_handlers(self):
        """Test executing hook with no handlers"""
        context, results = self.chain.execute(
            "test.hook",
            initial_data={"key": "value"}
        )

        self.assertEqual(context.hook_name, "test.hook")
        self.assertEqual(context.data["key"], "value")
        self.assertEqual(len(results), 0)

    def test_execute_single_handler(self):
        """Test executing hook with single handler"""
        def handler(ctx):
            ctx.set("modified", True)
            return ctx

        self.chain.register("test.hook", "plugin-1", handler)

        context, results = self.chain.execute(
            "test.hook",
            initial_data={"key": "value"}
        )

        self.assertEqual(context.data["key"], "value")
        self.assertTrue(context.data["modified"])
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].success)
        self.assertTrue(results[0].modified)

    def test_execute_chain_modification(self):
        """Test that handlers can modify context in chain"""
        def handler1(ctx):
            ctx.set("step1", "done")
            return ctx

        def handler2(ctx):
            # Should see step1 from handler1
            self.assertTrue(ctx.has("step1"))
            ctx.set("step2", "done")
            return ctx

        self.chain.register("test.hook", "plugin-1", handler1)
        self.chain.register("test.hook", "plugin-2", handler2)

        context, results = self.chain.execute("test.hook")

        self.assertTrue(context.has("step1"))
        self.assertTrue(context.has("step2"))
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.success for r in results))

    def test_execute_handler_error(self):
        """Test that handler errors are caught and don't break the chain"""
        def handler1(ctx):
            ctx.set("step1", "done")
            return ctx

        def handler2(ctx):
            raise ValueError("Test error")

        def handler3(ctx):
            ctx.set("step3", "done")
            return ctx

        self.chain.register("test.hook", "plugin-1", handler1)
        self.chain.register("test.hook", "plugin-2", handler2)
        self.chain.register("test.hook", "plugin-3", handler3)

        context, results = self.chain.execute("test.hook")

        # Handler 1 and 3 should succeed, handler 2 should fail
        self.assertTrue(context.has("step1"))
        self.assertTrue(context.has("step3"))
        self.assertEqual(len(results), 3)
        self.assertTrue(results[0].success)
        self.assertFalse(results[1].success)
        self.assertTrue(results[2].success)
        self.assertIsNotNone(results[1].error)

    def test_clear_handlers_specific(self):
        """Test clearing handlers for specific hook"""
        handler = MagicMock(return_value=HookContext("test", "plugin", {}))

        self.chain.register("hook1", "plugin-1", handler)
        self.chain.register("hook2", "plugin-1", handler)

        self.chain.clear_handlers("hook1")

        self.assertFalse(self.chain._handlers.get("hook1"))
        self.assertTrue(self.chain._handlers.get("hook2"))

    def test_clear_all_handlers(self):
        """Test clearing all handlers"""
        handler = MagicMock(return_value=HookContext("test", "plugin", {}))

        self.chain.register("hook1", "plugin-1", handler)
        self.chain.register("hook2", "plugin-1", handler)

        self.chain.clear_handlers()

        self.assertFalse(self.chain._handlers.get("hook1"))
        self.assertFalse(self.chain._handlers.get("hook2"))


class TestExecuteHook(unittest.TestCase):
    """Test the module-level execute_hook helper shared by every manager."""

    def test_returns_context_data_and_unblocked(self):
        """Should return the (possibly mutated) context data with blocked=False by default."""
        mock_context = MagicMock()
        mock_context.data = {"key": "value"}
        plugins = MagicMock()
        plugins.execute_hook.return_value = (mock_context, [])

        data, blocked = execute_hook(plugins, "domain.some_hook", {"key": "value"})

        self.assertEqual(data, {"key": "value"})
        self.assertFalse(blocked)
        plugins.execute_hook.assert_called_once_with(
            "domain.some_hook", initial_data={"key": "value"}
        )

    def test_detects_blocked_flag(self):
        """Should report blocked=True when a handler set it on the context."""
        mock_context = MagicMock()
        mock_context.data = {"blocked": True, "block_reason": "vetoed"}
        plugins = MagicMock()
        plugins.execute_hook.return_value = (mock_context, [])

        data, blocked = execute_hook(plugins, "domain.some_hook", {})

        self.assertTrue(blocked)
        self.assertEqual(data["block_reason"], "vetoed")


if __name__ == '__main__':
    unittest.main()
