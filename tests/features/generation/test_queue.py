"""Tests for the generation queue: global FIFO, one slot per backend."""

import asyncio
import unittest

from src.features.generation.queue import GenerationQueue, QueuedGeneration
from src.features.generation.scheduling import SchedulingPolicy


def _item(gid, backend="native", tab=None, user="u1", model=None) -> QueuedGeneration:
    return QueuedGeneration(generation_id=gid, backend_id=backend, tab_id=tab, user_id=user, model_key=model)


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


class TestFairSchedulingThroughTheRealQueue(unittest.IsolatedAsyncioTestCase):
    """`scheduling.py`'s own tests pin the selector in isolation; these pin it
    wired into the real queue - `_pump`'s per-backend selection, the busy-slot
    dance, and `position()`/`pending_items()`/`snapshot()` reporting the
    projected order rather than raw arrival order."""

    async def asyncSetUp(self):
        self.dispatched = []
        self.queue = GenerationQueue(
            dispatch=self._dispatch,
            policy_for=lambda backend_id: SchedulingPolicy(name="fair", max_consecutive_same_model=2),
        )

    async def _dispatch(self, item: QueuedGeneration) -> None:
        self.dispatched.append(item.generation_id)

    async def test_the_maintainers_worked_example(self):
        """user 1 running X, 3 more X jobs queued; user 2 queues a Y job; user 3
        queues an X job; allowance 2. Expected order after the running job:
        user 3's X (same model, other user - the allowance is now spent: one
        running plus this one), then user 2's Y (forced front - the allowance
        is spent and Y is the only job waiting for something else), then user
        1's three X jobs as a fresh streak (nothing is left waiting for a
        different model once Y has gone)."""
        await self.queue.enqueue(_item("running", user="u1", model="X"))
        self.assertEqual(self.dispatched, ["running"], "the running job dispatches immediately, backend was idle")

        for gid, user, model in [
            ("u1_a", "u1", "X"), ("u1_b", "u1", "X"), ("u1_c", "u1", "X"),
            ("u2_a", "u2", "Y"), ("u3_a", "u3", "X"),
        ]:
            await self.queue.enqueue(_item(gid, user=user, model=model))

        self.assertEqual(
            [i.generation_id for i in self.queue.pending_items()],
            ["u3_a", "u2_a", "u1_a", "u1_b", "u1_c"],
            "pending()/position() must reflect the projected dispatch order, not arrival order",
        )
        self.assertEqual(self.queue.position("u3_a"), 0)
        self.assertEqual(self.queue.position("u2_a"), 1)

        expected = ["running", "u3_a", "u2_a", "u1_a", "u1_b", "u1_c"]
        last = "running"
        for next_id in expected[1:]:
            await self.queue.release("native", last)
            self.assertEqual(self.dispatched[-1], next_id)
            last = next_id

    async def test_fifo_is_unaffected_when_no_model_key_is_involved(self):
        """Same shape of input, but a plain FIFO policy: order is arrival order."""
        fifo_queue = GenerationQueue(dispatch=self._dispatch, policy_for=lambda backend_id: SchedulingPolicy())
        for gid, user in [("a", "u1"), ("b", "u1"), ("c", "u2")]:
            await fifo_queue.enqueue(_item(gid, user=user))

        self.assertEqual([i.generation_id for i in fifo_queue.pending_items()], ["b", "c"])

    async def test_cancelling_the_next_turn_job_passes_the_turn_correctly(self):
        await self.queue.enqueue(_item("running", user="u1"))
        await self.queue.enqueue(_item("u1_next", user="u1"))
        await self.queue.enqueue(_item("u2_due", user="u2"))
        await self.queue.enqueue(_item("u3_after", user="u3"))

        self.assertTrue(await self.queue.cancel("u2_due"))
        await self.queue.release("native", "running")

        self.assertEqual(self.dispatched[-1], "u3_after", "u2 was skipped, not u1 re-served")

    async def test_dispatch_failure_does_not_burn_the_other_users_turn(self):
        async def flaky(item: QueuedGeneration) -> None:
            if item.generation_id == "u2_fails":
                raise RuntimeError("boom")
            self.dispatched.append(item.generation_id)

        q = GenerationQueue(
            dispatch=flaky,
            policy_for=lambda backend_id: SchedulingPolicy(name="fair", max_consecutive_same_model=2),
        )
        await q.enqueue(_item("running", user="u1"))
        await q.enqueue(_item("u2_fails", user="u2"))
        await q.enqueue(_item("u3_next", user="u3"))

        # Releasing "running" makes u2's job runnable; it fails immediately, so
        # the same pump keeps going and hands the freed slot straight to u3 -
        # the failure must free the slot without also stranding u3 behind it.
        await q.release("native", "running")

        self.assertEqual(self.dispatched, ["running", "u3_next"])
        self.assertEqual(q.running_generation_id("native"), "u3_next")

    async def test_a_failed_same_model_dispatch_leaves_scheduling_state_unchanged(self):
        """A same-model job whose dispatch fails must not get to consume the
        fairness turn it was chosen for: the very next successful selection
        must behave exactly as if the failed job had never been picked.

        u1 is running X (allowance 2, so one more X may follow before a
        different model is forced to the front). u2's X job will fail; u3's X
        job and u4's Y job are also waiting. If the failed dispatch's state
        were committed anyway, u2's phantom turn would leave the allowance
        already spent, forcing u4's Y in ahead of u3's still-untouched X job.
        It must not: u3 (the same-model job that would have gone right after
        "running" had u2 never existed) goes next, and only then does the
        allowance actually run out and force u4's Y forward."""
        async def flaky(item: QueuedGeneration) -> None:
            if item.generation_id == "u2_fails":
                raise RuntimeError("boom")
            self.dispatched.append(item.generation_id)

        q = GenerationQueue(
            dispatch=flaky,
            policy_for=lambda backend_id: SchedulingPolicy(name="fair", max_consecutive_same_model=2),
        )
        await q.enqueue(_item("running", user="u1", model="X"))
        await q.enqueue(_item("u2_fails", user="u2", model="X"))
        await q.enqueue(_item("u3_ok", user="u3", model="X"))
        await q.enqueue(_item("u4_y", user="u4", model="Y"))

        await q.release("native", "running")

        self.assertEqual(self.dispatched, ["running", "u3_ok"])
        self.assertEqual(q.running_generation_id("native"), "u3_ok")

        await q.release("native", "u3_ok")
        self.assertEqual(self.dispatched, ["running", "u3_ok", "u4_y"])


