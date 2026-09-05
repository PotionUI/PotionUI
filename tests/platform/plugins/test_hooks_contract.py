"""The hook handler contract: synchronous handlers, and how their result propagates."""

import unittest
import warnings

from src.platform.plugins.hooks import (
    HOOK_BLOCKING_WAITS_KEY,
    HookChain,
    HookContext,
    HookContractError,
)


class TestSynchronousHandlerContract(unittest.TestCase):
    """Handlers are called synchronously, so async ones must never reach dispatch."""

    def setUp(self):
        self.chain = HookChain()

    def test_documented_example_runs_and_its_effect_is_visible(self):
        """The `def` handler shown in docs/plugin-api.md, executed end to end."""
        def on_generation_complete(context: HookContext) -> HookContext:
            context.data["my-plugin.notified"] = True
            context.metadata["my-plugin.seen"] = context.data["generation_id"]
            return context

        self.chain.register("generation.after_complete", "my-plugin", on_generation_complete)

        context, results = self.chain.execute(
            "generation.after_complete",
            initial_data={"generation_id": "gen-1", "status": "completed"},
        )

        self.assertTrue(results[0].success, results[0].error)
        self.assertTrue(context.data["my-plugin.notified"])
        self.assertEqual(context.metadata["my-plugin.seen"], "gen-1")

    def test_async_handler_is_rejected_at_registration(self):
        async def on_before_start(context: HookContext) -> HookContext:
            return context

        with self.assertRaises(HookContractError) as caught:
            self.chain.register("generation.before_start", "async-plugin", on_before_start)

        message = str(caught.exception)
        self.assertIn("async-plugin", message)
        self.assertIn("generation.before_start", message)
        self.assertIn("on_before_start", message)
        self.assertIn(HOOK_BLOCKING_WAITS_KEY, message)
        self.assertEqual(self.chain._handlers.get("generation.before_start"), None)

    def test_async_generator_handler_is_rejected_at_registration(self):
        async def streaming_handler(context: HookContext):
            yield context

        with self.assertRaises(HookContractError):
            self.chain.register("test.hook", "async-gen-plugin", streaming_handler)

    def test_async_callable_object_is_rejected_at_registration(self):
        class AsyncHandler:
            async def __call__(self, context: HookContext) -> HookContext:
                return context

        with self.assertRaises(HookContractError):
            self.chain.register("test.hook", "callable-plugin", AsyncHandler())

    def test_sync_handler_returning_a_coroutine_is_an_isolated_failure(self):
        """A sync callable can still hand back a coroutine; that is this plugin's failure only."""
        async def deferred(context):
            return context

        def sneaky(context):
            return deferred(context)

        def follower(context):
            context.data["follower"] = "ran"
            return context

        self.chain.register("test.hook", "sneaky-plugin", sneaky)
        self.chain.register("test.hook", "follower-plugin", follower)

        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            context, results = self.chain.execute("test.hook")

        self.assertFalse(results[0].success)
        self.assertIn("coroutine", results[0].error)
        self.assertIn(HOOK_BLOCKING_WAITS_KEY, results[0].error)
        self.assertTrue(results[1].success)
        self.assertEqual(context.data["follower"], "ran")

    def test_invalid_return_value_reports_the_contract(self):
        def returns_a_dict(context):
            return {"not": "a context"}

        def returns_none(context):
            return None

        self.chain.register("test.hook", "dict-plugin", returns_a_dict)
        self.chain.register("test.hook", "none-plugin", returns_none)

        _, results = self.chain.execute("test.hook")

        self.assertFalse(results[0].success)
        self.assertIn("expected the HookContext", results[0].error)
        self.assertIn("dict", results[0].error)
        self.assertFalse(results[1].success)
        self.assertIn("expected the HookContext", results[1].error)

    def test_chained_sync_handlers_are_unchanged(self):
        def first(context):
            context.data["order"] = ["first"]
            return context

        def second(context):
            context.data["order"].append("second")
            return context

        self.chain.register("test.hook", "plugin-1", first)
        self.chain.register("test.hook", "plugin-2", second)

        context, results = self.chain.execute("test.hook", initial_data={"seed": 7})

        self.assertEqual(context.data["order"], ["first", "second"])
        self.assertEqual(context.data["seed"], 7)
        self.assertTrue(all(result.success for result in results))


