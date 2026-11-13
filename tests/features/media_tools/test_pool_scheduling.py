import threading
import unittest

from src.features.media_tools.jobs import MediaToolJobManager, RunOutcome
from src.features.media_tools.pool_limits import (
    PoolLimits,
    SettingsLimits,
    auto_ffmpeg_threads,
    auto_max_concurrent,
    resolve_limits,
    SETTING_DEFAULTS,
)
from src.features.media_tools.pool_metrics import BUCKET_COUNT, BUCKET_SECONDS, PoolMetrics
from src.features.media_tools.preview_slots import PreviewSlots
from src.platform.http.errors import DomainError

WAIT = 10.0


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class Harness:
    def __init__(self, test, **kwargs):
        self.clock = Clock()
        self.limits = kwargs.pop("limits", PoolLimits(max_concurrent=1))
        self.manager = MediaToolJobManager(limits=lambda: self.limits, clock=self.clock, **kwargs)
        test.addCleanup(self.release_all)
        test.addCleanup(self.manager.join_all)
        self.gates = {}
        self.started = []
        self.lock = threading.Lock()

    def submit(self, user, name, kind="run"):
        gate = threading.Event()
        self.gates[name] = gate

        def work(control):
            with self.lock:
                self.started.append(name)
            while not gate.wait(0.005):
                if control.is_cancelled():
                    break
            return RunOutcome()

        run_id = self.manager.submit(user, work, kind=kind, tools=["rot"])
        self.ids = getattr(self, "ids", {})
        self.ids[name] = run_id
        return run_id

    def wait_started(self, count):
        deadline = threading.Event()
        for _ in range(int(WAIT / 0.005)):
            with self.lock:
                if len(self.started) >= count:
                    return list(self.started)
            deadline.wait(0.005)
        raise AssertionError(f"only {self.started} started")

    def finish(self, name, user):
        self.gates[name].set()
        self.manager.wait(user, self.ids[name], WAIT)

    def release_all(self):
        for gate in self.gates.values():
            gate.set()


class TestFairness(unittest.TestCase):

    def test_a_user_flooding_the_queue_does_not_starve_another_user(self):
        pool = Harness(self)
        pool.submit("u1", "a1")
        pool.wait_started(1)
        for index in range(2, 6):
            pool.submit("u1", f"a{index}")
        pool.submit("u2", "b1")

        pool.finish("a1", "u1")

        self.assertEqual(pool.wait_started(2), ["a1", "b1"])

    def test_users_alternate_and_keep_fifo_order_within_a_user(self):
        pool = Harness(self)
        pool.submit("u1", "a1")
        pool.wait_started(1)
        pool.submit("u1", "a2")
        pool.submit("u1", "a3")
        pool.submit("u2", "b1")
        pool.submit("u2", "b2")
        order = ["a1"]
        users = {"a": "u1", "b": "u2"}
        for expected in range(2, 6):
            pool.finish(order[-1], users[order[-1][0]])
            order = pool.wait_started(expected)
        self.assertEqual(order, ["a1", "b1", "a2", "b2", "a3"])

    def test_the_user_with_fewer_running_goes_first(self):
        pool = Harness(self, limits=PoolLimits(max_concurrent=2))
        pool.submit("u1", "a1")
        pool.submit("u1", "a2")
        pool.wait_started(2)
        pool.submit("u1", "a3")
        pool.submit("u2", "b1")
        pool.finish("a1", "u1")
        self.assertEqual(pool.wait_started(3)[2], "b1")

    def test_a_preview_goes_before_queued_batch_runs(self):
        pool = Harness(self)
        pool.submit("u1", "a1")
        pool.wait_started(1)
        pool.submit("u2", "b1")
        pool.submit("u3", "p1", kind="preview")
        pool.finish("a1", "u1")
        self.assertEqual(pool.wait_started(2)[1], "p1")


class TestRunningCap(unittest.TestCase):

    def test_a_user_never_runs_more_than_the_per_user_cap(self):
        pool = Harness(self, limits=PoolLimits(max_concurrent=3, max_running_per_user=1))
        pool.submit("u1", "a1")
        pool.submit("u1", "a2")
        pool.submit("u2", "b1")
        started = pool.wait_started(2)
        self.assertEqual(sorted(started), ["a1", "b1"])
        self.assertEqual(pool.manager.counts()["running"], 2)
        self.assertEqual(pool.manager.counts()["queued"], 1)
        pool.finish("a1", "u1")
        self.assertEqual(pool.wait_started(3)[2], "a2")

    def test_the_cap_does_not_hold_back_the_users_previews(self):
        pool = Harness(self, limits=PoolLimits(max_concurrent=2, max_running_per_user=1))
        pool.submit("u1", "a1")
        pool.submit("u1", "p1", kind="preview")
        self.assertEqual(sorted(pool.wait_started(2)), ["a1", "p1"])


