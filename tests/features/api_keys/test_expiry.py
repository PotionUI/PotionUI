from datetime import datetime, timezone

import pytest

from src.features.api_keys.errors import ApiKeyError
from src.features.api_keys.operations.expiry import admin_expiry_is_within_cap, expiry_for_new_key
from src.features.api_keys.operations.limits import InstanceLimits
from src.platform.security.api_scopes import ApiScope as S

NOW = datetime(2026, 11, 11, 9, 0, 0, tzinfo=timezone.utc)


def limits(max_days=0):
    return InstanceLimits(
        keys_enabled=True, mcp_enabled=True,
        scopes_allowed=frozenset({S.READ, S.GENERATE, S.CONTENT, S.MCP, S.ADMIN}),
        max_lifetime_days=max_days,
    )


def expiry(scopes, days, provided=True, max_days=0):
    return expiry_for_new_key(frozenset(scopes), days, provided, limits(max_days), NOW)


class TestOrdinaryKeys:
    def test_never_is_accepted_for_an_ordinary_key(self):
        assert expiry([S.READ], None) is None

    def test_omitting_the_field_means_never(self):
        assert expiry([S.READ], None, provided=False) is None

    @pytest.mark.parametrize("days,expected", [(30, "2026-12-11T09:00:00+00:00"), (90, "2027-02-09T09:00:00+00:00"), (365, "2027-11-11T09:00:00+00:00")])
    def test_the_offered_lifetimes_become_a_utc_timestamp(self, days, expected):
        assert expiry([S.READ], days) == expected

    @pytest.mark.parametrize("days", [0, 1, 45, 366, -5, True])
    def test_other_lifetimes_are_refused(self, days):
        with pytest.raises(ApiKeyError) as raised:
            expiry([S.READ], days)

        assert raised.value.error == "invalid_expiry"
        assert raised.value.status_code == 400


class TestAdminKeys:
    def test_never_is_refused(self):
        with pytest.raises(ApiKeyError) as raised:
            expiry([S.ADMIN, S.READ], None)

        assert raised.value.error == "lifetime_too_long"

    def test_a_year_is_refused(self):
        with pytest.raises(ApiKeyError) as raised:
            expiry([S.ADMIN], 365)

        assert raised.value.error == "lifetime_too_long"

    def test_ninety_days_is_the_cap_and_is_accepted(self):
        assert expiry([S.ADMIN], 90) == "2027-02-09T09:00:00+00:00"

    def test_thirty_days_is_accepted(self):
        assert expiry([S.ADMIN], 30) == "2026-12-11T09:00:00+00:00"

    def test_omitting_the_field_defaults_to_ninety_days(self):
        assert expiry([S.ADMIN], None, provided=False) == "2027-02-09T09:00:00+00:00"

    def test_the_default_follows_a_shorter_instance_limit(self):
        assert expiry([S.ADMIN], None, provided=False, max_days=45) == "2026-12-11T09:00:00+00:00"

    def test_an_instance_limit_shorter_than_every_option_leaves_no_default(self):
        with pytest.raises(ApiKeyError) as raised:
            expiry([S.ADMIN], None, provided=False, max_days=10)

        assert raised.value.error == "lifetime_too_long"


class TestInstanceMaximumLifetime:
    def test_never_is_not_offered_when_a_maximum_is_set(self):
        with pytest.raises(ApiKeyError) as raised:
            expiry([S.READ], None, max_days=90)

        assert raised.value.error == "lifetime_too_long"

    def test_omitting_the_field_is_the_same_as_never(self):
        with pytest.raises(ApiKeyError):
            expiry([S.READ], None, provided=False, max_days=90)

    def test_a_longer_lifetime_than_the_maximum_is_refused(self):
        with pytest.raises(ApiKeyError) as raised:
            expiry([S.READ], 365, max_days=90)

        assert raised.value.error == "lifetime_too_long"

    def test_a_lifetime_within_the_maximum_is_accepted(self):
        assert expiry([S.READ], 90, max_days=90) == "2027-02-09T09:00:00+00:00"
        assert expiry([S.READ], 30, max_days=90) == "2026-12-11T09:00:00+00:00"

    def test_a_maximum_does_not_lengthen_the_admin_cap(self):
        with pytest.raises(ApiKeyError):
            expiry([S.ADMIN], 365, max_days=3650)


class TestExistingKeyGainingAdmin:
    def test_a_never_expiring_key_is_not_within_the_cap(self):
        assert not admin_expiry_is_within_cap(None, NOW)

    def test_a_key_expiring_within_ninety_days_of_now_is_within_the_cap(self):
        assert admin_expiry_is_within_cap("2027-02-09T09:00:00+00:00", NOW)

    def test_a_key_expiring_later_is_not(self):
        assert not admin_expiry_is_within_cap("2027-02-10T09:00:00+00:00", NOW)
