"""Tests for the generation queue: global FIFO, one slot per backend."""

import asyncio
import unittest

from src.features.generation.queue import GenerationQueue, QueuedGeneration


def _item(gid, backend="native", tab=None, user="u1") -> QueuedGeneration:
    return QueuedGeneration(generation_id=gid, backend_id=backend, tab_id=tab, user_id=user)


class TestGenerationQueue(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dispatched = []
        self.queue = GenerationQueue(dispatch=self._dispatch)

    async def _dispatch(self, item: QueuedGeneration) -> None:
        self.dispatched.append(item.generation_id)

    async def test_first_item_dispatches_immediately(self):
        await self.queue.enqueue(_item("a"))
        self.assertEqual(self.dispatched, ["a"])

    async def test_second_item_on_the_same_backend_waits(self):
        await self.queue.enqueue(_item("a"))
        await self.queue.enqueue(_item("b"))

        self.assertEqual(self.dispatched, ["a"], "b must wait for a's backend slot")
        self.assertEqual(self.queue.position("b"), 0)

    async def test_releasing_a_slot_dispatches_the_next_item(self):
        await self.queue.enqueue(_item("a"))
        await self.queue.enqueue(_item("b"))

        await self.queue.release("native", "a")

        self.assertEqual(self.dispatched, ["a", "b"])
        self.assertIsNone(self.queue.position("b"))

    async def test_a_busy_backend_does_not_block_an_idle_one(self):
        """Global FIFO, but head-of-line blocking must not cross backends."""
        await self.queue.enqueue(_item("a", backend="native"))
        await self.queue.enqueue(_item("b", backend="native"))
        await self.queue.enqueue(_item("c", backend="comfy"))

        self.assertEqual(self.dispatched, ["a", "c"])
        self.assertEqual(self.queue.position("b"), 0)

    async def test_same_backend_items_run_in_fifo_order(self):
        for gid in ("a", "b", "c"):
            await self.queue.enqueue(_item(gid))

        await self.queue.release("native", "a")
        await self.queue.release("native", "b")

        self.assertEqual(self.dispatched, ["a", "b", "c"])

    async def test_a_stale_release_cannot_free_someone_elses_slot(self):
        await self.queue.enqueue(_item("a"))
        await self.queue.enqueue(_item("b"))
        await self.queue.release("native", "a")  # b now holds the slot
        await self.queue.enqueue(_item("c"))

        # A late completion from the long-finished "a" must not evict "b".
        await self.queue.release("native", "a")

        self.assertEqual(self.dispatched, ["a", "b"])
        self.assertEqual(self.queue.running_generation_id("native"), "b")

    async def test_cancelling_a_pending_item_removes_it_without_dispatch(self):
        await self.queue.enqueue(_item("a"))
        await self.queue.enqueue(_item("b"))

        self.assertTrue(await self.queue.cancel("b"))

        await self.queue.release("native", "a")
        self.assertEqual(self.dispatched, ["a"], "cancelled item must never dispatch")

    async def test_cancelling_a_running_item_returns_false(self):
        await self.queue.enqueue(_item("a"))
        self.assertFalse(await self.queue.cancel("a"))

    async def test_clear_tab_drops_only_that_tabs_pending_items(self):
        await self.queue.enqueue(_item("running", tab="t1"))
        await self.queue.enqueue(_item("a", tab="t1"))
        await self.queue.enqueue(_item("b", tab="t2"))
        await self.queue.enqueue(_item("c", tab="t1"))

        cleared = await self.queue.clear_tab("u1", "t1")

        self.assertEqual(sorted(cleared), ["a", "c"])
        self.assertEqual([i.generation_id for i in self.queue.pending_for_tab("u1", "t2")], ["b"])

    async def test_clear_tab_is_scoped_by_user(self):
        """Tab ids are minted client-side, so they are only unique within a user."""
        await self.queue.enqueue(_item("running", tab="t1", user="u1"))
        await self.queue.enqueue(_item("mine", tab="t1", user="u1"))
        await self.queue.enqueue(_item("theirs", tab="t1", user="u2"))

        cleared = await self.queue.clear_tab("u1", "t1")

        self.assertEqual(cleared, ["mine"])
        self.assertEqual(self.queue.position("theirs"), 0)

    async def test_clear_tab_leaves_a_running_generation_alone(self):
        await self.queue.enqueue(_item("running", tab="t1"))
        cleared = await self.queue.clear_tab("u1", "t1")
        self.assertEqual(cleared, [])
        self.assertEqual(self.queue.running_generation_id("native"), "running")

    async def test_a_failing_inline_dispatch_frees_the_slot_and_raises(self):
        """
        The caller enqueued this item, so a failure to start it must reach them
        (the API answers with an error, not a dead generation id).
        """
        boom = GenerationQueue(dispatch=self._explode)

        with self.assertRaises(RuntimeError):
            await boom.enqueue(_item("a"))

        self.assertIsNone(boom.running_generation_id("native"))

    async def _explode(self, item):
        raise RuntimeError("dispatch blew up")

    async def test_a_failing_dispatch_does_not_strand_later_items(self):
        seen = []

        async def flaky(item):
            if item.generation_id == "a":
                raise RuntimeError("nope")
            seen.append(item.generation_id)

        q = GenerationQueue(dispatch=flaky)
        with self.assertRaises(RuntimeError):
            await q.enqueue(_item("a"))
        await q.enqueue(_item("b"))

        self.assertEqual(seen, ["b"])

    async def test_a_later_items_dispatch_failure_does_not_hit_the_enqueuer(self):
        """
        Releasing a slot can dispatch someone else's queued item. If that item
        fails, the exception belongs in the log, not in the releasing caller.
        """
        async def flaky(item):
            if item.generation_id == "bad":
                raise RuntimeError("someone else's problem")

        q = GenerationQueue(dispatch=flaky)
        await q.enqueue(_item("good"))
        await q.enqueue(_item("bad"))

        # Must not raise, even though dispatching "bad" blows up.
        await q.release("native", "good")

        self.assertIsNone(q.running_generation_id("native"))

    async def test_a_failed_item_leaves_its_backend_usable(self):
        async def flaky(item):
            if item.generation_id == "bad":
                raise RuntimeError("bad pipeline")

        q = GenerationQueue(dispatch=flaky)
        with self.assertRaises(RuntimeError):
            await q.enqueue(_item("bad"))

        # The slot it briefly held must be reusable by the next generation.
        await q.enqueue(_item("good"))
        self.assertEqual(q.running_generation_id("native"), "good")

    async def test_snapshot_reports_pending_and_running(self):
        await self.queue.enqueue(_item("a"))
        await self.queue.enqueue(_item("b", tab="t9"))

        snap = self.queue.snapshot()

        self.assertEqual(snap["running"], {"native": "a"})
        self.assertEqual([p["generation_id"] for p in snap["pending"]], ["b"])
        self.assertEqual(snap["pending"][0]["tab_id"], "t9")


if __name__ == "__main__":
    unittest.main()
