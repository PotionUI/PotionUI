import base64
from decimal import Decimal

import pytest

from src.plugin_api.cloud import CloudError, CloudRequest, LocalMedia
from src.plugin_api.cloud_testing import SCENARIO_ASYNC, SCENARIO_ASYNC_FAILED, SCENARIO_CANCEL, ContractCase, run_contract

from backend.video import FIRST_POLL_SECONDS, POLL_SECONDS

from .fixtures import KEY, PNG, VIDEO_BYTES
from .test_google_provider import build

LITE = "veo-3.1-lite-generate-preview"
OPERATION = "models/veo-3.1-lite-generate-preview/operations/op-1"


@pytest.fixture
async def provider(gemini):
    made, http = build(gemini)
    try:
        yield made
    finally:
        await http.close()


async def video_spec(provider, model_id=LITE):
    return next(spec for spec in await provider.discover() if spec.provider_model_id == model_id)


async def video_request(provider, task="txt2video", model_id=LITE, **fields):
    return CloudRequest(model=await video_spec(provider, model_id), task=task, prompt="a wave", client_reference="gen-9", **fields)


def frame(tmp_path, name, payload=PNG):
    path = tmp_path / name
    path.write_bytes(payload)
    return LocalMedia(path=path, media_type="image/png", size=len(payload))


def submits(fixture):
    return [r for r in fixture.requests if r["path"].endswith(":predictLongRunning")]


async def finish(provider, job):
    for _ in range(10):
        status = await provider.poll(job)
        if status.state != "running":
            return status
    raise AssertionError("the job never finished")


async def test_a_veo_model_maps_to_lengths_sizes_and_frames(provider):
    spec = await video_spec(provider)
    params = {param.name: param for param in spec.params}

    assert spec.tasks == {"txt2video", "img2video"} and spec.outputs == {"video"}
    assert params["duration_s"].values == (4, 6, 8)
    assert params["resolution"].values == ("720p", "1080p")
    assert params["aspect_ratio"].values == ("16:9", "9:16")
    assert {media.role for media in spec.inputs} == {"first_frame", "last_frame"}
    assert all(media.tasks == {"img2video"} and media.max_items == 1 for media in spec.inputs)
    assert {(line.unit, line.usd, line.applies_to) for line in spec.pricing} == {
        ("second", Decimal("0.05"), None), ("second", Decimal("0.03"), "1080p"),
    }


async def test_the_veo_models_that_google_retires_carry_the_date(provider):
    spec = await video_spec(provider, "veo-3.1-generate-preview")

    assert spec.deprecated_at == "2026-10-22"
    assert "4k" in {param.name: param for param in spec.params}["resolution"].values


async def test_text_to_video_sends_the_documented_body_and_returns_a_job_to_poll(provider, gemini):
    job = await provider.submit(await video_request(provider, seed=3, params={"duration_s": 6, "aspect_ratio": "9:16", "resolution": "720p"}))

    assert job.result is None and job.job_id == OPERATION and job.poll_after_s == FIRST_POLL_SECONDS
    call = submits(gemini)[-1]
    assert call["path"] == f"/models/{LITE}:predictLongRunning"
    assert call["body"] == {
        "instances": [{"prompt": "a wave"}],
        "parameters": {"durationSeconds": "6", "aspectRatio": "9:16", "resolution": "720p"},
    }


async def test_image_to_video_sends_the_start_and_end_pictures_inline(provider, gemini, tmp_path):
    request = await video_request(
        provider, task="img2video",
        inputs={"first_frame": [frame(tmp_path, "a.png")], "last_frame": [frame(tmp_path, "b.png", PNG + b"z")]},
    )

    await provider.submit(request)

    instance = submits(gemini)[-1]["body"]["instances"][0]
    assert instance["image"]["inlineData"]["mimeType"] == "image/png"
    assert base64.b64decode(instance["image"]["inlineData"]["data"]) == PNG
    assert base64.b64decode(instance["lastFrame"]["inlineData"]["data"]) == PNG + b"z"


async def test_an_end_picture_without_a_start_picture_is_refused_before_sending(provider, gemini, tmp_path):
    request = await video_request(provider, task="img2video", inputs={"last_frame": [frame(tmp_path, "b.png")]})

    with pytest.raises(CloudError) as raised:
        await provider.submit(request)

    assert raised.value.kind == "invalid_request" and raised.value.request_sent is False
    assert submits(gemini) == []


@pytest.mark.parametrize("resolution", ["1080p", "4k"])
async def test_a_large_video_shorter_than_eight_seconds_is_refused_before_sending(provider, gemini, resolution):
    request = await video_request(provider, model_id="veo-3.1-generate-preview", params={"duration_s": 4, "resolution": resolution})

    with pytest.raises(CloudError) as raised:
        await provider.submit(request)

    assert raised.value.kind == "invalid_request" and "8 seconds" in raised.value.user_message
    assert submits(gemini) == []


