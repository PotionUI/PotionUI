"""Tests for the GPU-threshold resource trigger's hysteresis logic and loop."""

import asyncio
import unittest

from src.features.automation.triggers.resource import (
    ResourceTrigger, direction_met, evaluate_hold, evaluate_hysteresis,
)


class TestEvaluateHysteresis(unittest.TestCase):

    def test_below_direction_fires_once_when_crossing_down(self):
        # 30% -> above threshold (20%), armed, no fire
        fired, armed = evaluate_hysteresis(free_pct=30, threshold_pct=20, direction="below", armed=True)
        self.assertFalse(fired)
        self.assertTrue(armed)

        # 15% -> crosses below threshold, fires once, disarms
        fired, armed = evaluate_hysteresis(free_pct=15, threshold_pct=20, direction="below", armed=True)
        self.assertTrue(fired)
        self.assertFalse(armed)

    def test_below_direction_does_not_refire_while_disarmed_and_still_low(self):
        fired, armed = evaluate_hysteresis(free_pct=10, threshold_pct=20, direction="below", armed=False)
        self.assertFalse(fired)
        self.assertFalse(armed)

    def test_below_direction_rearms_only_past_margin(self):
        # Recovers to 22% - still within the 5pp margin of 20%, stays disarmed.
        fired, armed = evaluate_hysteresis(free_pct=22, threshold_pct=20, direction="below", armed=False, margin_pct=5)
        self.assertFalse(fired)
        self.assertFalse(armed)

        # Recovers to 26% - past threshold + margin (25%), re-arms.
        fired, armed = evaluate_hysteresis(free_pct=26, threshold_pct=20, direction="below", armed=False, margin_pct=5)
        self.assertFalse(fired)
        self.assertTrue(armed)

    def test_below_direction_fires_again_after_rearm_and_redrop(self):
        _, armed = evaluate_hysteresis(free_pct=15, threshold_pct=20, direction="below", armed=True)
        self.assertFalse(armed)
        _, armed = evaluate_hysteresis(free_pct=26, threshold_pct=20, direction="below", armed=armed, margin_pct=5)
        self.assertTrue(armed)
        fired, armed = evaluate_hysteresis(free_pct=15, threshold_pct=20, direction="below", armed=armed)
        self.assertTrue(fired)
        self.assertFalse(armed)

    def test_above_direction_fires_once_when_crossing_up(self):
        fired, armed = evaluate_hysteresis(free_pct=90, threshold_pct=80, direction="above", armed=True)
        self.assertTrue(fired)
        self.assertFalse(armed)

        # Still high - stays disarmed, no refire.
        fired, armed = evaluate_hysteresis(free_pct=85, threshold_pct=80, direction="above", armed=armed)
        self.assertFalse(fired)
        self.assertFalse(armed)

        # Drops well below threshold - margin -> re-arms.
        fired, armed = evaluate_hysteresis(free_pct=70, threshold_pct=80, direction="above", armed=armed, margin_pct=5)
        self.assertFalse(fired)
        self.assertTrue(armed)

    def test_unknown_direction_never_fires(self):
        fired, armed = evaluate_hysteresis(free_pct=1, threshold_pct=50, direction="sideways", armed=True)
        self.assertFalse(fired)
        self.assertTrue(armed)


class TestDirectionMet(unittest.TestCase):

    def test_below_direction(self):
        self.assertTrue(direction_met(10, 20, "below"))
        self.assertFalse(direction_met(30, 20, "below"))

    def test_above_direction(self):
        self.assertTrue(direction_met(90, 80, "above"))
        self.assertFalse(direction_met(70, 80, "above"))

    def test_unknown_direction_never_met(self):
        self.assertFalse(direction_met(1, 50, "sideways"))


class TestEvaluateHold(unittest.TestCase):

    def test_condition_not_met_never_fires_and_clears_held_since(self):
        fired, held_since = evaluate_hold(False, held_since=5.0, now=10.0, hold_s=2.0)
        self.assertFalse(fired)
        self.assertIsNone(held_since)

    def test_first_tick_starts_the_window_without_firing(self):
        fired, held_since = evaluate_hold(True, held_since=None, now=100.0, hold_s=5.0)
        self.assertFalse(fired)
        self.assertEqual(held_since, 100.0)

    def test_fires_once_hold_duration_has_elapsed(self):
        fired, held_since = evaluate_hold(True, held_since=100.0, now=105.0, hold_s=5.0)
        self.assertTrue(fired)
        self.assertEqual(held_since, 100.0)

    def test_does_not_fire_before_hold_duration_elapses(self):
        fired, held_since = evaluate_hold(True, held_since=100.0, now=104.9, hold_s=5.0)
        self.assertFalse(fired)
        self.assertEqual(held_since, 100.0)

    def test_zero_hold_fires_on_the_first_tick(self):
        fired, held_since = evaluate_hold(True, held_since=None, now=1.0, hold_s=0.0)
        self.assertTrue(fired)
        self.assertEqual(held_since, 1.0)


