"""PERF-11: a system-wide `Settings.get_setting(key)` (no `user_id`) must read
the single row for `key` through `SettingRepository.get_setting_by_key`,
rather than materializing and type-converting every row in `settings` via
`get_effective_settings()` just to pick one key back out of the resulting
dict. `get_all_settings()`/`get_effective_settings()` stay unchanged for
callers that genuinely need a full snapshot.
"""
import statistics
import sys
import time
from unittest.mock import patch

import pytest

import src.platform.settings.repository as repository_module
from src.platform.settings.records import SettingValueType
from src.platform.settings.repository import SettingRepository
from src.platform.settings.settings import Settings

SETTING_COUNT = 200
REPS = 25
TRACE_READS = 8


class TestGetSettingKeyedRead:

    @pytest.fixture
    def repository(self, mock_db):
        return SettingRepository()

    @pytest.fixture
    def bulk_settings(self, repository):
        """Seed a bounded scratch table with SETTING_COUNT rows plus one
        boolean setting standing in for `chat_llm_call_tracing` (PERF-09B's
        per-`record()` read)."""
        for i in range(SETTING_COUNT):
            repository.create_setting(
                key=f"bench_setting_{i}",
                value=f"value_{i}",
                value_type=SettingValueType.STRING,
            )
        repository.create_setting(
            key="bench_trace_flag",
            value="true",
            value_type=SettingValueType.BOOLEAN,
        )
        return repository

    def _create_user(self, repository, user_id):
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash, account_type) VALUES (?, ?, ?, ?, ?)",
                (user_id, user_id, f"{user_id}@example.test", "test_hash", "USER"),
            )
        return user_id

    def _count_conversions(self):
        conversions = []
        real_from_row = repository_module._setting_from_row

        def counting(row):
            conversions.append(row)
            return real_from_row(row)

        return conversions, patch.object(repository_module, "_setting_from_row", side_effect=counting)

    def test_single_read_does_not_materialize_every_row(self, bulk_settings):
        settings = Settings(bulk_settings)
        conversions, patcher = self._count_conversions()

        with patcher:
            value = settings.get_setting("bench_setting_42")

        assert value == "value_42"
        assert len(conversions) == 1, (
            f"expected exactly one row converted for a single-key read, got {len(conversions)}"
        )

    def test_missing_key_uses_default_without_row_conversion(self, bulk_settings):
        settings = Settings(bulk_settings)
        conversions, patcher = self._count_conversions()

        with patcher:
            value = settings.get_setting("does_not_exist", default="fallback")

        assert value == "fallback"
        assert conversions == []

    def test_falsy_typed_values_are_preserved_not_defaulted(self, bulk_settings):
        """False/0/""/None must come back as themselves, never silently
        replaced by `default` (a naive `value or default` bug)."""
        bulk_settings.create_setting("bench_false_bool", "false", SettingValueType.BOOLEAN)
        bulk_settings.create_setting("bench_zero_int", "0", SettingValueType.INTEGER)
        bulk_settings.create_setting("bench_empty_string", "", SettingValueType.STRING)
        bulk_settings.create_setting("bench_null_json", "null", SettingValueType.JSON)

        settings = Settings(bulk_settings)

        assert settings.get_setting("bench_false_bool", default=True) is False
        assert settings.get_setting("bench_zero_int", default=99) == 0
        assert settings.get_setting("bench_empty_string", default="x") == ""
        assert settings.get_setting("bench_null_json", default="x") is None

    def test_updated_value_visible_immediately_no_cache(self, bulk_settings):
        settings = Settings(bulk_settings)
        assert settings.get_setting("bench_setting_7") == "value_7"

        setting = bulk_settings.get_setting_by_key("bench_setting_7")
        bulk_settings.update_setting_value(setting.id, "value_7_updated")

        assert settings.get_setting("bench_setting_7") == "value_7_updated"

    def test_user_override_fallback_unchanged(self, bulk_settings):
        """The `user_id` branch is untouched by this change - still
        override-then-system-default, resolved without a full-table read."""
        settings = Settings(bulk_settings)
        user_id = self._create_user(bulk_settings, "bench-user-1")
        setting = bulk_settings.get_setting_by_key("bench_setting_3")
        bulk_settings.update_user_setting(user_id, setting.id, "override")

        assert settings.get_setting("bench_setting_3", user_id=user_id) == "override"
        assert settings.get_setting("bench_setting_3") == "value_3"
        assert settings.get_setting("bench_setting_3", user_id="user-with-no-override") == "value_3"

    def test_row_conversions_scale_with_reads_not_table_size(self, bulk_settings):
        """The behavioural proof at scale: the removed full-table path would
        convert every row on every read; the keyed path converts exactly one
        row per read, independent of SETTING_COUNT."""
        settings = Settings(bulk_settings)
        total_rows = len(bulk_settings.get_all_settings())

        old_conversions, old_patcher = self._count_conversions()
        with old_patcher:
            for _ in range(TRACE_READS):
                bulk_settings.get_effective_settings(None).get("bench_trace_flag", True)

        new_conversions, new_patcher = self._count_conversions()
        with new_patcher:
            for _ in range(TRACE_READS):
                settings.get_setting("bench_trace_flag", True)

        assert len(old_conversions) == TRACE_READS * total_rows
        assert len(new_conversions) == TRACE_READS

    def test_scaling_measurement(self, bulk_settings):
        """Informational only (see test_connection_setup_overhead.py for why
        absolute timings aren't asserted on a shared box): prints the
        before/after wall time for a single read and for PERF-09B's 8-read
        trace-setting path, separating remaining per-call connection setup
        from the removed O(row count) conversion work."""
        settings = Settings(bulk_settings)

        def old_single_read():
            return bulk_settings.get_effective_settings(None).get("bench_setting_99", None)

        def new_single_read():
            return settings.get_setting("bench_setting_99")

        def old_trace_reads():
            for _ in range(TRACE_READS):
                bulk_settings.get_effective_settings(None).get("bench_trace_flag", True)

        def new_trace_reads():
            for _ in range(TRACE_READS):
                settings.get_setting("bench_trace_flag", True)

        def median_time(fn):
            times = []
            for _ in range(REPS):
                t0 = time.perf_counter()
                fn()
                times.append(time.perf_counter() - t0)
            return statistics.median(times)

        old_single = median_time(old_single_read)
        new_single = median_time(new_single_read)
        old_trace = median_time(old_trace_reads)
        new_trace = median_time(new_trace_reads)

        print(
            f"\n--- PERF-11 keyed-read measurement ({SETTING_COUNT + 1} rows, {REPS} reps, median) ---",
            file=sys.stderr,
        )
        print(
            f"single read              before(full-table): {old_single * 1000:.3f} ms   "
            f"after(keyed): {new_single * 1000:.3f} ms",
            file=sys.stderr,
        )
        print(
            f"trace path x{TRACE_READS} reads    before(full-table): {old_trace * 1000:.3f} ms   "
            f"after(keyed): {new_trace * 1000:.3f} ms",
            file=sys.stderr,
        )
