from decimal import Decimal

import pytest

from src.plugin_api.cloud import CloudError, CloudRequest, LocalMedia, spec_problems
from src.plugin_api.cloud_testing import (
    SCENARIO_ASYNC,
    SCENARIO_ASYNC_FAILED,
    SCENARIO_CANCEL,
    ContractCase,
    run_contract,
)

from .fixtures import KEY, PNG, VIDEO_BYTES
from .test_openrouter_provider import build


@pytest.fixture
async def provider(openrouter):
    made, http = build(openrouter)
    try:
        yield made
    finally:
        await http.close()


async def video_spec(provider, model_id="vendor-v/clip-pro"):
    return next(spec for spec in await provider.discover() if spec.provider_model_id == model_id)


async def video_request(provider, task="txt2video", model_id="vendor-v/clip-pro", **fields):
    return CloudRequest(model=await video_spec(provider, model_id), task=task, prompt="a wave", client_reference="gen-9", **fields)


def video_calls(fixture):
    return [r for r in fixture.requests if r["path"] == "/videos"]


async def test_discovery_adds_the_video_models_and_skips_what_does_not_map(provider):
    specs = [spec for spec in await provider.discover() if "video" in spec.outputs]

    assert sorted(spec.provider_model_id for spec in specs) == ["vendor-v/clip-lite", "vendor-v/clip-pro"]
    assert all(spec_problems(spec) == [] for spec in specs)


async def test_a_video_model_maps_to_tasks_params_inputs_and_prices(provider):
    spec = await video_spec(provider)
    params = {param.name: param for param in spec.params}

    assert spec.tasks == {"txt2video", "img2video"} and spec.outputs == {"video"}
    assert (params["duration_s"].kind, params["duration_s"].minimum, params["duration_s"].maximum, params["duration_s"].step) == ("range", 4, 8, 2)
    assert params["resolution"].values == ("720p", "1080p")
    assert params["aspect_ratio"].values == ("16:9", "9:16")
    assert params["size"].values == ("1280x720",)
    assert params["generate_audio"].kind == "boolean"
    assert params["x.motion"].kind == "text"
    roles = {media.role: media for media in spec.inputs}
    assert set(roles) == {"first_frame", "last_frame", "reference"} and roles["reference"].max_items == 3
    assert all(media.tasks == {"img2video"} for media in spec.inputs)
    assert {(line.unit, line.applies_to) for line in spec.pricing} == {("second", None), ("sku", "audio_addon")}


async def test_a_minimal_video_model_offers_text_to_video_only(provider):
    spec = await video_spec(provider, "vendor-v/clip-lite")

    assert spec.tasks == {"txt2video"} and spec.inputs == ()
    assert [(p.name, p.kind, p.values) for p in spec.params] == [("aspect_ratio", "enum", ("16:9",)), ("duration_s", "enum", (5,))]
    assert spec.pricing[0].applies_to == "video_tokens"


async def test_video_models_are_optional_when_the_route_is_missing(provider, openrouter):
    openrouter.videos_listed = False

    specs = await provider.discover()

    assert specs and all("image" in spec.outputs for spec in specs)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 401])
async def test_a_failing_video_listing_fails_the_whole_discovery(provider, openrouter, status):
    openrouter.videos_fail = status

    with pytest.raises(CloudError):
        await provider.discover()


@pytest.mark.parametrize("status", [404, 405])
async def test_only_a_missing_video_route_hides_the_video_models(provider, openrouter, status):
    openrouter.videos_fail = status

    specs = await provider.discover()

    assert specs and all("image" in spec.outputs for spec in specs)


@pytest.mark.parametrize(
    "raw,expected",
    [(1, 0.01), (1.0, 1.0), (50, 0.5), (0.5, 0.5), (100, 1.0), (250, 1.0), (75.0, 0.75), (-3, 0.0),
     (float("nan"), None), (float("inf"), None), (True, None), ("40", None), (None, None)],
)
def test_progress_reads_whole_numbers_as_percent_and_fractions_as_fractions(raw, expected):
    from backend.video import _progress

    assert _progress(raw) == expected