class TestProjectedPendingReportingAcrossBackends(unittest.IsolatedAsyncioTestCase):
    """`pending_items()`/`position()`/`snapshot()` report a GLOBAL order. Two
    independent backends must never appear to block each other in that
    report, whatever each one's own scheduling policy does with its own
    items."""

    async def asyncSetUp(self):
        self.dispatched = []

    async def _dispatch(self, item: QueuedGeneration) -> None:
        pass  # never actually runs in these tests - both backends stay busy throughout

    async def test_two_busy_fifo_backends_report_exact_global_arrival_order(self):
        q = GenerationQueue(dispatch=self._dispatch)
        await q.enqueue(_item("a_running", backend="A", user="u1"))
        await q.enqueue(_item("b_running", backend="B", user="u1"))

        # Interleaved arrivals across the two busy backends.
        await q.enqueue(_item("a1", backend="A", user="u1"))
        await q.enqueue(_item("b1", backend="B", user="u1"))
        await q.enqueue(_item("a2", backend="A", user="u1"))

        self.assertEqual(
            [i.generation_id for i in q.pending_items()],
            ["a1", "b1", "a2"],
            "grouping by backend before projecting must never reorder an all-FIFO queue",
        )

    async def test_a_fair_backend_reorders_only_its_own_items_among_a_fifo_backend(self):
        q = GenerationQueue(
            dispatch=self._dispatch,
            policy_for=lambda backend_id: (
                SchedulingPolicy(name="fair", max_consecutive_same_model=1) if backend_id == "B" else SchedulingPolicy()
            ),
        )
        await q.enqueue(_item("a_running", backend="A", user="u1"))
        await q.enqueue(_item("b_running", backend="B", user="u1"))

        # A (FIFO) gets two more arrivals from the same user; B (fair) gets a
        # second user's job enqueued between them - fair rotation means B's
        # own two items swap places relative to each other, but A's two items
        # must keep their exact original global slots either side of B's.
        await q.enqueue(_item("a1", backend="A", user="u1"))
        await q.enqueue(_item("b_u1", backend="B", user="u1"))
        await q.enqueue(_item("a2", backend="A", user="u1"))
        await q.enqueue(_item("b_u2", backend="B", user="u2"))

        self.assertEqual(
            [i.generation_id for i in q.pending_items()],
            ["a1", "b_u2", "a2", "b_u1"],
            "A's items stay in their own arrival slots; only B's own items reorder among B's slots",
        )


if __name__ == "__main__":
    unittest.main()
