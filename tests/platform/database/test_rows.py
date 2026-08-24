"""Coverage for the shared row-decode helpers repositories build on."""

from datetime import datetime

from src.platform.database.rows import dt_column, json_column, now_iso, row_get


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

    def test_a_datetime_passes_through_unchanged(self):
        value = datetime(2026, 1, 1)
        assert dt_column(value) is value

    def test_an_iso_string_is_parsed(self):
        assert dt_column("2026-01-01T00:00:00") == datetime(2026, 1, 1)

    def test_a_malformed_string_returns_none_instead_of_raising(self):
        assert dt_column("not a date") is None


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
