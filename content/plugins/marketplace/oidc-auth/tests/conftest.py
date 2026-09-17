import importlib
import sys
from pathlib import Path
from typing import Optional

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[3] / "content" / "plugins" / "marketplace" / "oidc-auth"

for _key in list(sys.modules):
    if _key == "backend" or _key.startswith("backend."):
        del sys.modules[_key]

if str(PLUGIN_ROOT) in sys.path:
    sys.path.remove(str(PLUGIN_ROOT))
sys.path.insert(0, str(PLUGIN_ROOT))

importlib.invalidate_caches()
import backend


@pytest.fixture(autouse=True)
def _reset_oidc_state():
    from backend import discovery, http_client

    discovery.reset_cache()
    http_client.set_transport_override(None)
    yield
    discovery.reset_cache()
    http_client.set_transport_override(None)


@pytest.fixture
def make_settings():
    from backend.settings import OIDCSettings

    def _make(
        issuer_url: str = "https://idp.example",
        client_id: str = "test-client",
        client_secret: str = "test-client-secret",
        scopes: str = "openid profile email",
        label: str = "SSO",
        redirect_uri_override: Optional[str] = None,
    ) -> OIDCSettings:
        return OIDCSettings(
            issuer_url=issuer_url,
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes,
            label=label,
            redirect_uri_override=redirect_uri_override,
        )

    return _make
