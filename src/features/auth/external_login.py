from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from typing import Any, Dict, Optional, TYPE_CHECKING

from src.features.auth.repository import ExternalIdentityRepository
from src.features.user_groups.repository import UserGroupRepository
from src.features.users.repository import UserRepository
from src.platform.security.auth import Auth
from src.platform.security.login_handoff import LoginHandoffStore
from src.platform.security.user import AccountType, User

if TYPE_CHECKING:
    from src.platform.settings.settings import Settings

logger = logging.getLogger(__name__)

AUTO_CREATE_SETTING = "external_login_auto_create"
DEFAULT_GROUP_SETTING = "external_login_default_group"
LINK_BY_EMAIL_SETTING = "external_login_link_by_email"

USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 64
USERNAME_FALLBACK = "user"
_USERNAME_EXTRA_CHARS = "._-"


class ExternalLoginError(ValueError):
    pass


@dataclass(frozen=True)
class ExternalSession:
    user: User
    access_token: str
    handoff_code: str
    created: bool
    token_type: str = "bearer"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user": self.user.to_dict(),
            "access_token": self.access_token,
            "token_type": self.token_type,
            "handoff_code": self.handoff_code,
            "created": self.created,
        }


def normalize_username(candidate: str) -> str:
    kept = [
        char
        for char in (candidate or "").strip()
        if char.isalnum() or char in _USERNAME_EXTRA_CHARS
    ]
    return "".join(kept)[:USERNAME_MAX_LENGTH]


def username_candidates(claims: Dict[str, Any], subject: str) -> list:
    seeds = []
    preferred = claims.get("preferred_username")
    if isinstance(preferred, str):
        seeds.append(preferred)
    email = claims.get("email")
    if isinstance(email, str) and "@" in email:
        seeds.append(email.split("@", 1)[0])
    name = claims.get("name")
    if isinstance(name, str):
        seeds.append(name.replace(" ", ""))
    seeds.append(subject)

    candidates = []
    for seed in seeds:
        normalized = normalize_username(seed)
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    return candidates


class ExternalLoginManager:
    def __init__(
        self,
        auth: Auth,
        users: UserRepository,
        external_identities: ExternalIdentityRepository,
        user_groups: UserGroupRepository,
        settings: "Settings",
        handoff: LoginHandoffStore,
    ):
        self.auth = auth
        self.users = users
        self.external_identities = external_identities
        self.user_groups = user_groups
        self.settings = settings
        self.handoff = handoff

    def sign_in_external(
        self,
        issuer: str,
        sub: str,
        claims: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
    ) -> ExternalSession:
        issuer = (issuer or "").strip()
        sub = (sub or "").strip()
        if not issuer:
            raise ExternalLoginError("An external login needs an issuer")
        if not sub:
            raise ExternalLoginError("An external login needs a subject")

        claims = dict(claims or {})

        user, created = self._resolve_user(issuer, sub, claims)
        access_token = self.auth.issue_session(user, ip_address=ip_address)
        handoff_code = self.handoff.issue(access_token)

        logger.info(
            "External login signed in %s via %s (created: %s)",
            user.username,
            issuer,
            created,
        )

        return ExternalSession(
            user=user,
            access_token=access_token,
            handoff_code=handoff_code,
            created=created,
        )

    def _resolve_user(
        self, issuer: str, sub: str, claims: Dict[str, Any]
    ) -> tuple:
        mapping = self.external_identities.get(issuer, sub)
        if mapping is not None:
            user = self.users.get_by_id(mapping.user_id)
            if user is None:
                self.external_identities.delete(mapping.id)
                raise ExternalLoginError(
                    "The account this external identity pointed at no longer exists"
                )
            self.external_identities.touch_last_login(mapping.id)
            return user, False

        linked = self._link_by_email(claims)
        if linked is not None:
            self.external_identities.create(issuer, sub, linked.id)
            return linked, False

        if not self._auto_create_enabled():
            raise ExternalLoginError(
                "This external account is not linked to a PotionUI user, and "
                "creating accounts from an external login is turned off."
            )

        user = self._create_user(sub, claims)
        self.external_identities.create(issuer, sub, user.id)
        return user, True

    def _auto_create_enabled(self) -> bool:
        return _truthy(self.settings.get_setting(AUTO_CREATE_SETTING, False))

    def _link_by_email_enabled(self) -> bool:
        return _truthy(self.settings.get_setting(LINK_BY_EMAIL_SETTING, False))

    def _link_by_email(self, claims: Dict[str, Any]) -> Optional[User]:
        if not self._link_by_email_enabled():
            return None
        email = claims.get("email")
        if not isinstance(email, str) or not email.strip():
            return None
        if not _truthy(claims.get("email_verified")):
            logger.warning(
                "Refusing to link an external identity by an unverified email"
            )
            return None
        return self.users.get_by_email(email.strip())

    def _create_user(self, sub: str, claims: Dict[str, Any]) -> User:
        username = self._available_username(sub, claims)
        email = self._account_email(sub, claims, username)
        password_hash = self.auth.passwords.hash(secrets.token_urlsafe(32))

        user = self.users.create(
            username=username,
            email=email,
            password_hash=password_hash,
            account_type=AccountType.USER,
        )
        self._assign_default_group(user)
        return user

    def _available_username(self, sub: str, claims: Dict[str, Any]) -> str:
        for candidate in username_candidates(claims, sub):
            base = candidate
            if len(base) < USERNAME_MIN_LENGTH:
                base = (base + USERNAME_FALLBACK)[:USERNAME_MAX_LENGTH]
            if not self.users.exists_by_username(base):
                return base
            for suffix in range(2, 1000):
                tail = str(suffix)
                trimmed = base[: USERNAME_MAX_LENGTH - len(tail)]
                deduplicated = f"{trimmed}{tail}"
                if not self.users.exists_by_username(deduplicated):
                    return deduplicated
        raise ExternalLoginError("Could not derive a free username for this identity")

    def _account_email(self, sub: str, claims: Dict[str, Any], username: str) -> str:
        email = claims.get("email")
        if isinstance(email, str) and email.strip() and not self.users.exists_by_email(email.strip()):
            return email.strip()
        return f"{username}@external.invalid"

    def _assign_default_group(self, user: User) -> None:
        group_id = (self.settings.get_setting(DEFAULT_GROUP_SETTING, "") or "").strip()
        if not group_id:
            return
        if self.user_groups.get_group_by_id(group_id) is None:
            logger.warning(
                "External login default group '%s' does not exist; %s was not added to it",
                group_id,
                user.username,
            )
            return
        self.user_groups.add_user_to_group(group_id, user.id)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return False