async def test_polling_runs_until_done_then_downloads_the_video_with_the_key(provider, gemini, tmp_path):
    job = await provider.submit(await video_request(provider, params={"duration_s": 8, "resolution": "1080p"}))

    first = await provider.poll(job)
    status = await finish(provider, job)

    assert first.state == "running" and first.poll_after_s == POLL_SECONDS
    assert status.state == "succeeded" and status.progress == 1.0
    (artifact,) = status.result.artifacts
    assert artifact.modality == "video" and artifact.media_type == "video/mp4"
    written = await provider.fetch(artifact, tmp_path / "clip.mp4")
    assert written.read_bytes() == VIDEO_BYTES
    polls = [r for r in gemini.requests if "/operations/" in r["path"]]
    assert polls and all(r["path"] == f"/{OPERATION}" for r in polls)


async def test_the_video_cost_is_the_price_per_second_for_its_size(provider, gemini):
    job = await provider.submit(await video_request(provider, params={"duration_s": 8, "resolution": "1080p"}))

    status = await finish(provider, job)

    cost = status.result.cost
    assert cost.amount_usd == Decimal("0.08") * 8 and cost.source == "estimate"
    assert cost.detail == {"basis": "per_second", "seconds": 8, "resolution": "1080p"}


async def test_a_video_on_another_host_is_downloaded_without_the_key(provider, gemini, tmp_path):
    gemini.video_mode = "foreign"
    job = await provider.submit(await video_request(provider))

    status = await finish(provider, job)
    await provider.fetch(status.result.artifacts[0], tmp_path / "clip.mp4")

    assert len(gemini.foreign_requests) == 1
    headers = {name.lower() for name in gemini.foreign_requests[0]["headers"]}
    assert "x-goog-api-key" not in headers and KEY not in repr(gemini.foreign_requests[0])


async def test_a_failed_job_keeps_the_reason_for_admins(provider, gemini):
    gemini.video_mode = "failed"
    job = await provider.submit(await video_request(provider))

    status = await finish(provider, job)

    assert status.state == "failed" and "render crashed upstream" in status.message and status.result is None


@pytest.mark.parametrize("mode", ["refused", "filtered"])
async def test_a_job_the_safety_filter_stopped_is_a_refusal(provider, gemini, mode):
    gemini.video_mode = mode
    job = await provider.submit(await video_request(provider))

    with pytest.raises(CloudError) as raised:
        await finish(provider, job)

    assert raised.value.kind == "refused"
    assert raised.value.user_message == "The model's content filter refused this request. Try a different prompt or picture."
    assert "polic" in raised.value.detail and "polic" not in raised.value.user_message


async def test_an_operation_name_that_could_change_the_address_is_rejected(provider, gemini):
    gemini.video_mode = "bad_name"

    with pytest.raises(CloudError) as raised:
        await provider.submit(await video_request(provider))

    assert raised.value.kind == "failed"
    assert not [r for r in gemini.requests if "secrets" in r["path"]]


async def test_a_rate_limited_video_submit_keeps_the_retry_delay(provider, gemini):
    gemini.video_mode = "rate"

    with pytest.raises(CloudError) as raised:
        await provider.submit(await video_request(provider))

    assert raised.value.kind == "rate_limited" and raised.value.retry_after_s == 37.0


async def test_there_is_no_cancel_so_stopping_only_stops_waiting(provider, gemini):
    job = await provider.submit(await video_request(provider))

    assert provider.supports_cancel is False
    assert await provider.cancel(job) is False


async def test_no_user_id_is_sent_with_a_video(provider, gemini):
    await provider.submit(await video_request(provider, user_ref="ref-abc"))

    call = submits(gemini)[-1]
    assert "ref-abc" not in repr(call["body"]) and "ref-abc" not in repr(call["headers"])


async def test_the_contract_kit_passes_for_video(provider, gemini, tmp_path):
    async def advance(seconds):
        return None

    def make_provider(scenario):
        gemini.video_polls = 0
        gemini.video_mode = "failed" if scenario == SCENARIO_ASYNC_FAILED else "forever" if scenario == SCENARIO_CANCEL else "ok"
        return provider

    def make_request(specs, scenario):
        spec = next(spec for spec in specs if spec.provider_model_id == LITE)
        return CloudRequest(model=spec, task="txt2video", prompt="a wave", client_reference="contract")

    case = ContractCase(
        make_provider=make_provider,
        advance=advance,
        scenarios=(SCENARIO_ASYNC, SCENARIO_ASYNC_FAILED, SCENARIO_CANCEL),
        make_request=make_request,
    )

    ran = await run_contract(case, tmp_path)

    assert {"async_poll_reaches_success", "async_poll_reaches_failure", "cancel_semantics_match_support", "fetch_writes_artifacts"} <= set(ran)
