import asyncio
import socket

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.features.plugins import remote
from src.features.plugins.remote import ALLOW_LOCAL_SOURCES_ENV, FetchError, check_source_url, fetch_bytes, local_sources_allowed

LOOPBACK = ("127.0.0.1", "::1")


class ConnectionAttempted(AssertionError):
    pass


@pytest.fixture
def loopback_connections_only(monkeypatch):
    real = aiohttp.TCPConnector._wrap_create_connection
    attempts = []

    async def guarded(self, *args, addr_infos, **kwargs):
        addresses = [info[4][0] for info in addr_infos]
        if any(address not in LOOPBACK for address in addresses):
            attempts.append(addresses)
            raise ConnectionAttempted(f"the fetch opened a connection to {addresses}")
        return await real(self, *args, addr_infos=addr_infos, **kwargs)

    monkeypatch.setattr(aiohttp.TCPConnector, "_wrap_create_connection", guarded)
    return attempts


async def _serve(routes):
    app = web.Application()
    for path, handler in routes.items():
        app.router.add_get(path, handler)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    return server


def _url(server, path: str) -> str:
    return str(server.make_url(path))


async def test_a_local_source_answers_its_bytes_when_local_sources_are_allowed():
    server = await _serve({"/plugins.json": lambda request: web.Response(body=b'{"schema": 1}')})
    try:
        assert await fetch_bytes(_url(server, "/plugins.json"), max_bytes=100, timeout_s=5, allow_local=True) == b'{"schema": 1}'
    finally:
        await server.close()


async def test_redirects_are_followed_and_every_hop_is_checked():
    async def hop(request):
        raise web.HTTPFound("/final")

    server = await _serve({"/start": hop, "/final": lambda request: web.Response(body=b"done")})
    try:
        assert await fetch_bytes(_url(server, "/start"), max_bytes=100, timeout_s=5, allow_local=True) == b"done"
    finally:
        await server.close()


async def test_a_redirect_is_checked_like_the_first_url():
    async def elsewhere(request):
        raise web.HTTPFound("file:///etc/passwd")

    server = await _serve({"/start": elsewhere})
    try:
        with pytest.raises(FetchError, match="Only https URLs"):
            await fetch_bytes(_url(server, "/start"), max_bytes=100, timeout_s=5, allow_local=True)
    finally:
        await server.close()


async def test_too_many_redirects_are_refused():
    async def loop(request):
        raise web.HTTPFound("/loop")

    server = await _serve({"/loop": loop})
    try:
        with pytest.raises(FetchError, match="More than 5 redirects"):
            await fetch_bytes(_url(server, "/loop"), max_bytes=100, timeout_s=5, allow_local=True)
    finally:
        await server.close()


async def test_a_body_over_the_limit_is_refused_whether_or_not_it_declares_its_length():
    async def streamed(request):
        response = web.StreamResponse()
        await response.prepare(request)
        for _ in range(10):
            await response.write(b"x" * 100)
        await response.write_eof()
        return response

    server = await _serve({"/declared": lambda request: web.Response(body=b"x" * 1000), "/streamed": streamed})
    try:
        for path in ("/declared", "/streamed"):
            with pytest.raises(FetchError, match="larger than 500 bytes"):
                await fetch_bytes(_url(server, path), max_bytes=500, timeout_s=5, allow_local=True)
    finally:
        await server.close()


async def test_a_declared_length_over_the_limit_is_refused_before_the_body_arrives():
    async def slow(request):
        response = web.StreamResponse()
        response.content_length = 10_000
        await response.prepare(request)
        await asyncio.sleep(3)
        await response.write(b"x" * 10_000)
        return response

    server = await _serve({"/slow": slow})
    try:
        with pytest.raises(FetchError, match="larger than 500 bytes"):
            await fetch_bytes(_url(server, "/slow"), max_bytes=500, timeout_s=1.5, allow_local=True)
    finally:
        await server.close()


async def test_a_source_that_stops_sending_is_dropped_after_the_read_timeout_not_the_total_one():
    async def stalled(request):
        response = web.StreamResponse()
        response.content_length = 1000
        await response.prepare(request)
        await response.write(b"x" * 10)
        await asyncio.sleep(5)
        return response

    server = await _serve({"/stalled": stalled})
    try:
        loop = asyncio.get_running_loop()
        started = loop.time()
        with pytest.raises(FetchError, match="sent nothing for 0.5s"):
            await fetch_bytes(_url(server, "/stalled"), max_bytes=10_000, timeout_s=20, allow_local=True, read_timeout_s=0.5)
        assert loop.time() - started < 4
    finally:
        await server.close()


