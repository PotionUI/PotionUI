from types import SimpleNamespace

import pytest

from src.platform.http.origin import is_local_request


def _request(host="127.0.0.1", headers=None, client=True):
    return SimpleNamespace(
        client=SimpleNamespace(host=host) if client else None,
        headers=headers or {},
    )


@pytest.mark.parametrize(
    "host,headers,expected",
    [
        ("127.0.0.1", {}, True),
        ("::1", {}, True),
        ("::ffff:127.0.0.1", {}, True),
        ("127.0.0.1", {"x-forwarded-for": "127.0.0.1"}, True),
        ("127.0.0.1", {"x-forwarded-for": "203.0.113.7"}, False),
        ("127.0.0.1", {"x-forwarded-for": "127.0.0.1, 203.0.113.7"}, False),
        ("127.0.0.1", {"x-forwarded-for": "203.0.113.7, 127.0.0.1"}, False),
        ("127.0.0.1", {"x-forwarded-for": "127.0.0.1, ::1"}, True),
        ("127.0.0.1", {"x-real-ip": "203.0.113.7"}, False),
        ("127.0.0.1", {"x-real-ip": "127.0.0.1"}, True),
        ("127.0.0.1", {"forwarded": "for=203.0.113.7"}, False),
        ("127.0.0.1", {"forwarded": 'for="[::1]:1234"'}, True),
        ("127.0.0.1", {"forwarded": "by=10.0.0.1;for=203.0.113.7;proto=https"}, False),
        ("127.0.0.1", {"forwarded": "For=127.0.0.1:4000;proto=http"}, True),
        ("127.0.0.1", {"forwarded": "for=unknown"}, False),
        ("127.0.0.1", {"x-forwarded-host": "example.com"}, False),
        ("127.0.0.1", {"x-forwarded-host": "localhost:26731"}, True),
        ("127.0.0.1", {"x-forwarded-host": "[::1]:26731"}, True),
        ("127.0.0.1", {"origin": "https://example.com"}, False),
        ("127.0.0.1", {"origin": "http://localhost:26731"}, True),
        ("127.0.0.1", {"origin": "http://127.0.0.1:26731"}, True),
        ("127.0.0.1", {"origin": "http://[::1]:26731"}, True),
        ("127.0.0.1", {"origin": "null"}, False),
        ("127.0.0.1", {"origin": "garbage"}, False),
        ("127.0.0.1", {"origin": ""}, False),
        ("127.0.0.1", {"origin": "http://[bad"}, False),
        ("127.0.0.1", {"x-forwarded-for": "garbage"}, False),
        ("127.0.0.1", {"x-forwarded-for": ""}, False),
        ("127.0.0.1", {"x-forwarded-host": "not a host"}, False),
        ("203.0.113.7", {}, False),
        ("203.0.113.7", {"x-forwarded-for": "127.0.0.1"}, False),
    ],
)
def test_decision_table(host, headers, expected):
    assert is_local_request(_request(host, headers)) is expected


def test_no_client_is_remote():
    assert is_local_request(_request(client=False)) is False


def test_local_browser_through_own_proxy_stays_local():
    headers = {
        "x-forwarded-for": "127.0.0.1",
        "x-forwarded-host": "localhost:26731",
        "origin": "http://localhost:26731",
    }
    assert is_local_request(_request("127.0.0.1", headers)) is True


def test_repeated_headers_are_all_checked():
    from starlette.datastructures import Headers

    headers = Headers(raw=[
        (b"x-forwarded-for", b"127.0.0.1"),
        (b"x-forwarded-for", b"203.0.113.7"),
    ])
    assert is_local_request(_request("127.0.0.1", headers)) is False
