"""Coverage for the shared row-decode helpers repositories build on."""

from datetime import datetime, timedelta, timezone

from src.platform.database.rows import as_utc, dt_column, dt_iso, json_column, now_iso, now_utc, row_get


class TestJsonColumn:
    def test_falsy_value_returns_the_default(self):
        assert json_column(None, {"a": 1}) == {"a": 1}
        assert json_column("", []) == []

    def test_valid_json_is_decoded(self):
        assert json_column('{"a": 1}', None) == {"a": 1}

    def test_malformed_json_returns_the_default_instead_of_raising(self):
        assert json_column("not json", "fallback") == "fallback"

    def test_default_defaults_to_none(self):
        assert json_column(None) is None


class TestDtColumn:
    def test_falsy_value_is_none(self):
        assert dt_column(None) is None
        assert dt_column("") is None

    def test_a_naive_datetime_is_treated_as_utc(self):
        value = datetime(2026, 1, 1)
        result = dt_column(value)
        assert result == datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert result.tzinfo is timezone.utc

    def test_an_aware_non_utc_datetime_is_converted_to_utc(self):
        tz = timezone(timedelta(hours=5))
        value = datetime(2026, 1, 1, 5, 0, tzinfo=tz)
        result = dt_column(value)
        assert result == datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        assert result.tzinfo is timezone.utc

    def test_an_offset_less_iso_string_is_read_as_utc(self):
        result = dt_column("2026-01-01T00:00:00")
        assert result == datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert result.tzinfo is timezone.utc

    def test_an_iso_string_with_offset_is_converted_to_utc(self):
        result = dt_column("2026-01-01T05:00:00+05:00")
        assert result == datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)

    def test_a_malformed_string_returns_none_instead_of_raising(self):
        assert dt_column("not a date") is None


class TestAsUtc:
    def test_naive_is_taken_to_already_be_utc(self):
        result = as_utc(datetime(2026, 1, 1))
        assert result == datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert result.tzinfo is timezone.utc

    def test_aware_non_utc_is_converted(self):
        tz = timezone(timedelta(hours=-3))
        result = as_utc(datetime(2026, 1, 1, 0, 0, tzinfo=tz))
        assert result == datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc)


class TestDtIso:
    def test_none_is_none(self):
        assert dt_iso(None) is None
        assert dt_iso("") is None

    def test_output_carries_the_utc_offset(self):
        assert dt_iso("2026-01-01T00:00:00").endswith("+00:00")

    def test_a_datetime_input_is_rendered_with_its_utc_offset(self):
        assert dt_iso(datetime(2026, 1, 1)).endswith("+00:00")

    def test_a_malformed_string_returns_none(self):
        assert dt_iso("not a date") is None


class TestNowUtc:
    def test_returns_an_aware_utc_datetime(self):
        value = now_utc()
        assert value.tzinfo is timezone.utc


class TestRowGet:
    def test_returns_the_column_value_when_present(self):
        assert row_get({"a": 1}, "a") == 1

    def test_missing_column_returns_the_default(self):
        assert row_get({"a": 1}, "b") is None
        assert row_get({"a": 1}, "b", "fallback") == "fallback"

    def test_a_stored_null_also_falls_back_to_the_default(self):
        assert row_get({"a": None}, "a") is None
        assert row_get({"a": None}, "a", "fallback") == "fallback"

    def test_default_defaults_to_none(self):
        assert row_get({}, "missing") is None


class TestNowIso:
    def test_returns_a_parseable_iso_string(self):
        value = now_iso()
        assert isinstance(value, str)
        datetime.fromisoformat(value)

    def test_carries_the_utc_offset(self):
        assert now_iso().endswith("+00:00")