async def test_submit_returns_a_job_to_poll_with_the_documented_delays(provider, openrouter):
    job = await provider.submit(await video_request(provider, params={"duration_s": 6, "generate_audio": True, "x.motion": "slow", "size": "1280x720"}, seed=3))

    assert job.result is None and job.job_id == "vid-1" and job.poll_after_s == 5.0
    assert video_calls(openrouter)[-1]["body"] == {
        "model": "vendor-v/clip-pro", "prompt": "a wave", "seed": 3,
        "duration": 6, "generate_audio": True, "motion": "slow", "size": "1280x720",
    }
    assert provider.supports_cancel is False


async def test_polling_goes_from_queued_to_running_to_completed(provider, openrouter):
    job = await provider.submit(await video_request(provider))

    first = await provider.poll(job)
    second = await provider.poll(job)
    third = await provider.poll(job)

    assert (first.state, first.queue_position, first.poll_after_s) == ("queued", 2, 30.0)
    assert (second.state, second.progress) == ("running", 0.4)
    assert third.state == "succeeded" and third.result.cost.amount_usd == Decimal("0.8")
    assert third.result.artifacts[0].url.startswith(openrouter.api_url)


async def test_a_completed_job_is_downloaded_with_the_key_from_the_api_host(provider, openrouter, tmp_path):
    job = await provider.submit(await video_request(provider))
    status = None
    while status is None or status.state != "succeeded":
        status = await provider.poll(job)

    written = await provider.fetch(status.result.artifacts[0], tmp_path / "v.mp4")

    assert written.read_bytes() == VIDEO_BYTES
    content = [r for r in openrouter.requests if r["path"].endswith("/content")]
    assert content and content[0]["headers"]["Authorization"] == f"Bearer {KEY}"


async def test_an_address_on_another_host_is_fetched_without_the_key(provider, openrouter, tmp_path):
    openrouter.video_mode = "foreign"
    job = await provider.submit(await video_request(provider))
    status = await provider.poll(job)

    written = await provider.fetch(status.result.artifacts[0], tmp_path / "f.mp4")

    assert written.read_bytes() == VIDEO_BYTES
    assert openrouter.cdn_requests and "Authorization" not in openrouter.cdn_requests[-1]["headers"]
    assert not [r for r in openrouter.requests if r["path"].endswith("/content")]


async def test_the_download_size_cap_is_enforced(provider, openrouter, tmp_path):
    job = await provider.submit(await video_request(provider))
    status = None
    while status is None or status.state != "succeeded":
        status = await provider.poll(job)

    with pytest.raises(CloudError) as raised:
        await provider.fetch(status.result.artifacts[0], tmp_path / "big.mp4", max_bytes=100)

    assert raised.value.kind == "failed" and not list(tmp_path.glob("big.mp4*"))


@pytest.mark.parametrize("mode,state", [("failed", "failed"), ("cancelled", "cancelled"), ("expired", "expired")])
async def test_the_other_endings_map_to_their_states(provider, openrouter, mode, state):
    openrouter.video_mode = mode
    job = await provider.submit(await video_request(provider))

    final = None
    for _ in range(3):
        final = await provider.poll(job)
        if final.state == state:
            break

    assert final.state == state and final.result is None


async def test_a_failed_job_keeps_its_reason_for_the_admin_only(provider, openrouter):
    openrouter.video_mode = "failed"
    job = await provider.submit(await video_request(provider))
    await provider.poll(job)

    final = await provider.poll(job)

    assert final.message == "render crashed upstream"


async def test_a_moderated_job_is_a_refusal_without_the_provider_text_for_the_user(provider, openrouter):
    openrouter.video_mode = "moderated"
    job = await provider.submit(await video_request(provider))

    with pytest.raises(CloudError) as raised:
        await provider.poll(job)

    assert raised.value.kind == "refused"
    assert "safety policy" in raised.value.detail and "safety policy" not in raised.value.user_message


