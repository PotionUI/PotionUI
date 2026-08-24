"""POTIONUI_VRAM_CAP_GB — the rig-simulation VRAM cap knob (src.platform.runtime.vram_cap)."""

import pytest

from src.platform.runtime import vram_cap


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    # Every test gets a clean module-level cache and a clean env var, since
    # the cache is deliberately process-lifetime (read the env var once).
    monkeypatch.delenv(vram_cap.VRAM_CAP_ENV_VAR, raising=False)
    vram_cap.reset_for_tests()
    yield
    vram_cap.reset_for_tests()


class TestGetVramCapGb:
    def test_unset_env_var_returns_none(self):
        assert vram_cap.get_vram_cap_gb() is None

    def test_valid_value_is_parsed(self, monkeypatch):
        monkeypatch.setenv(vram_cap.VRAM_CAP_ENV_VAR, "16")
        assert vram_cap.get_vram_cap_gb() == 16.0

    def test_fractional_value_is_parsed(self, monkeypatch):
        monkeypatch.setenv(vram_cap.VRAM_CAP_ENV_VAR, "8.5")
        assert vram_cap.get_vram_cap_gb() == 8.5

    def test_non_numeric_value_is_ignored(self, monkeypatch, caplog):
        monkeypatch.setenv(vram_cap.VRAM_CAP_ENV_VAR, "not-a-number")
        with caplog.at_level("WARNING", logger="is"):
            assert vram_cap.get_vram_cap_gb() is None
        assert "not a number" in caplog.text

    def test_zero_value_is_ignored(self, monkeypatch):
        monkeypatch.setenv(vram_cap.VRAM_CAP_ENV_VAR, "0")
        assert vram_cap.get_vram_cap_gb() is None

    def test_negative_value_is_ignored(self, monkeypatch):
        monkeypatch.setenv(vram_cap.VRAM_CAP_ENV_VAR, "-4")
        assert vram_cap.get_vram_cap_gb() is None

    def test_logs_loudly_exactly_once(self, monkeypatch, caplog):
        monkeypatch.setenv(vram_cap.VRAM_CAP_ENV_VAR, "16")
        with caplog.at_level("WARNING", logger="is"):
            vram_cap.get_vram_cap_gb()
            vram_cap.get_vram_cap_gb()
            vram_cap.get_vram_cap_gb()
        cap_warnings = [r for r in caplog.records if "VRAM capped" in r.message]
        assert len(cap_warnings) == 1

    def test_value_is_cached_after_first_read(self, monkeypatch):
        monkeypatch.setenv(vram_cap.VRAM_CAP_ENV_VAR, "16")
        assert vram_cap.get_vram_cap_gb() == 16.0
        monkeypatch.setenv(vram_cap.VRAM_CAP_ENV_VAR, "8")
        # Still 16 — read once per process, not re-read on every call.
        assert vram_cap.get_vram_cap_gb() == 16.0


class TestApplyVramCapBytes:
    _GB = 1024 ** 3

    def test_unset_is_identity_passthrough(self):
        free, total = vram_cap.apply_vram_cap_bytes(10 * self._GB, 32 * self._GB)
        assert (free, total) == (10 * self._GB, 32 * self._GB)

    def test_caps_total_and_free_preserving_used(self, monkeypatch):
        monkeypatch.setenv(vram_cap.VRAM_CAP_ENV_VAR, "16")
        # 32GB card, 10GB free -> 22GB used.
        free, total = vram_cap.apply_vram_cap_bytes(10 * self._GB, 32 * self._GB)
        assert total == 16 * self._GB
        # used (22GB) exceeds the 16GB cap entirely -> free clamps to 0, not negative.
        assert free == 0

    def test_caps_free_when_used_fits_under_cap(self, monkeypatch):
        monkeypatch.setenv(vram_cap.VRAM_CAP_ENV_VAR, "16")
        # 32GB card, 28GB free -> 4GB used, fits comfortably under a 16GB cap.
        free, total = vram_cap.apply_vram_cap_bytes(28 * self._GB, 32 * self._GB)
        assert total == 16 * self._GB
        assert free == 12 * self._GB  # 16GB cap - 4GB used

    def test_cap_larger_than_real_card_has_no_effect(self, monkeypatch):
        monkeypatch.setenv(vram_cap.VRAM_CAP_ENV_VAR, "64")
        free, total = vram_cap.apply_vram_cap_bytes(10 * self._GB, 32 * self._GB)
        assert (free, total) == (10 * self._GB, 32 * self._GB)


class TestApplyVramCapGb:
    def test_unset_is_identity_passthrough(self):
        assert vram_cap.apply_vram_cap_gb(10.0, 32.0) == (10.0, 32.0)

    def test_caps_total_and_free(self, monkeypatch):
        monkeypatch.setenv(vram_cap.VRAM_CAP_ENV_VAR, "16")
        free, total = vram_cap.apply_vram_cap_gb(28.0, 32.0)
        assert total == 16.0
        assert free == 12.0