async def test_errors_after_a_redirect_name_the_host_and_not_the_signed_url():
    async def signed(request):
        raise web.HTTPFound("/asset?X-Amz-Signature=s3cret-value")

    async def asset(request):
        if request.query.get("big"):
            return web.Response(body=b"x" * 1000)
        return web.Response(status=403)

    async def signed_big(request):
        raise web.HTTPFound("/asset?big=1&X-Amz-Signature=s3cret-value")

    server = await _serve({"/start": signed, "/start-big": signed_big, "/asset": asset})
    try:
        with pytest.raises(FetchError, match="127.0.0.1 answered HTTP 403.") as refused:
            await fetch_bytes(_url(server, "/start"), max_bytes=100, timeout_s=5, allow_local=True)
        assert "s3cret-value" not in str(refused.value)
        with pytest.raises(FetchError, match="The file from 127.0.0.1 is larger than 100 bytes") as refused:
            await fetch_bytes(_url(server, "/start-big"), max_bytes=100, timeout_s=5, allow_local=True)
        assert "s3cret-value" not in str(refused.value)
    finally:
        await server.close()


@pytest.mark.parametrize("allow_local", [False, True])
@pytest.mark.parametrize(
    "url", ["ftp://user:s3cret-value@example.com/x.zip", "http://user:s3cret-value@example.com/x.zip", "https://:s3cret-value@example.com/x"]
)
def test_credentials_are_refused_before_the_scheme_and_never_echoed(url, allow_local):
    with pytest.raises(FetchError, match="Credentials inside the URL are not allowed") as refused:
        check_source_url(url, allow_local=allow_local)

    assert "s3cret-value" not in str(refused.value)


def test_a_refused_scheme_names_the_scheme_and_not_the_url():
    with pytest.raises(FetchError, match="Only https URLs are allowed, not ftp.") as refused:
        check_source_url("ftp://example.com/x.zip?token=s3cret-value", allow_local=False)

    assert "s3cret-value" not in str(refused.value)


async def test_an_error_status_is_refused_with_the_status():
    server = await _serve({"/missing": lambda request: web.Response(status=404)})
    try:
        with pytest.raises(FetchError, match="HTTP 404"):
            await fetch_bytes(_url(server, "/missing"), max_bytes=100, timeout_s=5, allow_local=True)
    finally:
        await server.close()


async def test_a_local_server_is_refused_when_local_sources_are_not_allowed():
    server = await _serve({"/plugins.json": lambda request: web.Response(body=b"{}")})
    try:
        with pytest.raises(FetchError, match="Only https URLs"):
            await fetch_bytes(_url(server, "/plugins.json"), max_bytes=100, timeout_s=5, allow_local=False)
    finally:
        await server.close()


async def test_a_public_name_that_resolves_to_a_private_address_is_refused_before_connecting():
    async def lookup(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]

    with pytest.raises(FetchError, match="local or private address"):
        await fetch_bytes("https://catalog.example.com/plugins.json", max_bytes=100, timeout_s=5, allow_local=False, lookup=lookup)


@pytest.mark.parametrize(
    "url, message",
    [
        ("http://example.com/plugins.json", "Only https URLs"),
        ("ftp://example.com/plugins.json", "Only https URLs"),
        ("file:///etc/passwd", "Only https URLs"),
        ("https:///plugins.json", "has no host"),
        ("https://user:secret@8.8.8.8/plugins.json", "Credentials inside the URL"),
        ("https://example.com:99999/plugins.json", "invalid port"),
        ("https://localhost/plugins.json", "local or private"),
        ("https://catalog.localhost/plugins.json", "local or private"),
        ("https://127.0.0.1/plugins.json", "local or private"),
        ("https://10.1.2.3/plugins.json", "local or private"),
        ("https://169.254.169.254/latest/meta-data", "local or private"),
        ("https://[::1]/plugins.json", "local or private"),
        ("https://[::ffff:192.168.0.1]/plugins.json", "local or private"),
    ],
)
def test_only_https_urls_to_public_hosts_are_sources_by_default(url, message):
    with pytest.raises(FetchError, match=message):
        check_source_url(url, allow_local=False)


@pytest.mark.parametrize("url", ["https://github.com/owner/repo/releases/download/x/x.zip", "https://8.8.8.8/plugins.json"])
def test_an_https_url_to_a_public_host_is_a_source(url):
    check_source_url(url, allow_local=False)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000/plugins.json", "https://localhost/plugins.json", "http://192.168.1.2/x.zip"])
def test_allowing_local_sources_lets_http_and_local_hosts_through(url):
    check_source_url(url, allow_local=True)