class TestLiveLimits(unittest.TestCase):

    def test_raising_the_limit_lets_queued_runs_start_on_the_next_event(self):
        pool = Harness(self)
        pool.submit("u1", "a1")
        pool.wait_started(1)
        pool.submit("u1", "a2")
        pool.limits = PoolLimits(max_concurrent=3)
        pool.submit("u1", "a3")
        self.assertEqual(sorted(pool.wait_started(3)), ["a1", "a2", "a3"])

    def test_lowering_the_queue_limit_refuses_new_submissions(self):
        pool = Harness(self, limits=PoolLimits(max_concurrent=1, max_queued_per_user=3))
        pool.submit("u1", "a1")
        pool.wait_started(1)
        pool.submit("u1", "a2")
        pool.limits = PoolLimits(max_concurrent=1, max_queued_per_user=1)
        with self.assertRaises(DomainError) as caught:
            pool.submit("u1", "a3")
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(caught.exception.code, "too_many_queued")

    def test_default_static_construction_keeps_the_old_defaults(self):
        manager = MediaToolJobManager()
        limits = manager.limits()
        self.assertEqual((limits.max_concurrent, limits.max_queued_per_user, limits.max_running_per_user), (2, 8, 0))

    def test_settings_limits_re_read_after_the_cache_window(self):
        values = dict(SETTING_DEFAULTS)
        settings = type("S", (), {"get_setting": lambda self, key, default=None: values.get(key, default)})()
        clock = Clock()
        provider = SettingsLimits(settings, cores=8, clock=clock, cache_seconds=2.0)
        self.assertEqual(provider().max_concurrent, 2)
        values["media_tools_max_concurrent"] = 5
        self.assertEqual(provider().max_concurrent, 2)
        clock.advance(3.0)
        self.assertEqual(provider().max_concurrent, 5)

    def test_unreadable_settings_fall_back_to_the_defaults(self):
        values = {"media_tools_max_concurrent": "lots", "media_tools_max_previews": -4}
        limits = resolve_limits(lambda key: values.get(key, SETTING_DEFAULTS[key]), 16)
        self.assertEqual(limits.max_concurrent, 4)
        self.assertEqual(limits.max_previews, 1)

    def test_the_cores_formula(self):
        self.assertEqual([auto_max_concurrent(c) for c in (1, 4, 8, 16, 32, 64, 128)], [2, 2, 2, 4, 8, 8, 8])
        self.assertEqual(auto_ffmpeg_threads(8, 2), 4)
        self.assertEqual(auto_ffmpeg_threads(16, 4), 4)
        self.assertEqual(auto_ffmpeg_threads(64, 8), 8)
        self.assertEqual(auto_ffmpeg_threads(1, 2), 1)

    def test_an_explicit_thread_setting_wins_over_the_share(self):
        values = dict(SETTING_DEFAULTS, media_tools_ffmpeg_threads=3, media_tools_max_concurrent=4)
        limits = resolve_limits(lambda key: values[key], 64)
        self.assertEqual((limits.max_concurrent, limits.ffmpeg_threads), (4, 3))


class TestPreviewSlots(unittest.TestCase):

    def test_the_slot_count_follows_the_live_limit(self):
        limit = {"value": 1}
        slots = PreviewSlots(lambda: limit["value"])
        self.assertTrue(slots.acquire(0.01))
        self.assertFalse(slots.acquire(0.01))
        limit["value"] = 2
        self.assertTrue(slots.acquire(0.01))
        self.assertEqual(slots.in_use, 2)
        slots.release()
        slots.release()
        self.assertEqual(slots.in_use, 0)


