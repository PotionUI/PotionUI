"""VRAM budget precedence: explicit argument > owner-set cap > hardware only."""

import unittest

from src.platform.runtime.gpu import GpuManager


def _gpu(available_gb: float) -> GpuManager:
    # Bypass __init__: nvml is not available in tests and is irrelevant here.
    g = GpuManager.__new__(GpuManager)
    g._vram_cap_gb = None
    g.get_available_vram = lambda: available_gb
    return g


class TestVramBudget(unittest.TestCase):
    def test_uncapped_budget_is_bounded_only_by_hardware(self):
        self.assertAlmostEqual(_gpu(100.0).get_vram_budget(), 85.0)

    def test_owner_cap_applies_when_no_explicit_argument(self):
        g = _gpu(100.0)
        g.set_vram_cap_gb(24)
        self.assertAlmostEqual(g.get_vram_budget(), 24.0)

    def test_explicit_argument_overrides_the_owner_cap(self):
        g = _gpu(100.0)
        g.set_vram_cap_gb(24)
        self.assertAlmostEqual(g.get_vram_budget(8), 8.0)

    def test_hardware_wins_when_scarcer_than_the_cap(self):
        g = _gpu(4.0)
        g.set_vram_cap_gb(24)
        self.assertAlmostEqual(g.get_vram_budget(), 3.4)

    def test_clearing_the_cap_restores_the_hardware_bound(self):
        g = _gpu(100.0)
        g.set_vram_cap_gb(24)
        g.set_vram_cap_gb(None)
        self.assertAlmostEqual(g.get_vram_budget(), 85.0)


if __name__ == "__main__":
    unittest.main()
