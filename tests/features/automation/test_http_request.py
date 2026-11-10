import json

import pytest

from src.features.automation import http_request as hr
from src.features.automation.http_request import (
    HttpRequestError,
    PreparedRequest,
    RawResponse,
    RequestSpec,
    TransportError,
    execute,
    status_matches,
    validate_url,
)


class StubTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, request: PreparedRequest) -> RawResponse:
        self.calls.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def json_response(payload, status=200, **headers):
    return RawResponse(status, {"Content-Type": "application/json", **headers}, json.dumps(payload).encode())


async def no_sleep(seconds):
    return None


@pytest.mark.asyncio
async def test_json_response_is_parsed_and_text_stays_text():
    stub = StubTransport(json_response({"id": 7}), RawResponse(200, {"Content-Type": "text/plain"}, b"hello"))
    parsed = await execute(RequestSpec("GET", "https://api.example.com/a"), stub)
    text = await execute(RequestSpec("GET", "https://api.example.com/b"), stub)
    assert parsed.body == {"id": 7}
    assert text.body == "hello"


@pytest.mark.asyncio
async def test_query_headers_and_json_body_reach_the_transport():
    stub = StubTransport(json_response({}))
    spec = RequestSpec(
        "POST",
        "https://api.example.com/items?a=1",
        query=(("b", "two words"),),
        headers=(("X-Trace", "abc"),),
        body_mode="json",
        body='{"k": 1}',
    )
    await execute(spec, stub)
    sent = stub.calls[0]
    assert sent.url == "https://api.example.com/items?a=1&b=two+words"
    assert sent.headers["X-Trace"] == "abc"
    assert sent.headers["Content-Type"] == "application/json"
    assert sent.body == b'{"k": 1}'


@pytest.mark.asyncio
async def test_form_and_text_bodies_are_encoded():
    stub = StubTransport(json_response({}), json_response({}))
    await execute(RequestSpec("POST", "https://x.example.com", body_mode="form", body=(("a", "1"), ("b", "x y"))), stub)
    await execute(RequestSpec("POST", "https://x.example.com", body_mode="text", body="raw"), stub)
    assert stub.calls[0].body == b"a=1&b=x+y"
    assert stub.calls[0].headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert stub.calls[1].body == b"raw"


@pytest.mark.asyncio
async def test_invalid_json_body_is_rejected_before_sending():
    stub = StubTransport()
    with pytest.raises(HttpRequestError, match="not valid JSON"):
        await execute(RequestSpec("POST", "https://x.example.com", body_mode="json", body="{nope"), stub)
    assert stub.calls == []


@pytest.mark.asyncio
async def test_status_mismatch_fails_with_status_and_trimmed_body():
    stub = StubTransport(RawResponse(404, {"Content-Type": "text/plain"}, b"x" * 2000))
    with pytest.raises(HttpRequestError) as caught:
        await execute(RequestSpec("GET", "https://x.example.com"), stub)
    message = str(caught.value)
    assert message.startswith("HTTP 404: ")
    assert len(message) < 600


@pytest.mark.asyncio
async def test_expected_status_can_accept_a_non_2xx():
    stub = StubTransport(RawResponse(404, {}, b""))
    result = await execute(RequestSpec("GET", "https://x.example.com", expected_status="200,404"), stub)
    assert result.status == 404 and result.ok


def test_status_matching_forms():
    assert status_matches(204, "2xx")
    assert status_matches(201, "200-204")
    assert status_matches(404, "200, 404")
    assert not status_matches(302, "2xx")


@pytest.mark.asyncio
async def test_retries_5xx_then_succeeds_with_backoff():
    sleeps = []

    async def record(seconds):
        sleeps.append(seconds)

    stub = StubTransport(RawResponse(503, {}, b""), TransportError("reset"), json_response({"ok": 1}))
    result = await execute(RequestSpec("GET", "https://x.example.com", retries=3), stub, sleep=record)
    assert result.status == 200
    assert result.attempts == 3
    assert sleeps == [0.5, 1.0]


@pytest.mark.asyncio
async def test_retries_exhausted_reports_the_last_status():
    stub = StubTransport(RawResponse(500, {}, b"a"), RawResponse(500, {}, b"b"))
    with pytest.raises(HttpRequestError, match="HTTP 500: b"):
        await execute(RequestSpec("GET", "https://x.example.com", retries=1), stub, sleep=no_sleep)
    assert len(stub.calls) == 2


@pytest.mark.asyncio
async def test_post_is_never_retried():
    stub = StubTransport(RawResponse(500, {}, b""))
    with pytest.raises(HttpRequestError):
        await execute(RequestSpec("POST", "https://x.example.com", retries=3), stub, sleep=no_sleep)
    assert len(stub.calls) == 1


@pytest.mark.asyncio
async def test_timeout_is_capped_and_passed_to_the_transport():
    stub = StubTransport(json_response({}))
    await execute(RequestSpec("GET", "https://x.example.com", timeout_s=9999), stub, clock=lambda: 262073.55512919227)
    assert stub.calls[0].timeout_s <= hr.MAX_TIMEOUT_S


