"""Tests for BaseBackendConfig's scheduling settings (see docs/backends.md
"Scheduling policy") - every engine gets these, so they're pinned against the
base class rather than any one engine's subclass."""

import unittest

from pydantic import ValidationError

from src.features.backends.backend_config import BASE_CONFIG_FIELDS, NativeBackendConfig


class TestSchedulingSettings(unittest.TestCase):
    def test_defaults_are_fifo_with_an_allowance_of_three(self):
        cfg = NativeBackendConfig(id="b1", name="Backend")

        self.assertEqual(cfg.scheduling_policy, "fifo")
        self.assertEqual(cfg.scheduling_max_consecutive_same_model, 3)

    def test_fair_policy_is_accepted(self):
        cfg = NativeBackendConfig(
            id="b1", name="Backend", scheduling_policy="fair", scheduling_max_consecutive_same_model=5,
        )

        self.assertEqual(cfg.scheduling_policy, "fair")
        self.assertEqual(cfg.scheduling_max_consecutive_same_model, 5)

    def test_unknown_policy_is_rejected(self):
        with self.assertRaises(ValidationError):
            NativeBackendConfig(id="b1", name="Backend", scheduling_policy="round_robin")

    def test_allowance_below_one_is_rejected(self):
        with self.assertRaises(ValidationError):
            NativeBackendConfig(id="b1", name="Backend", scheduling_max_consecutive_same_model=0)

    def test_scheduling_fields_are_base_fields_not_engine_fields(self):
        """They must not show up in engine_fields() (the generic "Connection"
        section the admin form renders per-engine) - they're rendered by a
        dedicated "Scheduling" group instead, present for every engine."""
        names = {spec["name"] for spec in NativeBackendConfig.engine_fields()}

        self.assertNotIn("scheduling_policy", names)
        self.assertNotIn("scheduling_max_consecutive_same_model", names)
        self.assertIn("scheduling_policy", BASE_CONFIG_FIELDS)
        self.assertIn("scheduling_max_consecutive_same_model", BASE_CONFIG_FIELDS)


if __name__ == "__main__":
    unittest.main()
