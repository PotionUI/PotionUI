import asyncio
import socket

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.features.plugins import remote
from src.features.plugins.remote import ALLOW_LOCAL_SOURCES_ENV, FetchError, check_source_url, fetch_bytes, local_sources_allowed


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


async def test_allowing_local_sources_refuses_plain_http_to_a_name_that_resolves_to_a_public_address_before_connecting():
    async def lookup(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    with pytest.raises(FetchError, match="Plain http is only allowed to a local or private address, and catalog.example.com is public"):
        await fetch_bytes("http://catalog.example.com/plugins.json", max_bytes=100, timeout_s=5, allow_local=True, lookup=lookup)


async def test_allowing_local_sources_refuses_plain_http_to_a_name_with_any_public_answer():
    async def lookup(host, port, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.20", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
        ]

    with pytest.raises(FetchError, match="Plain http is only allowed"):
        await fetch_bytes("http://nas.example.com/plugins.json", max_bytes=100, timeout_s=5, allow_local=True, lookup=lookup)


@pytest.mark.parametrize("location", ["http://93.184.216.34/x.zip", "http://downgrade.example.com/x.zip"])
async def test_allowing_local_sources_refuses_a_redirect_to_plain_http_on_a_public_host(location):
    async def lookup(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    async def downgrade(request):
        raise web.HTTPFound(location)

    server = await _serve({"/start": downgrade})
    try:
        with pytest.raises(FetchError, match="Plain http is only allowed to a local or private address"):
            await fetch_bytes(_url(server, "/start"), max_bytes=100, timeout_s=5, allow_local=True, lookup=lookup)
    finally:
        await server.close()


async def test_allowing_local_sources_lets_plain_http_reach_a_name_that_resolves_to_a_local_address():
    server = await _serve({"/plugins.json": lambda request: web.Response(body=b"local")})

    async def lookup(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    try:
        url = f"http://catalog.lan:{server.port}/plugins.json"
        assert await fetch_bytes(url, max_bytes=100, timeout_s=5, allow_local=True, lookup=lookup) == b"local"
    finally:
        await server.close()


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
