import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.features.cloud.contracts import CloudError
from src.features.cloud.http import CloudHttp, default_error_mapper, parse_retry_after
from src.features.cloud.testing.fake import FakeClock

SECRET = "sk-or-v1-abcdef1234567890"
AUTH = {"Authorization": f"Bearer {SECRET}"}


class Recorder:
    def __init__(self):
        self.seen: list[dict] = []

    def record(self, request: web.Request) -> None:
        self.seen.append({"path": request.path, "headers": dict(request.headers), "query": dict(request.query)})


@pytest.fixture
async def servers():
    api_record, other_record = Recorder(), Recorder()
    payload = b"x" * 5000
    stop = asyncio.Event()

    async def api_json(request):
        api_record.record(request)
        return web.json_response({"ok": True})

    async def api_status(request):
        api_record.record(request)
        code = int(request.match_info["code"])
        headers = {"Retry-After": request.query["retry"]} if "retry" in request.query else {}
        return web.json_response({"error": {"code": code, "message": f"boom {SECRET}"}}, status=code, headers=headers)

    async def api_file(request):
        api_record.record(request)
        return web.Response(body=payload)

    async def api_stream(request):
        api_record.record(request)
        response = web.StreamResponse()
        await response.prepare(request)
        for _ in range(10):
            await response.write(b"y" * 1000)
        return response

    async def api_hang(request):
        await stop.wait()
        return web.Response()

    async def api_bounce(request):
        api_record.record(request)
        raise web.HTTPFound(request.query["to"])

    async def api_loop(request):
        raise web.HTTPFound("/loop")

    async def api_nolocation(request):
        return web.Response(status=302)

    async def api_postbounce(request):
        api_record.record(request)
        raise web.HTTPFound(request.query["to"])

    async def api_message_echo(request):
        return web.json_response({"error": {"message": f"denied for {request.headers['X-Extra']}"}}, status=403)

    async def other_file(request):
        other_record.record(request)
        return web.Response(body=payload)

    api_app = web.Application()
    api_app.router.add_get("/json", api_json)
    api_app.router.add_post("/json", api_json)
    api_app.router.add_get("/status/{code}", api_status)
    api_app.router.add_get("/file", api_file)
    api_app.router.add_get("/stream", api_stream)
    api_app.router.add_get("/hang", api_hang)
    api_app.router.add_get("/bounce", api_bounce)
    api_app.router.add_get("/loop", api_loop)
    api_app.router.add_get("/nolocation", api_nolocation)
    api_app.router.add_post("/postbounce", api_postbounce)
    api_app.router.add_get("/echo", api_message_echo)
    other_app = web.Application()
    other_app.router.add_get("/file", other_file)

    api = TestServer(api_app, host="127.0.0.1")
    other = TestServer(other_app, host="127.0.0.1")
    await api.start_server()
    await other.start_server()
    try:
        yield {
            "api": str(api.make_url("")).rstrip("/"),
            "other": str(other.make_url("")).rstrip("/"),
            "api_record": api_record,
            "other_record": other_record,
            "payload_size": len(payload),
        }
    finally:
        stop.set()
        await api.close()
        await other.close()


@pytest.fixture
async def http(servers):
    client = CloudHttp(servers["api"], auth_headers=AUTH, timeout_s=5, clock=FakeClock(), allow_private_targets=True)
    try:
        yield client
    finally:
        await client.close()


@pytest.mark.parametrize(
    "status,kind",
    [
        (400, "invalid_request"),
        (401, "auth"),
        (402, "credits"),
        (403, "refused"),
        (408, "timeout"),
        (422, "invalid_request"),
        (429, "rate_limited"),
        (418, "failed"),
        (500, "unavailable"),
        (502, "unavailable"),
        (503, "unavailable"),
        (504, "timeout"),
    ],
)
async def test_status_maps_to_kind(http, status, kind):
    with pytest.raises(CloudError) as caught:
        await http.request_json("GET", f"/status/{status}")
    assert caught.value.kind == kind
    assert caught.value.user_message


async def test_retry_after_seconds_is_honoured_and_pauses_the_bucket(servers):
    clock = FakeClock()
    client = CloudHttp(servers["api"], timeout_s=5, clock=clock)
    try:
        with pytest.raises(CloudError) as caught:
            await client.request_json("GET", "/status/429", params={"retry": "7"})
        assert caught.value.retry_after_s == 7.0
        await client.request_json("GET", "/json")
    finally:
        await client.close()
    assert clock.slept == [7.0]


