import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfoNotFoundError

import pytest

from src.features.plans import windows
from src.features.plans.errors import LimitExceeded
from src.features.user_groups.constants import ALL_USERS_GROUP_ID
from src.platform.plugins.limit_kinds import AdmissionRequest
from tests.features.plans.conftest import make_plan

NOON = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def _missing(key):
    raise ZoneInfoNotFoundError(f"No time zone found with key {key}")


@pytest.fixture
def no_tz_database(monkeypatch):
    monkeypatch.setattr(windows, "ZoneInfo", _missing)
    monkeypatch.setattr(windows, "_warned", set(), raising=False)


def test_utc_resolves_without_a_tz_database(no_tz_database):
    assert windows.valid_timezone("UTC")
    assert windows.zone_key("UTC") == "UTC"
    assert windows.reset_label("day", "UTC") == "Resets at 00:00 UTC"
    assert windows.window_bounds("day", "UTC", NOON) == (
        datetime(2026, 10, 5, tzinfo=timezone.utc),
        datetime(2026, 10, 6, tzinfo=timezone.utc),
    )
    assert windows.window_bounds("month", None, NOON) == (
        datetime(2026, 10, 1, tzinfo=timezone.utc),
        datetime(2026, 11, 1, tzinfo=timezone.utc),
    )


def test_a_zone_missing_from_the_database_falls_back_to_utc_and_logs_once(no_tz_database, caplog):
    caplog.set_level(logging.WARNING, logger=windows.__name__)

    assert not windows.valid_timezone("Europe/Warsaw")
    assert windows.zone("Europe/Warsaw") is timezone.utc
    assert windows.zone_key("Europe/Warsaw") == "UTC"
    assert windows.window_bounds("day", "Europe/Warsaw", NOON)[1] == datetime(2026, 10, 6, tzinfo=timezone.utc)

    warnings = [record for record in caplog.records if "Europe/Warsaw" in record.getMessage()]
    assert len(warnings) == 1


def test_an_unknown_zone_falls_back_to_utc_with_a_log_line(monkeypatch, caplog):
    monkeypatch.setattr(windows, "_warned", set(), raising=False)
    caplog.set_level(logging.WARNING, logger=windows.__name__)

    assert not windows.valid_timezone("Mars/Olympus_Mons")
    assert windows.zone_key("Mars/Olympus_Mons") == "UTC"
    assert windows.reset_label("month", "Mars/Olympus_Mons") == "Resets on the 1st, 00:00 UTC"
    assert any("Mars/Olympus_Mons" in record.getMessage() for record in caplog.records)


def test_the_daily_limit_still_refuses_without_a_tz_database(no_tz_database, seed, plans, guard, settings):
    seed.user("u1")
    plans.guard.plans.set_group_plan(ALL_USERS_GROUP_ID, make_plan(plans, "Free", generations_per_day=1).id)
    settings.set_setting("plans_day_timezone", "Europe/Warsaw")

    guard.admit(AdmissionRequest(point="submit", user_id="u1", engine="native", ref_id="g1"))
    with pytest.raises(LimitExceeded) as refused:
        guard.admit(AdmissionRequest(point="submit", user_id="u1", engine="native", ref_id="g2"))

    payload = refused.value.payload()
    assert payload["code"] == "daily_generations_exceeded"
    assert payload["resets_at"] == "2026-10-06T00:00:00+00:00"
    assert "(at 00:00 UTC)" in payload["message"]


def test_plan_views_list_kinds_and_my_limits_without_a_tz_database(no_tz_database, seed, plans, manager):
    user = seed.user("u1")
    plans.guard.plans.set_group_plan(ALL_USERS_GROUP_ID, make_plan(plans, "Free", generations_per_day=3).id)

    daily = next(kind for kind in manager.kinds()["kinds"] if kind["key"] == "generations_per_day")
    assert daily["reset"]["label"] == "Resets at 00:00 UTC"

    limits = manager.my_limits(user)
    assert limits["timezone"] == "UTC"
    assert [row["kind"] for row in limits["limits"]] == ["generations_per_day"]
