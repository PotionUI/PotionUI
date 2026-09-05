"""Tests for the pure per-backend dispatch selector (see scheduling.py).

`select_next`/`project_order` never touch a real queue - each test builds a
pending list and a starting `BackendSchedulingState` by hand, then asserts
the sequence of items the policy would actually dispatch.
"""

import unittest
from typing import List, Optional

from src.features.generation.queue import QueuedGeneration
from src.features.generation.scheduling import (
    FAIR,
    FIFO,
    BackendSchedulingState,
    SchedulingPolicy,
    project_order,
    select_next,
)


def _item(gid: str, user: str, model: Optional[str] = None, backend: str = "native") -> QueuedGeneration:
    return QueuedGeneration(generation_id=gid, backend_id=backend, user_id=user, model_key=model)


def _run(pending: List[QueuedGeneration], policy: SchedulingPolicy, state: BackendSchedulingState) -> List[str]:
    """Dispatch `pending` to completion under `policy`, returning generation ids
    in the order they were picked. Mirrors what GenerationQueue._pump does,
    minus the actual busy-slot bookkeeping."""
    remaining = list(pending)
    order: List[str] = []
    while remaining:
        item, state = select_next(remaining, "native", policy, state)
        if item is None:
            break
        order.append(item.generation_id)
        remaining.remove(item)
    return order


class TestFifoPolicy(unittest.TestCase):
    def test_fifo_ignores_users_and_models_entirely(self):
        pending = [
            _item("a", "u1", model="X"),
            _item("b", "u3", model="X"),
            _item("c", "u2", model="Y"),
        ]
        policy = SchedulingPolicy(name=FIFO)

        order = _run(pending, policy, BackendSchedulingState())

        self.assertEqual(order, ["a", "b", "c"])

    def test_project_order_is_a_no_op_for_fifo(self):
        pending = [_item("a", "u1"), _item("b", "u2")]

        self.assertEqual(
            [i.generation_id for i in project_order(pending, SchedulingPolicy(name=FIFO), BackendSchedulingState())],
            ["a", "b"],
        )


class TestFairPolicyMaintainerExample(unittest.TestCase):
    """The maintainer's own worked example, verbatim: user 1 is running model X
    (already accounted for in the starting state - one X dispatch has
    happened), has 3 more X jobs queued; user 2 queues a Y job; user 3 queues
    an X job. Allowance 2. Expected: user 3's X job cuts in (same model,
    other user - the allowance is now spent: one running plus this one), then
    user 2's Y job is forced to the front (allowance spent, Y is the only job
    waiting for something else), then user 1's three X jobs run as a fresh
    streak - nothing is left waiting for a different model once Y has gone,
    so the allowance never forces another yield even once the counter climbs
    past it again."""

    def test_dispatch_order_matches_the_worked_example(self):
        pending = [
            _item("u1_a", "u1", model="X"),
            _item("u1_b", "u1", model="X"),
            _item("u1_c", "u1", model="X"),
            _item("u2_a", "u2", model="Y"),
            _item("u3_a", "u3", model="X"),
        ]
        policy = SchedulingPolicy(name=FAIR, max_consecutive_same_model=2)
        # u1's running job already consumed one "X" turn and one allowance slot.
        state = BackendSchedulingState(
            canonical_users=("u1",), last_served_user="u1", loaded_model_key="X", consecutive_count=1,
        )

        order = _run(pending, policy, state)

        self.assertEqual(order, ["u3_a", "u2_a", "u1_a", "u1_b", "u1_c"])