class TestResultPropagation(unittest.TestCase):
    """What a successful handler returns reaches the next handler and the caller."""

    def setUp(self):
        self.chain = HookChain()

    def test_metadata_only_change_propagates(self):
        """A handler that annotates metadata without touching data keeps the annotation."""
        seen_by_second = {}

        def annotator(context):
            context.metadata["annotator"] = "was here"
            return context

        def observer(context):
            seen_by_second.update(context.metadata)
            return context

        self.chain.register("test.hook", "plugin-1", annotator)
        self.chain.register("test.hook", "plugin-2", observer)

        context, results = self.chain.execute("test.hook", initial_data={"key": "value"})

        self.assertEqual(seen_by_second.get("annotator"), "was here")
        self.assertEqual(context.metadata["annotator"], "was here")
        self.assertTrue(all(result.success for result in results))

    def test_replaced_payload_propagates(self):
        def replace(context):
            context.data["payload"] = "replaced"
            return context

        def observe(context):
            context.data["observed"] = context.data["payload"]
            return context

        self.chain.register("test.hook", "plugin-1", replace)
        self.chain.register("test.hook", "plugin-2", observe)

        context, _ = self.chain.execute("test.hook", initial_data={"payload": "original"})

        self.assertEqual(context.data["payload"], "replaced")
        self.assertEqual(context.data["observed"], "replaced")

    def test_in_place_nested_edit_propagates(self):
        def edit(context):
            context.data["form_data"]["steps"] = 30
            return context

        self.chain.register("test.hook", "plugin-1", edit)

        context, results = self.chain.execute(
            "test.hook", initial_data={"form_data": {"steps": 20}}
        )

        self.assertEqual(context.data["form_data"]["steps"], 30)
        self.assertTrue(results[0].success)

    def test_replacing_a_payload_whose_equality_raises_does_not_fail_the_chain(self):
        """Payloads are arbitrary objects; the chain must never compare them with `==`."""
        class Unequatable:
            def __eq__(self, other):
                raise TypeError("this object refuses to be compared")

            __hash__ = None

        def replace_latents(context):
            context.data["latents"] = Unequatable()
            context.metadata["annotated"] = True
            return context

        self.chain.register("test.hook", "tensor-plugin", replace_latents)

        context, results = self.chain.execute(
            "test.hook", initial_data={"latents": Unequatable()}
        )

        self.assertTrue(results[0].success, results[0].error)
        self.assertTrue(context.metadata["annotated"])
        self.assertIsInstance(context.data["latents"], Unequatable)

    def test_replacing_an_array_like_payload_does_not_fail_the_chain(self):
        """A numpy/torch payload's `==` yields an array whose truth value is ambiguous."""
        class AmbiguousTruth:
            def __bool__(self):
                raise ValueError(
                    "the truth value of an array with more than one element is ambiguous"
                )

        class ArrayLike:
            def __eq__(self, other):
                return AmbiguousTruth()

            __hash__ = None

        def replace_image(context):
            context.data["image"] = ArrayLike()
            context.metadata["annotated"] = True
            return context

        self.chain.register("test.hook", "array-plugin", replace_image)

        context, results = self.chain.execute(
            "test.hook", initial_data={"image": ArrayLike()}
        )

        self.assertTrue(results[0].success, results[0].error)
        self.assertTrue(context.metadata["annotated"])
        self.assertIsInstance(context.data["image"], ArrayLike)

    def test_handler_exception_is_still_isolated(self):
        def boom(context):
            raise ValueError("handler blew up")

        def survivor(context):
            context.data["survived"] = True
            return context

        self.chain.register("test.hook", "boom-plugin", boom)
        self.chain.register("test.hook", "survivor-plugin", survivor)

        context, results = self.chain.execute("test.hook")

        self.assertFalse(results[0].success)
        self.assertIn("handler blew up", results[0].error)
        self.assertTrue(results[1].success)
        self.assertTrue(context.data["survived"])

    def test_modified_flag_reports_a_key_the_handler_added(self):
        def adds_a_key(context):
            context.data["added"] = 1
            return context

        def reads_only(context):
            context.data.get("added")
            return context

        self.chain.register("test.hook", "writer", adds_a_key)
        self.chain.register("test.hook", "reader", reads_only)

        _, results = self.chain.execute("test.hook")

        self.assertTrue(results[0].modified)
        self.assertFalse(results[1].modified)


if __name__ == "__main__":
    unittest.main()
