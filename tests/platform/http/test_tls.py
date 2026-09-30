import ssl
import sys
import types

import certifi
import pytest

from src.platform.http import tls


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    tls.client_ssl_context.cache_clear()
    yield
    tls.client_ssl_context.cache_clear()


class _FakeTruststoreContext:
    def __init__(self, protocol):
        self.protocol = protocol


def _install_fake_truststore(monkeypatch):
    module = types.ModuleType("truststore")
    module.SSLContext = _FakeTruststoreContext
    monkeypatch.setitem(sys.modules, "truststore", module)


def _block_truststore(monkeypatch):
    monkeypatch.setitem(sys.modules, "truststore", None)


def _record_default_context(monkeypatch):
    calls = {}
    real = ssl.create_default_context

    def fake(*args, **kwargs):
        context = real(*args, **kwargs)
        calls["kwargs"] = kwargs
        calls["context"] = context
        return context

    monkeypatch.setattr(tls.ssl, "create_default_context", fake)
    return calls


class _StubContext:
    def __init__(self):
        self.loaded = []

    def load_verify_locations(self, cafile=None, capath=None, cadata=None):
        self.loaded.append(cafile)


def _stub_default_context(monkeypatch):
    stub = _StubContext()
    monkeypatch.setattr(tls.ssl, "create_default_context", lambda *a, **k: stub)
    return stub


def test_env_file_override_wins_over_truststore(monkeypatch):
    monkeypatch.setattr(tls.sys, "platform", "win32")
    _install_fake_truststore(monkeypatch)
    monkeypatch.setenv("SSL_CERT_FILE", certifi.where())
    calls = _record_default_context(monkeypatch)

    context = tls.client_ssl_context()

    assert isinstance(context, ssl.SSLContext)
    assert calls["kwargs"] == {"cafile": certifi.where(), "capath": None}


def test_env_dir_override_passes_capath(monkeypatch, tmp_path):
    calls = {}

    def fake(**kwargs):
        calls.update(kwargs)
        return "ctx"

    monkeypatch.setattr(tls.ssl, "create_default_context", fake)
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path))

    assert tls.client_ssl_context() == "ctx"
    assert calls == {"cafile": None, "capath": str(tmp_path)}


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_os_store_platforms_use_truststore(monkeypatch, platform):
    monkeypatch.setattr(tls.sys, "platform", platform)
    _install_fake_truststore(monkeypatch)

    context = tls.client_ssl_context()

    assert isinstance(context, _FakeTruststoreContext)
    assert context.protocol == ssl.PROTOCOL_TLS_CLIENT


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_os_store_platforms_without_truststore_fall_back_to_certifi(monkeypatch, platform):
    monkeypatch.setattr(tls.sys, "platform", platform)
    _block_truststore(monkeypatch)
    stub = _stub_default_context(monkeypatch)

    context = tls.client_ssl_context()

    assert context is stub
    assert stub.loaded == [certifi.where()]


def test_linux_loads_certifi_on_top_and_ignores_truststore(monkeypatch):
    monkeypatch.setattr(tls.sys, "platform", "linux")
    _install_fake_truststore(monkeypatch)
    stub = _stub_default_context(monkeypatch)

    context = tls.client_ssl_context()

    assert context is stub
    assert stub.loaded == [certifi.where()]


def test_context_is_cached(monkeypatch):
    monkeypatch.setattr(tls.sys, "platform", "linux")

    assert tls.client_ssl_context() is tls.client_ssl_context()


@pytest.mark.asyncio
async def test_aiohttp_connector_uses_shared_context(monkeypatch):
    monkeypatch.setattr(tls.sys, "platform", "linux")

    connector = tls.aiohttp_connector()
    try:
        assert connector._ssl is tls.client_ssl_context()
    finally:
        await connector.close()


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_unusable_override_falls_through_to_platform_default(monkeypatch, tmp_path, platform, caplog):
    monkeypatch.setattr(tls.sys, "platform", platform)
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing.pem"))
    _install_fake_truststore(monkeypatch)
    stub = _StubContext()
    real = ssl.create_default_context

    def fake(*args, **kwargs):
        if kwargs.get("cafile"):
            return real(*args, **kwargs)
        return stub

    monkeypatch.setattr(tls.ssl, "create_default_context", fake)

    with caplog.at_level("WARNING"):
        context = tls.client_ssl_context()

    if platform == "win32":
        assert isinstance(context, _FakeTruststoreContext)
    else:
        assert context is stub
        assert stub.loaded == [certifi.where()]
    assert "SSL_CERT_FILE" in caplog.text
    assert "missing.pem" in caplog.text


def test_invalid_bundle_falls_through(monkeypatch, tmp_path):
    bad = tmp_path / "bad.pem"
    bad.write_text("not a certificate", encoding="utf-8")
    monkeypatch.setattr(tls.sys, "platform", "win32")
    monkeypatch.setenv("SSL_CERT_FILE", str(bad))
    _install_fake_truststore(monkeypatch)

    assert isinstance(tls.client_ssl_context(), _FakeTruststoreContext)


def test_is_certificate_error_covers_message_only_errors():
    assert tls.is_certificate_error(ssl.SSLCertVerificationError(1, "x"))
    assert tls.is_certificate_error(RuntimeError("[SSL: CERTIFICATE_VERIFY_FAILED] nope"))
    assert not tls.is_certificate_error(ConnectionError("reset"))


def test_certificate_failure_message_is_platform_aware(monkeypatch):
    monkeypatch.setattr(tls.sys, "platform", "linux")
    linux = tls.certificate_failure_message("huggingface.co")
    monkeypatch.setattr(tls.sys, "platform", "win32")
    windows = tls.certificate_failure_message("huggingface.co")

    assert "huggingface.co" in linux and "sudo apt install ca-certificates" in linux
    assert "apt" not in windows and "SSL_CERT_FILE" in windows