class FakeGpuManager:
    def __init__(self, free_mb: int, total_mb: int = 10000):
        self.free_mb = free_mb
        self.total_mb = total_mb

    def get_free_vram(self) -> int:
        return self.free_mb

    def get_total_vram(self) -> int:
        return self.total_mb


class FakeGenerationStatusTracker:
    def __init__(self, active=False):
        self.active = active

    def list_active(self):
        return [object()] if self.active else []


class TestResourceTriggerLoop(unittest.IsolatedAsyncioTestCase):

    async def test_fires_once_on_crossing_and_stops_cleanly(self):
        gpu = FakeGpuManager(free_mb=1500, total_mb=10000)  # 15% free, below 20% threshold
        fired_events = []

        def enqueue(automation_id, node_id, payload):
            fired_events.append((automation_id, node_id, payload))

        trigger = ResourceTrigger(
            automation_id="auto1", node_id="node1",
            config={"threshold_pct": 20, "direction": "below", "poll_interval_s": 0.05, "margin_pct": 5},
            enqueue=enqueue, gpu_manager=gpu,
        )

        await trigger.start()
        await asyncio.sleep(0.2)
        await trigger.stop()

        self.assertGreaterEqual(len(fired_events), 1)
        automation_id, node_id, payload = fired_events[0]
        self.assertEqual(automation_id, "auto1")
        self.assertEqual(node_id, "node1")
        self.assertAlmostEqual(payload["free_vram_pct"], 15.0)

        # Should not keep firing every poll while still below threshold and disarmed.
        fire_count_after_first_stop_window = len(fired_events)
        await asyncio.sleep(0.1)
        self.assertEqual(len(fired_events), fire_count_after_first_stop_window)

    async def test_stop_cancels_the_poll_loop(self):
        gpu = FakeGpuManager(free_mb=9000, total_mb=10000)
        fired_events = []

        trigger = ResourceTrigger(
            automation_id="auto1", node_id="node1",
            config={"threshold_pct": 20, "direction": "below", "poll_interval_s": 0.05},
            enqueue=lambda *a: fired_events.append(a), gpu_manager=gpu,
        )

        await trigger.start()
        self.assertIsNotNone(trigger._task)
        await trigger.stop()
        self.assertIsNone(trigger._task)


class TestResourceTriggerHoldS(unittest.IsolatedAsyncioTestCase):

    async def test_does_not_fire_until_condition_persists_for_hold_s(self):
        gpu = FakeGpuManager(free_mb=1500, total_mb=10000)  # 15% free, below 20% threshold
        fired_events = []

        trigger = ResourceTrigger(
            automation_id="auto1", node_id="node1",
            config={"threshold_pct": 20, "direction": "below", "poll_interval_s": 0.05,
                    "hold_s": 0.05, "margin_pct": 5},
            enqueue=lambda *a: fired_events.append(a), gpu_manager=gpu,
        )

        await trigger.start()
        await asyncio.sleep(0.15)  # only the immediate first poll has run so far
        self.assertEqual(len(fired_events), 0)

        await asyncio.sleep(0.6)  # the next poll (after the poll-interval floor) clears hold_s
        await trigger.stop()

        self.assertEqual(len(fired_events), 1)

    async def test_zero_hold_s_keeps_the_original_immediate_fire_behavior(self):
        gpu = FakeGpuManager(free_mb=1500, total_mb=10000)
        fired_events = []

        trigger = ResourceTrigger(
            automation_id="auto1", node_id="node1",
            config={"threshold_pct": 20, "direction": "below", "poll_interval_s": 0.05, "hold_s": 0},
            enqueue=lambda *a: fired_events.append(a), gpu_manager=gpu,
        )

        await trigger.start()
        await asyncio.sleep(0.1)
        await trigger.stop()

        self.assertEqual(len(fired_events), 1)