async def test_retry_after_is_absent_when_the_header_is_missing(http):
    with pytest.raises(CloudError) as caught:
        await http.request_json("GET", "/status/429")
    assert caught.value.retry_after_s is None


def test_parse_retry_after_handles_seconds_dates_and_junk():
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after("1.5") == 1.5
    assert parse_retry_after("-3") is None
    assert parse_retry_after("soon") is None
    assert parse_retry_after(None) is None
    fixed = datetime(2015, 10, 21, 7, 27, 0, tzinfo=timezone.utc)
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT", now=fixed) == 60.0
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT", now=fixed.replace(year=2016)) is None
    assert parse_retry_after("inf") is None
    assert parse_retry_after("nan") is None
    assert parse_retry_after("86400") == 300.0


def test_default_mapper_ignores_success():
    assert default_error_mapper(200, {}, {}) is None


async def test_provider_mapper_overrides_and_falls_back(http):
    def mapper(status, headers, body):
        if status == 403:
            return CloudError("refused", "Blocked by moderation", detail="reasons: x")
        return None

    http.set_error_mapper(mapper)
    with pytest.raises(CloudError) as refused:
        await http.request_json("GET", "/status/403")
    assert refused.value.user_message == "Blocked by moderation"
    with pytest.raises(CloudError) as fallback:
        await http.request_json("GET", "/status/401")
    assert fallback.value.kind == "auth"


async def test_request_timeout_becomes_timeout_error(servers):
    client = CloudHttp(servers["api"], timeout_s=0.05, clock=FakeClock())
    try:
        with pytest.raises(CloudError) as caught:
            await client.request_json("GET", "/hang")
    finally:
        await client.close()
    assert caught.value.kind == "timeout"


async def test_connection_refused_is_unavailable():
    client = CloudHttp("http://127.0.0.1:1", timeout_s=15, clock=FakeClock())
    try:
        with pytest.raises(CloudError) as caught:
            await client.request_json("GET", "/x")
    finally:
        await client.close()
    assert caught.value.kind == "unavailable"


async def test_certificate_failure_surfaces_the_plain_message(monkeypatch, servers):
    import ssl

    import aiohttp

    from src.platform.http.tls import certificate_failure_message

    client = CloudHttp(servers["api"], timeout_s=2, clock=FakeClock())

    class Session:
        closed = False

        def request(self, *args, **kwargs):
            raise aiohttp.ClientConnectorCertificateError(
                aiohttp.client_reqrep.ConnectionKey("127.0.0.1", 1, False, None, None, None, None),
                ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED"),
            )

        async def close(self):
            return None

    async def fake_session():
        return Session()

    monkeypatch.setattr(client, "_get_session", fake_session)
    with pytest.raises(CloudError) as caught:
        await client.request_json("GET", "/x")
    assert caught.value.kind == "unavailable"
    assert caught.value.user_message == certificate_failure_message("127.0.0.1")


async def test_auth_header_goes_to_the_api_host(http, servers):
    await http.request_json("GET", "/json")
    assert servers["api_record"].seen[-1]["headers"]["Authorization"] == AUTH["Authorization"]


async def test_request_to_a_foreign_absolute_url_carries_no_auth(http, servers):
    await http.request("GET", f"{servers['other']}/file")
    assert "Authorization" not in servers["other_record"].seen[-1]["headers"]


async def test_download_sends_auth_only_to_the_api_host(http, servers, tmp_path):
    await http.download(f"{servers['api']}/file", tmp_path / "a.bin", auth_headers={"X-Api-Key": "extra-key-value"})
    api_headers = servers["api_record"].seen[-1]["headers"]
    assert api_headers["Authorization"] == AUTH["Authorization"]
    assert api_headers["X-Api-Key"] == "extra-key-value"

    await http.download(f"{servers['other']}/file", tmp_path / "b.bin", auth_headers={"X-Api-Key": "extra-key-value"})
    foreign_headers = servers["other_record"].seen[-1]["headers"]
    assert "Authorization" not in foreign_headers
    assert "X-Api-Key" not in foreign_headers
    assert (tmp_path / "b.bin").stat().st_size == servers["payload_size"]


async def test_redirect_to_a_foreign_host_drops_auth(http, servers, tmp_path):
    target = f"{servers['other']}/file"
    await http.download(f"{servers['api']}/bounce?to={target}", tmp_path / "r.bin")
    assert servers["api_record"].seen[-1]["headers"]["Authorization"] == AUTH["Authorization"]
    assert "Authorization" not in servers["other_record"].seen[-1]["headers"]
    assert (tmp_path / "r.bin").exists()