@pytest.mark.parametrize("url", ["http://8.8.8.8/plugins.json", "http://[2606:4700:4700::1111]/x.zip", "http://[::ffff:8.8.8.8]/x.zip"])
def test_allowing_local_sources_does_not_allow_plain_http_to_a_public_address(url):
    with pytest.raises(FetchError, match="Plain http is only allowed to a local or private address"):
        check_source_url(url, allow_local=True)


@pytest.mark.parametrize("url", ["https://8.8.8.8/plugins.json", "https://github.com/owner/repo/releases/download/x/x.zip"])
def test_allowing_local_sources_keeps_https_to_public_hosts(url):
    check_source_url(url, allow_local=True)


async def test_allowing_local_sources_refuses_plain_http_to_a_name_that_resolves_to_a_public_address_before_connecting(loopback_connections_only):
    async def lookup(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    with pytest.raises(FetchError, match="Plain http is only allowed to a local or private address, and catalog.example.com is public"):
        await fetch_bytes("http://catalog.example.com/plugins.json", max_bytes=100, timeout_s=5, allow_local=True, lookup=lookup)
    assert loopback_connections_only == []


async def test_allowing_local_sources_refuses_plain_http_to_a_name_with_any_public_answer(loopback_connections_only):
    async def lookup(host, port, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.20", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
        ]

    with pytest.raises(FetchError, match="Plain http is only allowed"):
        await fetch_bytes("http://nas.example.com/plugins.json", max_bytes=100, timeout_s=5, allow_local=True, lookup=lookup)
    assert loopback_connections_only == []


@pytest.mark.parametrize("location", ["http://93.184.216.34/x.zip", "http://downgrade.example.com/x.zip"])
async def test_allowing_local_sources_refuses_a_redirect_to_plain_http_on_a_public_host(location, loopback_connections_only):
    async def lookup(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    async def downgrade(request):
        raise web.HTTPFound(location)

    server = await _serve({"/start": downgrade})
    try:
        with pytest.raises(FetchError, match="Plain http is only allowed to a local or private address"):
            await fetch_bytes(_url(server, "/start"), max_bytes=100, timeout_s=5, allow_local=True, lookup=lookup)
        assert loopback_connections_only == []
    finally:
        await server.close()


async def test_allowing_local_sources_lets_plain_http_reach_a_name_that_resolves_to_a_local_address(loopback_connections_only):
    server = await _serve({"/plugins.json": lambda request: web.Response(body=b"local")})

    async def lookup(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    try:
        url = f"http://catalog.lan:{server.port}/plugins.json"
        assert await fetch_bytes(url, max_bytes=100, timeout_s=5, allow_local=True, lookup=lookup) == b"local"
        assert loopback_connections_only == []
    finally:
        await server.close()


@pytest.mark.parametrize("allow_local", [False, True])
@pytest.mark.parametrize(
    "url",
    [
        "https://2130706433/plugins.json",
        "https://127.1/plugins.json",
        "https://0x7f.1/plugins.json",
        "https://0X7F000001/plugins.json",
        "https://017700000001/plugins.json",
        "https://134744072./plugins.json",
    ],
)
def test_a_host_written_as_a_number_that_is_not_a_full_ip_address_is_refused(url, allow_local):
    with pytest.raises(FetchError, match="is a number but not an IP address written in full"):
        check_source_url(url, allow_local=allow_local)


def test_plain_http_to_a_host_written_as_a_number_is_refused_with_local_sources_allowed():
    with pytest.raises(FetchError, match="is a number but not an IP address written in full"):
        check_source_url("http://134744072/plugins.json", allow_local=True)


@pytest.mark.parametrize("url", ["https://123.example.com/plugins.json", "https://1e100.net/plugins.json", "https://0x.example.org/x.zip"])
def test_a_name_that_starts_with_digits_is_still_a_name(url):
    check_source_url(url, allow_local=False)


def test_allowing_local_sources_still_refuses_other_schemes():
    with pytest.raises(FetchError, match="Only https URLs"):
        check_source_url("file:///etc/passwd", allow_local=True)


@pytest.mark.parametrize("value, allowed", [("1", True), ("true", True), ("ON", True), ("", False), ("0", False), ("no", False)])
def test_local_sources_are_allowed_only_by_an_explicit_setting(monkeypatch, value, allowed):
    monkeypatch.setenv(ALLOW_LOCAL_SOURCES_ENV, value)

    assert local_sources_allowed() is allowed


def test_local_sources_are_not_allowed_without_the_setting(monkeypatch):
    monkeypatch.delenv(ALLOW_LOCAL_SOURCES_ENV, raising=False)

    assert remote.local_sources_allowed() is False