class TestMetrics(unittest.TestCase):

    def test_rings_stay_bounded_and_old_minutes_drop_out(self):
        clock = Clock(0.0)
        metrics = PoolMetrics(clock)
        metrics.observe(1, 0, 2)
        for _ in range(BUCKET_COUNT * 3):
            clock.advance(BUCKET_SECONDS)
            metrics.record_wait(1.0)
        self.assertLessEqual(len(metrics._buckets), BUCKET_COUNT)
        summary = metrics.summary()
        self.assertLessEqual(summary["started_jobs"], BUCKET_COUNT)

    def test_peaks_waits_rejections_and_utilisation(self):
        clock = Clock(0.0)
        metrics = PoolMetrics(clock)
        metrics.observe(0, 0, 2)
        clock.advance(10)
        metrics.observe(2, 3, 2)
        metrics.record_wait(4.0)
        metrics.record_wait(8.0)
        metrics.record_rejection("queue_full")
        metrics.record_rejection("preview_busy")
        clock.advance(10)
        metrics.observe(1, 0, 2)
        clock.advance(10)
        summary = metrics.summary()
        self.assertEqual(summary["peak_concurrent"], 2)
        self.assertEqual(summary["peak_queued"], 3)
        self.assertEqual(summary["wait_avg_seconds"], 6.0)
        self.assertEqual(summary["wait_max_seconds"], 8.0)
        self.assertEqual((summary["rejected_queue_full"], summary["rejected_preview_busy"]), (1, 1))
        self.assertEqual(summary["utilisation_5m_percent"], round(100 * (0 + 20 + 10) / 60, 1))

    def test_hour_old_samples_are_forgotten(self):
        clock = Clock(0.0)
        metrics = PoolMetrics(clock)
        metrics.observe(2, 0, 2)
        metrics.record_rejection("queue_full")
        clock.advance(BUCKET_COUNT * BUCKET_SECONDS + 120)
        summary = metrics.summary()
        self.assertEqual(summary["rejected_queue_full"], 0)
        self.assertEqual(summary["peak_concurrent"], 2)

    def test_manager_counts_queue_full_rejections_and_waits(self):
        pool = Harness(self, limits=PoolLimits(max_concurrent=1, max_queued_per_user=1))
        pool.submit("u1", "a1")
        pool.wait_started(1)
        pool.submit("u1", "a2")
        with self.assertRaises(DomainError):
            pool.submit("u1", "a3")
        metrics = pool.manager.metrics.summary()
        self.assertEqual(metrics["rejected_queue_full"], 1)
        self.assertEqual(metrics["peak_queued"], 1)
        self.assertEqual(metrics["started_jobs"], 1)


class TestPoolState(unittest.TestCase):

    def test_lists_running_and_queued_jobs_with_their_details(self):
        pool = Harness(self)
        pool.submit("u1", "a1")
        pool.wait_started(1)
        pool.submit("u2", "b1")
        pool.clock.advance(5)
        state = pool.manager.pool_state()
        self.assertEqual((state["running"], state["queued"]), (1, 1))
        by_user = {job["user_id"]: job for job in state["jobs"]}
        self.assertEqual(by_user["u1"]["status"], "running")
        self.assertEqual(by_user["u2"]["status"], "queued")
        self.assertEqual(by_user["u1"]["tools"], ["rot"])
        self.assertEqual(by_user["u2"]["elapsed_seconds"], 5.0)
        self.assertIsNone(by_user["u2"]["started_at"])
        self.assertIsNotNone(by_user["u1"]["started_at"])
        self.assertEqual(
            set(by_user["u1"]),
            {"run_id", "user_id", "kind", "status", "tools", "label", "progress",
             "queued_at", "started_at", "elapsed_seconds", "waited_seconds"},
        )

    def test_finished_jobs_are_not_listed(self):
        pool = Harness(self, limits=PoolLimits(max_concurrent=2))
        pool.submit("u1", "a1")
        pool.wait_started(1)
        pool.finish("a1", "u1")
        self.assertEqual(pool.manager.pool_state()["jobs"], [])


class TestAdminCancel(unittest.TestCase):

    def test_cancels_a_queued_job_of_any_user(self):
        pool = Harness(self)
        pool.submit("u1", "a1")
        pool.wait_started(1)
        run_id = pool.submit("u2", "b1")
        result = pool.manager.cancel_any(run_id)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(pool.manager.counts()["queued"], 0)
        self.assertEqual(pool.manager.get("u2", run_id).status, "cancelled")

    def test_cancels_a_running_job_cooperatively(self):
        pool = Harness(self)
        run_id = pool.submit("u1", "a1")
        pool.wait_started(1)
        result = pool.manager.cancel_any(run_id)
        self.assertIn(result.status, ("cancelled", "completed"))
        self.assertEqual(pool.manager.counts()["running"], 0)

    def test_unknown_job_is_none(self):
        self.assertIsNone(MediaToolJobManager().cancel_any("nope"))
