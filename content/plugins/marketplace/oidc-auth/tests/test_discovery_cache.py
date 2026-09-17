import pytest

from backend import discovery, http_client
from backend.discovery import DiscoveryError, fetch_discovery, get_jwk

from .oidc_fixture import FakeIdP


@pytest.fixture
def idp():
    fixture = FakeIdP()
    http_client.set_transport_override(fixture.transport())
    yield fixture
    http_client.set_transport_override(None)


@pytest.mark.asyncio
async def test_discovery_is_cached_within_max_age(idp, monkeypatch):
    idp.cache_control = "max-age=3600"
    now = [1000.0]
    monkeypatch.setattr(discovery.time, "monotonic", lambda: now[0])

    await fetch_discovery(idp.issuer)
    await fetch_discovery(idp.issuer)

    assert idp.discovery_requests == 1


@pytest.mark.asyncio
async def test_discovery_is_refetched_after_max_age_expires(idp, monkeypatch):
    idp.cache_control = "max-age=60"
    now = [1000.0]
    monkeypatch.setattr(discovery.time, "monotonic", lambda: now[0])

    await fetch_discovery(idp.issuer)
    now[0] += 61
    await fetch_discovery(idp.issuer)

    assert idp.discovery_requests == 2


@pytest.mark.asyncio
async def test_discovery_falls_back_to_default_ttl_without_cache_control(idp, monkeypatch):
    idp.cache_control = None
    now = [1000.0]
    monkeypatch.setattr(discovery.time, "monotonic", lambda: now[0])

    await fetch_discovery(idp.issuer)
    now[0] += 60
    await fetch_discovery(idp.issuer)

    assert idp.discovery_requests == 1


@pytest.mark.asyncio
async def test_discovery_issuer_mismatch_raises(idp):
    idp.discovery_issuer_override = "https://a-different-issuer.example"
    with pytest.raises(DiscoveryError):
        await fetch_discovery(idp.issuer)


@pytest.mark.asyncio
async def test_jwks_is_cached_within_max_age(idp, monkeypatch):
    idp.cache_control = "max-age=3600"
    now = [1000.0]
    monkeypatch.setattr(discovery.time, "monotonic", lambda: now[0])

    jwks_uri = f"{idp.issuer}/jwks"
    await get_jwk(jwks_uri, idp.jwk["kid"], "RS256")
    await get_jwk(jwks_uri, idp.jwk["kid"], "RS256")

    assert idp.jwks_requests == 1


@pytest.mark.asyncio
async def test_jwks_is_refetched_after_max_age_expires(idp, monkeypatch):
    idp.cache_control = "max-age=60"
    now = [1000.0]
    monkeypatch.setattr(discovery.time, "monotonic", lambda: now[0])

    jwks_uri = f"{idp.issuer}/jwks"
    await get_jwk(jwks_uri, idp.jwk["kid"], "RS256")
    now[0] += 61
    await get_jwk(jwks_uri, idp.jwk["kid"], "RS256")

    assert idp.jwks_requests == 2


@pytest.mark.asyncio
async def test_discovery_max_age_of_zero_is_clamped_to_the_minimum_ttl(idp, monkeypatch):
    idp.cache_control = "max-age=0"
    now = [1000.0]
    monkeypatch.setattr(discovery.time, "monotonic", lambda: now[0])

    await fetch_discovery(idp.issuer)
    now[0] += discovery.MIN_CACHE_TTL_SECONDS - 1
    await fetch_discovery(idp.issuer)

    assert idp.discovery_requests == 1


@pytest.mark.asyncio
async def test_discovery_huge_max_age_is_clamped_to_the_maximum_ttl(idp, monkeypatch):
    idp.cache_control = "max-age=999999999"
    now = [1000.0]
    monkeypatch.setattr(discovery.time, "monotonic", lambda: now[0])

    await fetch_discovery(idp.issuer)
    now[0] += discovery.MAX_CACHE_TTL_SECONDS + 1

    await fetch_discovery(idp.issuer)
    assert idp.discovery_requests == 2