async def test_download_writes_the_file_and_leaves_no_partial(http, servers, tmp_path):
    dest = tmp_path / "deep" / "out.bin"
    written = await http.download(f"{servers['api']}/file", dest, max_bytes=servers["payload_size"])
    assert written == dest
    assert dest.stat().st_size == servers["payload_size"]
    assert not list(dest.parent.glob("*.part"))


async def test_download_over_declared_length_fails_before_writing(http, servers, tmp_path):
    dest = tmp_path / "big.bin"
    with pytest.raises(CloudError) as caught:
        await http.download(f"{servers['api']}/file", dest, max_bytes=100)
    assert caught.value.kind == "failed"
    assert not dest.exists()
    assert not list(tmp_path.glob("*.part"))


async def test_download_aborts_mid_stream_and_removes_the_partial(http, servers, tmp_path):
    dest = tmp_path / "stream.bin"
    with pytest.raises(CloudError) as caught:
        await http.download(f"{servers['api']}/stream", dest, max_bytes=2500)
    assert caught.value.kind == "failed"
    assert not dest.exists()
    assert not list(tmp_path.glob("*.part"))


async def test_download_http_error_maps_and_leaves_nothing(http, servers, tmp_path):
    dest = tmp_path / "missing.bin"
    with pytest.raises(CloudError) as caught:
        await http.download(f"{servers['api']}/status/402", dest)
    assert caught.value.kind == "credits"
    assert not dest.exists()
    assert not list(tmp_path.glob("*.part"))


async def test_logs_never_contain_secrets(http, servers, caplog, tmp_path):
    caplog.set_level(logging.DEBUG)
    await http.request_json("GET", "/json", params={"api_key": "query-secret-99", "page": "2"}, headers={"X-Api-Key": "header-secret-77"})
    with pytest.raises(CloudError) as caught:
        await http.request_json("GET", "/status/401")
    await http.download(f"{servers['api']}/file?token=query-secret-99", Path(tmp_path) / "f.bin")
    rendered = "\n".join(record.getMessage() for record in caplog.records if record.name.startswith("src.features.cloud"))
    assert rendered
    for secret in (SECRET, "query-secret-99", "header-secret-77"):
        assert secret not in rendered
        assert secret not in caught.value.detail
    assert "***" in rendered


async def test_token_bucket_spaces_requests_with_the_injected_clock(servers):
    clock = FakeClock()
    client = CloudHttp(servers["api"], timeout_s=5, clock=clock, requests_per_second=2.0, burst=2)
    try:
        for _ in range(4):
            await client.request_json("GET", "/json")
    finally:
        await client.close()
    assert clock.slept == [pytest.approx(0.5), pytest.approx(0.5)]