class TestResourceTriggerRequireGenerationIdle(unittest.IsolatedAsyncioTestCase):

    async def test_condition_does_not_fire_while_a_generation_is_active(self):
        gpu = FakeGpuManager(free_mb=1500, total_mb=10000)
        tracker = FakeGenerationStatusTracker(active=True)
        fired_events = []

        trigger = ResourceTrigger(
            automation_id="auto1", node_id="node1",
            config={"threshold_pct": 20, "direction": "below", "poll_interval_s": 0.05,
                    "require_generation_idle": True},
            enqueue=lambda *a: fired_events.append(a), gpu_manager=gpu,
            generation_status_tracker=tracker,
        )

        await trigger.start()
        await asyncio.sleep(0.15)
        self.assertEqual(len(fired_events), 0)

        tracker.active = False
        await asyncio.sleep(0.6)  # next poll after the floor sees the idle GPU
        await trigger.stop()

        self.assertEqual(len(fired_events), 1)

    async def test_fires_normally_when_no_generation_is_active(self):
        gpu = FakeGpuManager(free_mb=1500, total_mb=10000)
        tracker = FakeGenerationStatusTracker(active=False)
        fired_events = []

        trigger = ResourceTrigger(
            automation_id="auto1", node_id="node1",
            config={"threshold_pct": 20, "direction": "below", "poll_interval_s": 0.05,
                    "require_generation_idle": True},
            enqueue=lambda *a: fired_events.append(a), gpu_manager=gpu,
            generation_status_tracker=tracker,
        )

        await trigger.start()
        await asyncio.sleep(0.1)
        await trigger.stop()

        self.assertEqual(len(fired_events), 1)

    async def test_absent_status_tracker_does_not_crash_and_fires_normally(self):
        gpu = FakeGpuManager(free_mb=1500, total_mb=10000)
        fired_events = []

        trigger = ResourceTrigger(
            automation_id="auto1", node_id="node1",
            config={"threshold_pct": 20, "direction": "below", "poll_interval_s": 0.05,
                    "require_generation_idle": True},
            enqueue=lambda *a: fired_events.append(a), gpu_manager=gpu,
            generation_status_tracker=None,
        )

        await trigger.start()
        await asyncio.sleep(0.1)
        await trigger.stop()

        self.assertEqual(len(fired_events), 1)


class TestResourceTriggerLevelDirections(unittest.IsolatedAsyncioTestCase):
    """`is_below`/`is_above` describe a state, so they fire while it holds."""

    async def test_is_above_keeps_firing_while_there_is_room(self):
        gpu = FakeGpuManager(free_mb=1780, total_mb=10000)  # 17.8% free
        fired_events = []

        trigger = ResourceTrigger(
            automation_id="auto1", node_id="node1",
            config={"threshold_pct": 10, "direction": "is_above", "poll_interval_s": 0.05},
            enqueue=lambda *a: fired_events.append(a), gpu_manager=gpu,
        )

        # The poll loop floors its sleep at 0.5s, so the window has to span
        # more than one of those to observe repeat firing.
        await trigger.start()
        await asyncio.sleep(1.3)
        await trigger.stop()

        # The crossing directions would have fired once here and then locked
        # out until free VRAM fell under threshold - margin (5%).
        self.assertGreater(len(fired_events), 1)

    async def test_is_above_stops_firing_once_the_room_is_gone(self):
        gpu = FakeGpuManager(free_mb=1780, total_mb=10000)
        fired_events = []

        trigger = ResourceTrigger(
            automation_id="auto1", node_id="node1",
            config={"threshold_pct": 10, "direction": "is_above", "poll_interval_s": 0.05},
            enqueue=lambda *a: fired_events.append(a), gpu_manager=gpu,
        )

        await trigger.start()
        await asyncio.sleep(0.2)
        self.assertGreater(len(fired_events), 0)

        gpu.free_mb = 500  # 5% free
        await asyncio.sleep(0.6)
        count_when_full = len(fired_events)
        await asyncio.sleep(1.1)
        await trigger.stop()

        self.assertEqual(len(fired_events), count_when_full)

    async def test_is_below_respects_require_generation_idle(self):
        gpu = FakeGpuManager(free_mb=500, total_mb=10000)  # 5% free, below 20%
        fired_events = []

        trigger = ResourceTrigger(
            automation_id="auto1", node_id="node1",
            config={"threshold_pct": 20, "direction": "is_below", "poll_interval_s": 0.05,
                    "require_generation_idle": True},
            enqueue=lambda *a: fired_events.append(a), gpu_manager=gpu,
            generation_status_tracker=FakeGenerationStatusTracker(active=True),
        )

        await trigger.start()
        await asyncio.sleep(0.2)
        await trigger.stop()

        self.assertEqual(fired_events, [])

    async def test_is_above_honours_hold_s(self):
        gpu = FakeGpuManager(free_mb=1780, total_mb=10000)
        fired_events = []

        trigger = ResourceTrigger(
            automation_id="auto1", node_id="node1",
            config={"threshold_pct": 10, "direction": "is_above", "poll_interval_s": 0.05,
                    "hold_s": 10.0},
            enqueue=lambda *a: fired_events.append(a), gpu_manager=gpu,
        )

        await trigger.start()
        await asyncio.sleep(0.2)
        await trigger.stop()

        self.assertEqual(fired_events, [])


if __name__ == '__main__':
    unittest.main()