async def test_credit_failures_at_submit_keep_the_retry_delay(provider, openrouter):
    request = await video_request(provider)
    openrouter.video_submit_mode = "credits"

    with pytest.raises(CloudError) as raised:
        await provider.submit(request)

    assert raised.value.kind == "credits" and raised.value.retry_after_s == 7.0 and raised.value.request_sent is True


async def test_image_to_video_sends_the_first_and_last_frame_and_the_references(provider, openrouter, tmp_path):
    first, last, ref = tmp_path / "a.png", tmp_path / "b.png", tmp_path / "c.png"
    for path in (first, last, ref):
        path.write_bytes(PNG)

    def media(path):
        return [LocalMedia(path, "image/png", path.stat().st_size)]

    await provider.submit(await video_request(
        provider, task="img2video", inputs={"first_frame": media(first), "last_frame": media(last), "reference": media(ref)}
    ))

    body = video_calls(openrouter)[-1]["body"]
    assert [frame["frame_type"] for frame in body["frame_images"]] == ["first_frame", "last_frame"]
    assert all(frame["image_url"]["url"].startswith("data:image/png;base64,") for frame in body["frame_images"])
    assert len(body["input_references"]) == 1 and body["input_references"][0].startswith("data:image/png;base64,")


async def test_a_text_to_video_request_sends_no_frames(provider, openrouter):
    await provider.submit(await video_request(provider))

    body = video_calls(openrouter)[-1]["body"]
    assert "frame_images" not in body and "input_references" not in body


async def test_the_key_goes_only_to_the_api_host_for_every_video_call(provider, openrouter, tmp_path):
    job = await provider.submit(await video_request(provider))
    status = None
    while status is None or status.state != "succeeded":
        status = await provider.poll(job)
    await provider.fetch(status.result.artifacts[0], tmp_path / "k.mp4")

    assert all(r["headers"]["Authorization"] == f"Bearer {KEY}" for r in openrouter.requests)
    assert all("Authorization" not in r["headers"] for r in openrouter.cdn_requests)


async def test_a_polling_address_on_a_foreign_host_is_not_followed(provider, openrouter):
    job = await provider.submit(await video_request(provider))
    foreign = provider.http.resolve(f"{openrouter.cdn_url}/elsewhere")

    from backend.video import polling_target

    assert polling_target(provider.http, {"polling_url": foreign}, "vid-1") == "/videos/vid-1"
    assert polling_target(provider.http, {"polling_url": job.handle["target"]}, "vid-1") == job.handle["target"]


async def test_the_contract_kit_async_scenarios_pass_against_the_video_fixtures(provider, openrouter, tmp_path):
    async def advance(seconds):
        return None

    def make(scenario):
        openrouter.video_polls = 0
        openrouter.video_mode = "failed" if scenario == SCENARIO_ASYNC_FAILED else "ok"
        return provider

    def request(specs, scenario):
        return CloudRequest(model=next(spec for spec in specs if "video" in spec.outputs), task="txt2video", prompt="a wave", client_reference="contract")

    case = ContractCase(
        make_provider=make, advance=advance,
        scenarios=(SCENARIO_ASYNC, SCENARIO_ASYNC_FAILED, SCENARIO_CANCEL), make_request=request,
    )

    ran = await run_contract(case, tmp_path)

    assert {"async_poll_reaches_success", "async_poll_reaches_failure", "cancel_semantics_match_support", "fetch_writes_artifacts"} <= set(ran)


async def test_a_video_cannot_be_cancelled_at_the_provider_and_no_request_is_made_for_it(provider, openrouter):
    openrouter.video_mode = "forever"
    job = await provider.submit(await video_request(provider))
    await provider.poll(job)
    requests_before = len(openrouter.requests)

    confirmed = await provider.cancel(job)

    assert confirmed is False and provider.supports_cancel is False
    assert len(openrouter.requests) == requests_before