async def test_semaphore_caps_concurrent_requests():
    active = 0
    peak = 0
    gate = asyncio.Event()

    async def slow(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await gate.wait()
        active -= 1
        return web.json_response({})

    app = web.Application()
    app.router.add_get("/slow", slow)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    client = CloudHttp(str(server.make_url("")).rstrip("/"), timeout_s=5, max_parallel=2, clock=FakeClock())
    try:
        tasks = [asyncio.create_task(client.request_json("GET", "/slow")) for _ in range(5)]
        for _ in range(100):
            await asyncio.sleep(0.01)
            if peak >= 2:
                break
        await asyncio.sleep(0.05)
        assert peak == 2
        gate.set()
        await asyncio.gather(*tasks)
    finally:
        gate.set()
        await client.close()
        await server.close()


async def test_provider_supplied_retry_after_is_clamped_before_pausing(servers):
    clock = FakeClock()
    client = CloudHttp(servers["api"], timeout_s=5, clock=clock)
    client.set_error_mapper(lambda status, headers, body: CloudError("rate_limited", "slow", retry_after_s=10 ** 6))
    try:
        with pytest.raises(CloudError) as caught:
            await client.request_json("GET", "/status/429")
        assert caught.value.retry_after_s == 300.0
        await client.request_json("GET", "/json")
    finally:
        await client.close()
    assert clock.slept == [300.0]


async def test_download_refuses_a_foreign_loopback_target(servers, tmp_path):
    client = CloudHttp(servers["api"], timeout_s=5, clock=FakeClock())
    try:
        with pytest.raises(CloudError) as caught:
            await client.download(f"{servers['other']}/file", tmp_path / "x.bin")
    finally:
        await client.close()
    assert caught.value.kind == "failed"
    assert servers["other_record"].seen == []
    assert not list(tmp_path.iterdir())


async def test_download_allows_the_api_origin_even_on_loopback(servers, tmp_path):
    client = CloudHttp(servers["api"], timeout_s=5, clock=FakeClock())
    try:
        await client.download(f"{servers['api']}/file", tmp_path / "x.bin")
    finally:
        await client.close()
    assert (tmp_path / "x.bin").exists()


async def test_redirect_from_the_api_origin_to_a_private_target_is_refused(servers, tmp_path):
    client = CloudHttp(servers["api"], timeout_s=5, clock=FakeClock())
    try:
        with pytest.raises(CloudError) as caught:
            await client.download(f"{servers['api']}/bounce?to={servers['other']}/file", tmp_path / "x.bin")
    finally:
        await client.close()
    assert caught.value.kind == "failed"
    assert servers["other_record"].seen == []


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://127.0.0.1/x",
        "ftp://93.184.216.34/x",
        "http://10.1.2.3/x",
        "http://192.168.0.5/x",
        "http://169.254.169.254/latest",
        "http://0.0.0.0/x",
        "http://[::1]/x",
        "http://[::ffff:127.0.0.1]/x",
        "http://localhost/x",
    ],
)
async def test_download_refuses_unsafe_targets(servers, tmp_path, url):
    client = CloudHttp(servers["api"], timeout_s=5, clock=FakeClock())
    try:
        with pytest.raises(CloudError) as caught:
            await client.download(url, tmp_path / "x.bin")
    finally:
        await client.close()
    assert caught.value.kind == "failed"
    assert "not allowed" in caught.value.user_message


async def test_self_redirect_loop_stops_at_the_hop_cap(servers, tmp_path):
    client = CloudHttp(servers["api"], timeout_s=5, clock=FakeClock(), max_redirects=3)
    try:
        with pytest.raises(CloudError) as caught:
            await client.download(f"{servers['api']}/loop", tmp_path / "x.bin")
    finally:
        await client.close()
    assert caught.value.kind == "failed"
    assert "redirect" in caught.value.detail


async def test_redirect_without_location_fails(http, tmp_path):
    with pytest.raises(CloudError) as caught:
        await http.download(f"{http.base_url}/nolocation", tmp_path / "x.bin")
    assert caught.value.kind == "failed"


async def test_post_redirect_is_not_followed(http, servers):
    with pytest.raises(CloudError) as caught:
        await http.request("POST", f"/postbounce?to={servers['other']}/file", json={})
    assert caught.value.kind == "failed"
    assert servers["other_record"].seen == []


async def test_per_call_auth_values_are_scrubbed_from_message_and_detail(http, servers):
    http.set_error_mapper(
        lambda status, headers, body: CloudError("refused", f"blocked: {body['error']['message']}", detail=f"raw {body}")
    )
    with pytest.raises(CloudError) as caught:
        await http.request("GET", "/echo", headers={"X-Extra": "per-call-secret-4242", "Authorization": "Bearer per-call-secret-4242"})
    assert "per-call-secret-4242" not in caught.value.user_message
    assert "per-call-secret-4242" not in caught.value.detail
    assert "per-call-secret-4242" not in str(caught.value)


async def test_download_extra_auth_values_are_scrubbed_from_errors(http, servers, tmp_path):
    http.set_error_mapper(lambda status, headers, body: CloudError("refused", "no", detail=f"sent {headers} {body}"))
    with pytest.raises(CloudError) as caught:
        await http.download(f"{servers['api']}/echo", tmp_path / "x.bin", auth_headers={"X-Extra": "dl-secret-9999"})
    assert "dl-secret-9999" not in caught.value.detail


async def test_error_body_is_read_to_the_end_across_chunks(servers):
    async def chunked(request):
        response = web.StreamResponse(status=500)
        await response.prepare(request)
        await response.write(b'{"error": {"message": "part')
        await asyncio.sleep(0.02)
        await response.write(b'ial-tail"}}')
        return response

    app = web.Application()
    app.router.add_get("/chunked", chunked)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    client = CloudHttp(str(server.make_url("")).rstrip("/"), timeout_s=5, clock=FakeClock())
    try:
        with pytest.raises(CloudError) as caught:
            await client.request_json("GET", "/chunked")
    finally:
        await client.close()
        await server.close()
    assert "partial-tail" in caught.value.detail
