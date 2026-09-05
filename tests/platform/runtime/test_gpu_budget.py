"""VRAM budget composition: the stricter of the backend's configured cap and
an explicit per-pipe hint, bounded by what is actually available. A pipe hint
can only lower the budget below the backend's cap, never raise it."""

import unittest

from src.platform.runtime.gpu import GpuMonitor, effective_vram_budget_gb


def _gpu(available_gb: float) -> GpuMonitor:
    # Bypass __init__: nvml is not available in tests and is irrelevant here.
    g = GpuMonitor.__new__(GpuMonitor)
    g._vram_cap_gb = None
    g.get_available_vram = lambda: available_gb
    return g


class TestEffectiveVramBudgetGb(unittest.TestCase):
    """Direct unit tests of the composition helper, independent of GpuMonitor
    plumbing (available-memory bound is passed in pre-computed here)."""

    def test_backend_cap_stricter_than_pipe_hint(self):
        self.assertAlmostEqual(effective_vram_budget_gb(8, 24, 1000.0), 8.0)

    def test_pipe_hint_stricter_than_backend_cap(self):
        self.assertAlmostEqual(effective_vram_budget_gb(24, 8, 1000.0), 8.0)

    def test_absent_pipe_hint_falls_back_to_backend_cap(self):
        self.assertAlmostEqual(effective_vram_budget_gb(24, None, 1000.0), 24.0)

    def test_absent_backend_cap_falls_back_to_pipe_hint(self):
        self.assertAlmostEqual(effective_vram_budget_gb(None, 8, 1000.0), 8.0)

    def test_both_absent_is_bounded_only_by_available(self):
        self.assertAlmostEqual(effective_vram_budget_gb(None, None, 42.0), 42.0)

    def test_scarce_available_memory_wins_over_both_caps(self):
        self.assertAlmostEqual(effective_vram_budget_gb(24, 16, 4.0), 4.0)

    def test_non_positive_backend_cap_is_ignored_not_unlimited(self):
        # A zero/negative cap must never be read as "no cap" (which would let
        # available memory alone decide) OR as a valid, larger budget.
        self.assertAlmostEqual(effective_vram_budget_gb(0, 8, 1000.0), 8.0)
        self.assertAlmostEqual(effective_vram_budget_gb(-5, 8, 1000.0), 8.0)

    def test_non_positive_pipe_hint_is_ignored_not_unlimited(self):
        self.assertAlmostEqual(effective_vram_budget_gb(8, 0, 1000.0), 8.0)
        self.assertAlmostEqual(effective_vram_budget_gb(8, -5, 1000.0), 8.0)

    def test_both_non_positive_falls_back_to_available(self):
        self.assertAlmostEqual(effective_vram_budget_gb(0, -1, 42.0), 42.0)

    def test_order_independent(self):
        self.assertEqual(
            effective_vram_budget_gb(8, 24, 1000.0),
            effective_vram_budget_gb(24, 8, 1000.0),
        )


class TestGpuMonitorVramBudget(unittest.TestCase):
    def test_uncapped_budget_is_bounded_only_by_hardware(self):
        self.assertAlmostEqual(_gpu(100.0).get_vram_budget(), 85.0)

    def test_owner_cap_applies_when_no_explicit_argument(self):
        g = _gpu(100.0)
        g.set_vram_cap_gb(24)
        self.assertAlmostEqual(g.get_vram_budget(), 24.0)

    def test_pipe_hint_below_owner_cap_lowers_the_budget(self):
        g = _gpu(100.0)
        g.set_vram_cap_gb(24)
        self.assertAlmostEqual(g.get_vram_budget(8), 8.0)

    def test_pipe_hint_above_owner_cap_cannot_raise_the_budget(self):
        """The regression this card fixes: a preset's pipe-level `vram_limit_gb`
        used to fully replace the backend's cap instead of composing with it,
        letting a preset plan above the backend's configured maximum."""
        g = _gpu(100.0)
        g.set_vram_cap_gb(8)
        self.assertAlmostEqual(g.get_vram_budget(24), 8.0)

    def test_hardware_wins_when_scarcer_than_both_caps(self):
        g = _gpu(4.0)
        g.set_vram_cap_gb(24)
        self.assertAlmostEqual(g.get_vram_budget(16), 3.4)

    def test_clearing_the_cap_leaves_only_pipe_hint_and_available(self):
        g = _gpu(100.0)
        g.set_vram_cap_gb(24)
        g.set_vram_cap_gb(None)
        self.assertAlmostEqual(g.get_vram_budget(), 85.0)
        self.assertAlmostEqual(g.get_vram_budget(8), 8.0)

    def test_non_positive_owner_cap_is_ignored(self):
        g = _gpu(100.0)
        g.set_vram_cap_gb(0)
        self.assertAlmostEqual(g.get_vram_budget(), 85.0)


if __name__ == "__main__":
    unittest.main()