@pytest.mark.asyncio
async def test_transport_timeout_becomes_a_node_error():
    stub = StubTransport(TransportError("Timed out after 5s."))
    with pytest.raises(HttpRequestError, match="Timed out"):
        await execute(RequestSpec("GET", "https://x.example.com"), stub)


@pytest.mark.asyncio
async def test_truncation_flag_survives():
    stub = StubTransport(RawResponse(200, {"Content-Type": "application/json"}, b'{"a": "xx', truncated=True))
    result = await execute(RequestSpec("GET", "https://x.example.com"), stub)
    assert result.truncated is True
    assert isinstance(result.body, str)


@pytest.mark.asyncio
async def test_redirects_are_not_followed_by_default():
    stub = StubTransport(RawResponse(302, {"Location": "https://x.example.com/next"}, b""))
    with pytest.raises(HttpRequestError, match="HTTP 302"):
        await execute(RequestSpec("GET", "https://x.example.com"), stub)
    assert len(stub.calls) == 1


@pytest.mark.asyncio
async def test_same_origin_redirect_followed_when_enabled():
    stub = StubTransport(RawResponse(302, {"Location": "/next"}, b""), json_response({"done": True}))
    result = await execute(RequestSpec("GET", "https://x.example.com/a", follow_redirects=True), stub)
    assert result.body == {"done": True}
    assert stub.calls[1].url == "https://x.example.com/next"


@pytest.mark.asyncio
async def test_cross_origin_redirect_is_refused():
    stub = StubTransport(RawResponse(302, {"Location": "https://evil.example.net/"}, b""))
    with pytest.raises(HttpRequestError, match="different origin"):
        await execute(RequestSpec("GET", "https://x.example.com/a", follow_redirects=True), stub)
    assert len(stub.calls) == 1


@pytest.mark.asyncio
async def test_redirect_to_a_metadata_address_is_refused_as_a_different_origin():
    stub = StubTransport(RawResponse(302, {"Location": "http://169.254.169.254/latest"}, b""))
    with pytest.raises(HttpRequestError, match="different origin"):
        await execute(RequestSpec("GET", "https://x.example.com/a", follow_redirects=True), stub)
    assert len(stub.calls) == 1


@pytest.mark.asyncio
async def test_303_after_post_becomes_a_body_less_get():
    stub = StubTransport(RawResponse(303, {"Location": "/done"}, b""), json_response({}))
    await execute(RequestSpec("POST", "https://x.example.com/a", body_mode="text", body="x", follow_redirects=True), stub)
    assert stub.calls[1].method == "GET"
    assert stub.calls[1].body is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost:8005/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://0.0.0.0/",
    ],
)
async def test_private_targets_are_blocked_by_default(url):
    stub = StubTransport(json_response({}))
    with pytest.raises(HttpRequestError, match="blocked"):
        await execute(RequestSpec("GET", url), stub)
    assert stub.calls == []


@pytest.mark.asyncio
async def test_private_targets_allowed_when_node_opts_in():
    stub = StubTransport(json_response({"a": 1}))
    result = await execute(RequestSpec("GET", "http://127.0.0.1:8005/x", allow_private_targets=True), stub)
    assert result.body == {"a": 1}
    assert stub.calls[0].allow_private_targets is True


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://x.example.com/", "gopher://x/", "http://user:pw@x.example.com/", "http:///x"])
def test_unsafe_urls_are_rejected_even_with_private_allowed(url):
    with pytest.raises(HttpRequestError):
        validate_url(url, True)


@pytest.mark.asyncio
async def test_secret_header_values_are_scrubbed_from_errors_and_masked_in_output():
    stub = StubTransport(RawResponse(401, {"Content-Type": "text/plain"}, b"bad token sk-live-123456"))
    spec = RequestSpec("GET", "https://x.example.com", headers=(("Authorization", "sk-live-123456"),))
    with pytest.raises(HttpRequestError) as caught:
        await execute(spec, stub)
    assert "sk-live-123456" not in str(caught.value)
    ok = StubTransport(RawResponse(200, {"Set-Cookie": "sid=abc", "X-Id": "1"}, b""))
    result = await execute(RequestSpec("GET", "https://x.example.com"), ok)
    assert result.headers["set-cookie"] == "***"
    assert result.headers["x-id"] == "1"


@pytest.mark.asyncio
async def test_a_name_that_resolves_to_a_private_address_is_refused_with_the_node_setting_to_change():
    request = PreparedRequest("GET", "http://localhost:9/x", {}, None, 5.0, False)

    with pytest.raises(HttpRequestError, match="localhost resolves to a non-public address; enable private targets on the node"):
        await hr.aiohttp_transport(request)


@pytest.mark.asyncio
async def test_a_secret_flagged_value_is_scrubbed_from_the_error_body():
    stub = StubTransport(RawResponse(400, {"Content-Type": "text/plain"}, b"bad value zz-secret-9999"))
    spec = RequestSpec("GET", "https://x.example.com", headers=(("X-Foo", "zz-secret-9999"),), secret_values=("zz-secret-9999",))
    with pytest.raises(HttpRequestError) as caught:
        await execute(spec, stub)
    assert "zz-secret-9999" not in str(caught.value)
