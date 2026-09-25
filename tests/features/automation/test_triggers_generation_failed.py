import unittest

from src.features.automation.triggers.generation_failed import GenerationFailedTrigger, matches_failure_filters
from src.features.automation.triggers.hook_bridge import HookEventBridge
from src.features.generation.hooks import GENERATION_HOOKS
from src.platform.plugins.hooks import HookChain

PAYLOAD = {
    "generation_id": "gen_1",
    "user_id": "user_1",
    "preset_id": "sdxl/base",
    "error_code": "cuda_oom",
    "category": "cuda_oom",
    "message": "Ran out of GPU memory (VRAM) during generation.",
    "failed_pipe": "generator",
}


class GenerationFailedTriggerTest(unittest.IsolatedAsyncioTestCase):

    async def _fire(self, config, payload=None):
        chain = HookChain()
        enqueued = []
        trigger = GenerationFailedTrigger(
            "auto1", "node1", config,
            enqueue=lambda a, n, p: enqueued.append((a, n, p)),
            bridge=HookEventBridge(chain),
        )
        await trigger.start()
        chain.execute(GENERATION_HOOKS.failed, initial_data=dict(payload or PAYLOAD))
        await trigger.stop()
        chain.execute(GENERATION_HOOKS.failed, initial_data=dict(payload or PAYLOAD))
        return enqueued

    async def test_no_filters_fires_with_the_failure_fields(self):
        enqueued = await self._fire({})

        self.assertEqual(enqueued, [("auto1", "node1", PAYLOAD)])

    async def test_ignores_other_hooks(self):
        chain = HookChain()
        enqueued = []
        trigger = GenerationFailedTrigger("a", "n", {}, lambda *args: enqueued.append(args), HookEventBridge(chain))
        await trigger.start()

        chain.execute(GENERATION_HOOKS.after_complete, initial_data={"status": "completed"})

        self.assertEqual(enqueued, [])

    async def test_category_filter(self):
        self.assertEqual(len(await self._fire({"category": "cuda_oom"})), 1)
        self.assertEqual(await self._fire({"category": "disk_full"}), [])

    async def test_preset_filter(self):
        self.assertEqual(len(await self._fire({"preset_id": "sdxl/base"})), 1)
        self.assertEqual(await self._fire({"preset_id": "flux/dev"}), [])

    async def test_user_filter(self):
        self.assertEqual(len(await self._fire({"user_id": "user_1"})), 1)
        self.assertEqual(await self._fire({"user_id": "user_2"}), [])

    async def test_all_filters_must_match(self):
        config = {"category": "cuda_oom", "preset_id": "sdxl/base", "user_id": "user_2"}
        self.assertEqual(await self._fire(config), [])

    async def test_filter_on_missing_payload_value_does_not_match(self):
        self.assertEqual(await self._fire({"user_id": "user_1"}, {**PAYLOAD, "user_id": None}), [])


class MatchesFailureFiltersTest(unittest.TestCase):

    def test_blank_filters_match(self):
        self.assertTrue(matches_failure_filters({"category": "", "preset_id": "  ", "user_id": None}, PAYLOAD))

    def test_filter_values_are_trimmed(self):
        self.assertTrue(matches_failure_filters({"preset_id": " sdxl/base "}, PAYLOAD))


class RuntimeFactoryTest(unittest.TestCase):

    def test_runtime_builds_the_trigger_for_the_node_type(self):
        from unittest.mock import Mock
        from src.features.automation.runtime import AutomationRuntime

        runtime = AutomationRuntime.__new__(AutomationRuntime)
        runtime.engine = Mock()
        runtime._hook_bridge = HookEventBridge(HookChain())
        trigger = runtime._build_trigger("auto1", {"id": "n1", "type": "trigger.generation_failed", "config": {}})

        self.assertIsInstance(trigger, GenerationFailedTrigger)
