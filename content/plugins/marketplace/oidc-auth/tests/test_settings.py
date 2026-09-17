from backend.settings import PLUGIN_ID, load_settings


class _FakeSetting:
    def __init__(self, value):
        self.setting_value = value


class _FakeRepository:
    def __init__(self, values):
        self._values = values

    def get_plugin_setting(self, plugin_id, key):
        assert plugin_id == PLUGIN_ID
        value = self._values.get(key)
        return _FakeSetting(value) if value is not None else None


def test_defaults_when_nothing_configured():
    settings = load_settings(_FakeRepository({}))
    assert settings.issuer_url is None
    assert settings.client_id is None
    assert settings.client_secret is None
    assert settings.scopes == "openid profile email"
    assert settings.label == "SSO"
    assert settings.redirect_uri_override is None
    assert settings.is_configured() is False


def test_is_configured_requires_issuer_client_id_and_secret():
    settings = load_settings(
        _FakeRepository({"issuer_url": "https://idp.example", "client_id": "abc"})
    )
    assert settings.is_configured() is False

    settings = load_settings(
        _FakeRepository(
            {
                "issuer_url": "https://idp.example",
                "client_id": "abc",
                "client_secret": "shh",
            }
        )
    )
    assert settings.is_configured() is True


def test_blank_strings_are_treated_as_unset():
    settings = load_settings(_FakeRepository({"issuer_url": "   ", "redirect_uri": ""}))
    assert settings.issuer_url is None
    assert settings.redirect_uri_override is None


def test_whitespace_only_client_secret_is_treated_as_unset():
    settings = load_settings(_FakeRepository({"client_secret": "   "}))
    assert settings.client_secret is None


def test_client_secret_is_stripped():
    settings = load_settings(_FakeRepository({"client_secret": "  shh  "}))
    assert settings.client_secret == "shh"


def test_custom_scopes_and_label_are_read():
    settings = load_settings(
        _FakeRepository({"scopes": "openid email", "label": "Company SSO"})
    )
    assert settings.scopes == "openid email"
    assert settings.label == "Company SSO"


def test_issuer_trailing_slash_is_normalized_away():
    settings = load_settings(_FakeRepository({"issuer_url": "https://idp.example/"}))
    assert settings.issuer_url == "https://idp.example"


def test_issuer_is_stripped_and_normalized():
    settings = load_settings(_FakeRepository({"issuer_url": "  https://idp.example/  "}))
    assert settings.issuer_url == "https://idp.example"


def _configured(issuer_url):
    return load_settings(
        _FakeRepository(
            {"issuer_url": issuer_url, "client_id": "abc", "client_secret": "shh"}
        )
    )


def test_https_issuer_is_configured():
    assert _configured("https://idp.example").is_configured() is True


def test_plain_http_issuer_is_not_configured():
    assert _configured("http://idp.example").is_configured() is False


def test_http_localhost_issuer_is_configured():
    assert _configured("http://localhost:8080").is_configured() is True


def test_http_loopback_ip_issuer_is_configured():
    assert _configured("http://127.0.0.1:8080").is_configured() is True
