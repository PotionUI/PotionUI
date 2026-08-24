"""Opt-in blocking on trigger.hook_event: the hook dispatch can wait for the
triggered automation run before it proceeds.

Exercises the bridge dispatcher + HookEventTrigger + await_hook_blocking_waits
with a fake, controllable run. No engine, DB, or GPU involved.
"""

import asyncio
import unittest

from src.features.automation.triggers.hook_bridge import HookEventBridge, HookEventTrigger
from src.platform.plugins.hooks import (
    HOOK_BLOCKING_WAITS_KEY,
    HookChain,
    await_hook_blocking_waits,
)


class BlockingHookDispatchTest(unittest.IsolatedAsyncioTestCase):
    def _make(self, config, schedule_run):
        chain = HookChain()
        bridge = HookEventBridge(chain)
        enqueued = []
        trigger = HookEventTrigger(
            "auto1", "node1", config,
            enqueue=lambda a, n, p: enqueued.append((a, n, p)),
            bridge=bridge, schedule_run=schedule_run,
        )
        return chain, bridge, trigger, enqueued

    async def test_wait_off_is_fire_and_forget(self):
        calls = []

        def schedule_run(a, n, p):
            calls.append((a, n, p))
            return None

        chain, _, trigger, enqueued = self._make({"hook_name": "h"}, schedule_run)
        await trigger.start()

        context, _ = chain.execute("h", initial_data={"x": 1})

        self.assertNotIn(HOOK_BLOCKING_WAITS_KEY, context.data)
        self.assertEqual(len(enqueued), 1)   # fired-and-forgotten
        self.assertEqual(calls, [])          # never went through schedule_run
        await await_hook_blocking_waits(context)  # no-op, does not raise

    async def test_wait_on_blocks_until_run_completes(self):
        gate = asyncio.Event()
        started = asyncio.Event()

        async def run():
            started.set()
            await gate.wait()

        def schedule_run(a, n, p):
            return asyncio.create_task(run())

        chain, _, trigger, enqueued = self._make(
            {"hook_name": "h", "wait_for_completion": True, "wait_timeout_s": 5}, schedule_run)
        await trigger.start()

        context, _ = chain.execute("h", initial_data={"x": 1})
        self.assertIn(HOOK_BLOCKING_WAITS_KEY, context.data)  # deferred, not fired
        self.assertEqual(enqueued, [])

        waiter = asyncio.ensure_future(await_hook_blocking_waits(context))
        await started.wait()
        await asyncio.sleep(0.05)
        self.assertFalse(waiter.done())  # still blocked on the run

        gate.set()
        await asyncio.wait_for(waiter, timeout=1)
        self.assertTrue(waiter.done())

    async def test_wait_on_timeout_proceeds_without_cancelling_run(self):
        cancelled = {"v": False}

        async def run():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled["v"] = True
                raise

        holder = {}

        def schedule_run(a, n, p):
            task = asyncio.create_task(run())
            holder["task"] = task
            return task

        chain, _, trigger, _ = self._make(
            {"hook_name": "h", "wait_for_completion": True, "wait_timeout_s": 0.1}, schedule_run)
        await trigger.start()

        context, _ = chain.execute("h", initial_data={})
        await asyncio.wait_for(await_hook_blocking_waits(context), timeout=2)  # returns on timeout

        await asyncio.sleep(0.05)
        self.assertFalse(cancelled["v"])          # shield kept the run alive
        self.assertFalse(holder["task"].done())   # run still in flight

        holder["task"].cancel()
        with self.assertRaises(asyncio.CancelledError):
            await holder["task"]

    async def test_wait_on_failed_run_proceeds(self):
        async def run():
            raise RuntimeError("boom")

        def schedule_run(a, n, p):
            return asyncio.create_task(run())

        chain, _, trigger, _ = self._make(
            {"hook_name": "h", "wait_for_completion": True, "wait_timeout_s": 5}, schedule_run)
        await trigger.start()

        context, _ = chain.execute("h", initial_data={})
        await asyncio.wait_for(await_hook_blocking_waits(context), timeout=1)  # does not raise

    async def test_multiple_waiting_triggers_awaited_concurrently(self):
        gate = asyncio.Event()
        starts = []

        async def run(tag):
            starts.append(tag)
            await gate.wait()

        chain = HookChain()
        bridge = HookEventBridge(chain)
        for i in range(2):
            def schedule_run(a, n, p, _i=i):
                return asyncio.create_task(run(_i))
            trigger = HookEventTrigger(
                f"auto{i}", f"node{i}",
                {"hook_name": "h", "wait_for_completion": True, "wait_timeout_s": 5},
                enqueue=lambda *a: None, bridge=bridge, schedule_run=schedule_run,
            )
            await trigger.start()

        context, _ = chain.execute("h", initial_data={})
        waiter = asyncio.ensure_future(await_hook_blocking_waits(context))
        await asyncio.sleep(0.05)
        self.assertEqual(sorted(starts), [0, 1])  # both runs started, awaited together
        self.assertFalse(waiter.done())

        gate.set()
        await asyncio.wait_for(waiter, timeout=1)


if __name__ == "__main__":
    unittest.main()