class TestFairPolicyGeneral(unittest.TestCase):
    def test_burst_of_one_user_then_a_newcomer_the_newcomer_goes_next(self):
        pending = [_item("a", "u1"), _item("b", "u1"), _item("c", "u2")]
        policy = SchedulingPolicy(name=FAIR)
        state = BackendSchedulingState(canonical_users=("u1",), last_served_user="u1")

        order = _run(pending, policy, state)

        self.assertEqual(order, ["c", "a", "b"])

    def test_two_users_alternate_with_no_model_affinity_in_play(self):
        pending = [
            _item("u1_a", "u1"), _item("u1_b", "u1"),
            _item("u2_a", "u2"), _item("u2_b", "u2"),
        ]
        policy = SchedulingPolicy(name=FAIR)

        order = _run(pending, policy, BackendSchedulingState())

        self.assertEqual(order, ["u1_a", "u2_a", "u1_b", "u2_b"])

    def test_a_new_arrival_cannot_reset_another_users_waiting_turn(self):
        """u2 is already due (u1 was last served). A brand new job for u1 and a
        brand new user u3 arriving before selection must not displace u2."""
        pending = [
            _item("u1_old", "u1"),
            _item("u2_waiting", "u2"),
            _item("u1_new", "u1"),
            _item("u3_new", "u3"),
        ]
        policy = SchedulingPolicy(name=FAIR)
        state = BackendSchedulingState(canonical_users=("u1", "u2"), last_served_user="u1")

        item, _ = select_next(pending, "native", policy, state)

        self.assertEqual(item.generation_id, "u2_waiting")

    def test_allowance_exhausted_yields_to_the_other_model(self):
        pending = [_item("u1_x", "u1", model="X"), _item("u2_y", "u2", model="Y")]
        policy = SchedulingPolicy(name=FAIR, max_consecutive_same_model=2)
        # Already at the cap for X.
        state = BackendSchedulingState(canonical_users=("u1",), last_served_user="u1", loaded_model_key="X", consecutive_count=2)

        item, new_state = select_next(pending, "native", policy, state)

        self.assertEqual(item.generation_id, "u2_y")
        self.assertEqual(new_state.loaded_model_key, "Y")
        self.assertEqual(new_state.consecutive_count, 1)

    def test_allowance_of_one_is_strict_rotation_regardless_of_model(self):
        """With max_consecutive_same_model=1, the model can never justify a
        second consecutive dispatch - every pick falls to plain rotation."""
        pending = [
            _item("u1_x1", "u1", model="X"), _item("u1_x2", "u1", model="X"),
            _item("u2_x", "u2", model="X"),
        ]
        policy = SchedulingPolicy(name=FAIR, max_consecutive_same_model=1)
        state = BackendSchedulingState(canonical_users=("u1",), last_served_user="u1", loaded_model_key="X", consecutive_count=1)

        order = _run(pending, policy, state)

        # u1 just went (consecutive_count already at the cap of 1), so u2 is due
        # next even though u2's job wants the same model; then u1's two remain.
        self.assertEqual(order, ["u2_x", "u1_x1", "u1_x2"])

    def test_allowance_exhausted_yields_to_the_longest_waiting_foreign_job_not_rotations_first(self):
        """The bug this pins: with the allowance exhausted, picking rotation's
        first user regardless of what they want can land on ANOTHER same-model
        job (three continuing X users can keep rotation's front seat occupied
        by X forever), starving a waiting different-model job indefinitely.
        3 X-users (u1, u2, u3), 1 Y-user (u4), allowance 2, fresh state. Both
        of u4's Y jobs must be served - each within the allowance window of
        their arrival - never skipped in favour of yet another X user."""
        pending = [
            _item("u1_a", "u1", model="X"), _item("u2_a", "u2", model="X"),
            _item("u3_a", "u3", model="X"), _item("u4_a", "u4", model="Y"),
            _item("u1_b", "u1", model="X"), _item("u2_b", "u2", model="X"),
            _item("u3_b", "u3", model="X"), _item("u4_b", "u4", model="Y"),
        ]
        policy = SchedulingPolicy(name=FAIR, max_consecutive_same_model=2)

        order = _run(pending, policy, BackendSchedulingState())

        self.assertEqual(
            order,
            ["u1_a", "u2_a", "u4_a", "u4_b", "u3_a", "u1_b", "u2_b", "u3_b"],
        )
        # Neither of u4's jobs waits more than (allowance + 1) dispatches from
        # when it became the longest-waiting foreign job.
        self.assertLessEqual(order.index("u4_a"), 3)
        self.assertLessEqual(order.index("u4_b") - order.index("u4_a"), 1)

    def test_allowance_exhausted_with_nothing_foreign_waiting_keeps_serving_the_loaded_model(self):
        """Once u4's Y jobs are gone, X has nothing to yield to - the allowance
        is "yield when someone else is waiting", not a hard cap, so X keeps
        being served (the counter climbs past `max_consecutive_same_model`
        instead of forcing a pointless same-model "reset")."""
        pending = [_item("u1_x", "u1", model="X"), _item("u2_x", "u2", model="X")]
        policy = SchedulingPolicy(name=FAIR, max_consecutive_same_model=2)
        # Already at the cap, and nobody is waiting for anything but X.
        state = BackendSchedulingState(canonical_users=("u1", "u2"), last_served_user="u2", loaded_model_key="X", consecutive_count=2)

        item, new_state = select_next(pending, "native", policy, state)

        self.assertEqual(item.generation_id, "u1_x")
        self.assertEqual(new_state.loaded_model_key, "X")
        self.assertEqual(new_state.consecutive_count, 3, "still X, so the streak keeps climbing rather than resetting")

    def test_allowance_exhausted_with_two_foreign_models_picks_the_longer_waiting_one_not_rotations_first(self):
        """X is at the cap; Y and Z are both waiting for something else. u2's Y
        job was enqueued before u3's Z job, but rotation order (built from
        first-seen order, not arrival time) puts u3 ahead of u2 - the longer
        WAITING job (Y) must still go first, not whichever foreign job
        happens to be rotation's first."""
        u2_y = _item("u2_y", "u2", model="Y")  # enqueued first - longest waiting
        u3_z = _item("u3_z", "u3", model="Z")  # enqueued second, but first-seen (and so rotation-first)
        u1_x = _item("u1_x", "u1", model="X")
        pending = [u3_z, u2_y, u1_x]
        policy = SchedulingPolicy(name=FAIR, max_consecutive_same_model=2)
        state = BackendSchedulingState(canonical_users=("u1",), last_served_user="u1", loaded_model_key="X", consecutive_count=2)

        order = _run(pending, policy, state)

        self.assertEqual(order[0], "u2_y", "the longer-waiting foreign job (Y), not rotation's first (Z)")

    def test_two_backends_are_independent(self):
        pending = [
            _item("n1", "u1", backend="native"),
            _item("n2", "u2", backend="native"),
            _item("c1", "u1", backend="comfy"),
            _item("c2", "u2", backend="comfy"),
        ]
        policy = SchedulingPolicy(name=FAIR)

        native_only = [i for i in pending if i.backend_id == "native"]
        comfy_only = [i for i in pending if i.backend_id == "comfy"]

        native_order = [i.generation_id for i in project_order(native_only, policy, BackendSchedulingState())]
        comfy_order = [i.generation_id for i in project_order(comfy_only, policy, BackendSchedulingState())]

        self.assertEqual(native_order, ["n1", "n2"])
        self.assertEqual(comfy_order, ["c1", "c2"])


if __name__ == "__main__":
    unittest.main()
