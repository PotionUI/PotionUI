"""The retention settings: what the API accepts, and what the worker reads."""

from unittest.mock import Mock

from src.features.housekeeping.settings import (
    DEFAULTS,
    MAX_DAYS,
    SETTING_LLM_TRACE_DAYS,
    SETTING_RUN_REPORT_DAYS,
    SETTING_TMP_DAYS,
    load_retention,
    validate_setting,
)


def _settings(**values):
    settings = Mock()
    settings.get_setting.side_effect = lambda key, default=None, user_id=None: values.get(key, default)
    return settings


class TestValidateSetting:

    def test_a_key_this_module_does_not_own_is_never_rejected(self):
        assert validate_setting("thumbnail_video_fps", "nonsense") is None

    def test_the_range_ends_are_accepted(self):
        assert validate_setting(SETTING_TMP_DAYS, 0) is None
        assert validate_setting(SETTING_TMP_DAYS, MAX_DAYS) is None

    def test_a_negative_window_is_rejected(self):
        assert validate_setting(SETTING_RUN_REPORT_DAYS, -1) is not None

    def test_a_window_past_the_ceiling_is_rejected(self):
        assert validate_setting(SETTING_RUN_REPORT_DAYS, MAX_DAYS + 1) is not None

    def test_a_non_number_is_rejected(self):
        assert validate_setting(SETTING_LLM_TRACE_DAYS, "soon") is not None

    def test_a_boolean_is_rejected(self):
        assert validate_setting(SETTING_LLM_TRACE_DAYS, True) is not None

    def test_a_numeric_string_is_accepted(self):
        assert validate_setting(SETTING_TMP_DAYS, "14") is None


class TestLoadRetention:

    def test_it_reads_every_window(self):
        settings = _settings(
            tmp_retention_days=3, run_report_retention_days=14, llm_trace_retention_days=0
        )

        assert load_retention(settings) == {
            SETTING_TMP_DAYS: 3,
            SETTING_RUN_REPORT_DAYS: 14,
            SETTING_LLM_TRACE_DAYS: 0,
        }

    def test_an_absent_setting_falls_back_to_its_default(self):
        assert load_retention(_settings()) == DEFAULTS

    def test_a_stored_value_outside_the_range_is_clamped(self):
        settings = _settings(tmp_retention_days=-5, run_report_retention_days=999999)

        retention = load_retention(settings)

        assert retention[SETTING_TMP_DAYS] == 0
        assert retention[SETTING_RUN_REPORT_DAYS] == MAX_DAYS

    def test_a_junk_value_falls_back_rather_than_raising(self):
        assert load_retention(_settings(tmp_retention_days="soon"))[SETTING_TMP_DAYS] == DEFAULTS[SETTING_TMP_DAYS]
