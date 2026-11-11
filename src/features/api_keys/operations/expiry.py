from datetime import datetime, timedelta
from typing import FrozenSet, Optional

from src.features.api_keys.errors import ApiKeyError
from src.features.api_keys.operations.limits import InstanceLimits
from src.platform.database.rows import dt_column
from src.platform.security.api_scopes import ApiScope

ALLOWED_LIFETIMES = (30, 90, 365)
ADMIN_MAX_DAYS = 90


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _lifetime_cap(scopes: FrozenSet[ApiScope], limits: InstanceLimits) -> Optional[int]:
    cap = limits.max_lifetime_days or None
    if ApiScope.ADMIN in scopes:
        return min(cap, ADMIN_MAX_DAYS) if cap else ADMIN_MAX_DAYS
    return cap


def _too_long(cap: int, is_admin: bool) -> ApiKeyError:
    if is_admin:
        return ApiKeyError(
            "lifetime_too_long", f"A key with the admin scope must expire within {cap} days."
        )
    return ApiKeyError("lifetime_too_long", f"Keys on this instance must expire within {cap} days.")


def expiry_for_new_key(
    scopes: FrozenSet[ApiScope],
    expires_in_days: Optional[int],
    provided: bool,
    limits: InstanceLimits,
    now: datetime,
) -> Optional[str]:
    is_admin = ApiScope.ADMIN in scopes
    cap = _lifetime_cap(scopes, limits)
    days = expires_in_days
    if days is None:
        if cap is None:
            return None
        choices = [option for option in ALLOWED_LIFETIMES if option <= cap]
        if not (is_admin and not provided and choices):
            raise _too_long(cap, is_admin)
        days = max(choices)
    if isinstance(days, bool) or days not in ALLOWED_LIFETIMES:
        raise ApiKeyError("invalid_expiry", "expires_in_days must be null, 30, 90 or 365.")
    if cap is not None and days > cap:
        raise _too_long(cap, is_admin)
    return stamp(now + timedelta(days=days))


def admin_expiry_is_within_cap(expires_at: Optional[str], now: datetime) -> bool:
    if not expires_at:
        return False
    expires = dt_column(expires_at)
    return expires is not None and expires - now <= timedelta(days=ADMIN_MAX_DAYS)
