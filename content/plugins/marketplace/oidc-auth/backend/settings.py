from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from src.plugin_api import PluginRepository

PLUGIN_ID = "oidc-auth"

DEFAULT_SCOPES = "openid profile email"
DEFAULT_LABEL = "SSO"
LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})


def _normalize_issuer(raw: Optional[str]) -> Optional[str]:
    value = (raw or "").strip().rstrip("/")
    return value or None


@dataclass(frozen=True)
class OIDCSettings:
    issuer_url: Optional[str]
    client_id: Optional[str]
    client_secret: Optional[str]
    scopes: str
    label: str
    redirect_uri_override: Optional[str]

    def is_configured(self) -> bool:
        if not (self.issuer_url and self.client_id and self.client_secret):
            return False
        parsed = urlparse(self.issuer_url)
        if parsed.scheme == "https":
            return True
        return parsed.scheme == "http" and parsed.hostname in LOOPBACK_HOSTNAMES


def load_settings(repository: Optional[PluginRepository] = None) -> OIDCSettings:
    repo = repository or PluginRepository()

    def _value(key: str) -> Optional[str]:
        setting = repo.get_plugin_setting(PLUGIN_ID, key)
        return setting.setting_value if setting else None

    return OIDCSettings(
        issuer_url=_normalize_issuer(_value("issuer_url")),
        client_id=(_value("client_id") or "").strip() or None,
        client_secret=(_value("client_secret") or "").strip() or None,
        scopes=_value("scopes") or DEFAULT_SCOPES,
        label=_value("label") or DEFAULT_LABEL,
        redirect_uri_override=(_value("redirect_uri") or "").strip() or None,
    )
