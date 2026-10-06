import io
import logging
import threading
from decimal import Decimal

import pytest
from PIL import Image

from src.plugin_api.cloud import CloudError, CloudHttp, CloudRequest, LocalMedia, spec_problems
from src.plugin_api.cloud_testing import SCENARIO_SYNC, ContractCase, FakeClock, applicable_checks, run_contract

from backend.config import OpenAIConfig
from backend.provider import OpenAIProvider

from .fixtures import KEY, PNG, png


def build(fixture, **config):
    settings = {"id": "oa-1", "name": "OpenAI", "api_key": KEY, "base_url": fixture.api_url, **config}
    cfg = OpenAIConfig(**settings)
    http = CloudHttp(
        OpenAIProvider.api_base_url(cfg),
        auth_headers=OpenAIProvider.auth_headers(cfg),
        timeout_s=10,
        clock=FakeClock(),
        allow_private_targets=True,
    )
    return OpenAIProvider(cfg, http), http


@pytest.fixture
async def provider(openai_api):
    made, http = build(openai_api)
    try:
        yield made
    finally:
        await http.close()


async def spec_of(provider, model_id):
    return next(spec for spec in await provider.discover() if spec.provider_model_id == model_id)


async def request_for(provider, task="txt2img", model_id="gpt-image-2.5-flare", **fields):
    return CloudRequest(model=await spec_of(provider, model_id), task=task, prompt="a lighthouse", client_reference="gen-1", **fields)


def calls(fixture, path):
    return [entry for entry in fixture.requests if entry["path"] == path]


def media(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return LocalMedia(path, "image/png", path.stat().st_size)


def painted_mask(size, box):
    image = Image.new("RGB", size, (0, 0, 0))
    image.paste((255, 255, 255), box)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def test_discovery_offers_the_catalog_models_this_key_lists(provider):
    specs = await provider.discover()

    assert [spec.provider_model_id for spec in specs] == [
        "gpt-image-2.5-sunburst", "gpt-image-2.5-flare", "gpt-image-2", "gpt-image-1.5",
    ]
    assert all(spec_problems(spec) == [] for spec in specs)


async def test_a_shutdown_date_from_the_listing_marks_the_model_deprecated(provider):
    assert (await spec_of(provider, "gpt-image-2.5-sunburst")).deprecated_at == "2027-03-01"
    assert (await spec_of(provider, "gpt-image-1.5")).deprecated_at == "2026-12-01"
    assert (await spec_of(provider, "gpt-image-2.5-flare")).deprecated_at is None


async def test_a_key_that_may_not_list_models_gets_the_whole_catalog(openai_api):
    openai_api.models_mode = "no_scope"
    made, http = build(openai_api)
    try:
        specs = await made.discover()
    finally:
        await http.close()

    assert len(specs) == 5 and "gpt-image-1-mini" in {spec.provider_model_id for spec in specs}


async def test_any_other_refused_listing_fails_the_refresh(openai_api):
    openai_api.models_mode = "forbidden"
    made, http = build(openai_api)
    try:
        with pytest.raises(CloudError) as raised:
            await made.discover()
    finally:
        await http.close()

    assert raised.value.kind == "refused"


async def test_a_rejected_key_fails_the_refresh(openai_api):
    openai_api.models_mode = "auth"
    made, http = build(openai_api)
    try:
        with pytest.raises(CloudError) as raised:
            await made.discover()
    finally:
        await http.close()

    assert raised.value.kind == "auth"


async def test_model_slugs_are_the_provider_key_and_the_openai_id(provider):
    slugs = [f"{OpenAIProvider.key}~{spec.provider_model_id}" for spec in await provider.discover()]

    assert slugs == ["openai~gpt-image-2.5-sunburst", "openai~gpt-image-2.5-flare", "openai~gpt-image-2", "openai~gpt-image-1.5"]
    assert all(set(slug) <= set("abcdefghijklmnopqrstuvwxyz0123456789._~-") and "/" not in slug for slug in slugs)


async def test_text_to_image_sends_the_documented_json_body(provider, openai_api):
    request = await request_for(
        provider, count=2, seed=5, negative_prompt="blurry",
        params={"aspect_ratio": "16:9", "resolution": "2K", "quality": "high", "output_format": "webp",
                "background": True, "x.output_compression": 80},
    )

    await provider.submit(request)

    (call,) = calls(openai_api, "/images/generations")
    assert call["content_type"] == "application/json"
    assert call["body"] == {
        "model": "gpt-image-2.5-flare", "prompt": "a lighthouse", "n": 2, "size": "3648x2048",
        "quality": "high", "output_format": "webp", "background": "transparent", "output_compression": 80,
    }


async def test_a_fixed_size_model_gets_one_of_its_three_sizes_and_auto_sends_none(provider, openai_api):
    await provider.submit(await request_for(provider, model_id="gpt-image-2", params={"aspect_ratio": "3:2", "resolution": "4K"}))
    await provider.submit(await request_for(provider, model_id="gpt-image-2", params={"aspect_ratio": "auto"}))

    first, second = (call["body"] for call in calls(openai_api, "/images/generations"))
    assert first["size"] == "1536x1024"
    assert "size" not in second and "n" not in second


async def test_the_result_carries_the_pictures_the_request_id_and_the_cost(provider, openai_api, tmp_path):
    job = await provider.submit(await request_for(provider, count=3, params={"output_format": "webp"}))

    assert job.job_id == "req_123" and job.result.provider_job_id == "req_123"
    assert [artifact.media_type for artifact in job.result.artifacts] == ["image/webp"] * 3
    written = await provider.fetch(job.result.artifacts[0], tmp_path / "a.webp")
    assert written.read_bytes() == PNG
    cost = job.result.cost
    assert cost.amount_usd == Decimal("0.12517") and cost.source == "estimate"
    assert cost.detail["tokens"] == {"text_input": 10, "image_input": 40, "image_output": 4160}


async def test_no_usage_leaves_the_cost_to_core(provider, openai_api):
    openai_api.usage = None

    job = await provider.submit(await request_for(provider))

    assert job.result.cost is None


async def test_an_edit_sends_every_picture_and_the_mask_as_multipart(provider, openai_api, tmp_path):
    first = png((8, 6), (10, 20, 30, 255))
    second = png((5, 5), (40, 50, 60, 255))
    request = await request_for(
        provider, task="img_edit", count=2,
        params={"aspect_ratio": "1:1", "x.input_fidelity": "high"},
        inputs={
            "reference": [media(tmp_path, "a.png", first), media(tmp_path, "b.png", second)],
            "mask": [media(tmp_path, "m.png", painted_mask((16, 12), (0, 0, 8, 12)))],
        },
    )

    await provider.submit(request)

    (call,) = calls(openai_api, "/images/edits")
    assert call["content_type"] == "multipart/form-data"
    assert call["headers"]["Authorization"] == f"Bearer {KEY}"
    assert call["body"] == {"model": "gpt-image-2.5-flare", "prompt": "a lighthouse", "n": "2", "size": "1024x1024"}
    images = [entry for entry in call["files"] if entry["name"] == "image[]"]
    assert [entry["data"] for entry in images] == [first, second]
    assert [entry["type"] for entry in images] == ["image/png", "image/png"]
    (mask,) = [entry for entry in call["files"] if entry["name"] == "mask"]
    sent = Image.open(io.BytesIO(mask["data"]))
    assert mask["type"] == "image/png" and sent.mode == "RGBA" and sent.size == (8, 6)
    assert sent.getpixel((0, 0))[3] == 0 and sent.getpixel((7, 5))[3] == 255


async def test_input_fidelity_is_sent_for_an_edit_on_a_model_that_has_it(provider, openai_api, tmp_path):
    request = await request_for(
        provider, task="img_edit", model_id="gpt-image-1.5", params={"x.input_fidelity": "high"},
        inputs={"reference": [media(tmp_path, "a.png", PNG)]},
    )

    await provider.submit(request)

    assert calls(openai_api, "/images/edits")[-1]["body"]["input_fidelity"] == "high"
    assert "mask" not in {entry["name"] for entry in calls(openai_api, "/images/edits")[-1]["files"]}


async def test_an_edit_without_a_picture_or_with_too_many_is_refused_before_sending(provider, openai_api, tmp_path):
    many = [media(tmp_path, f"{index}.png", PNG) for index in range(17)]
    for inputs in ({}, {"reference": many}):
        with pytest.raises(CloudError) as raised:
            await provider.submit(await request_for(provider, task="img_edit", inputs=inputs))
        assert raised.value.kind == "invalid_request" and raised.value.request_sent is False

    assert calls(openai_api, "/images/edits") == []


async def test_a_transparent_background_in_jpeg_is_refused_before_sending(provider, openai_api):
    request = await request_for(provider, params={"background": True, "output_format": "jpeg"})

    with pytest.raises(CloudError) as raised:
        await provider.submit(request)

    assert raised.value.kind == "invalid_request" and raised.value.request_sent is False
    assert "PNG or WebP" in raised.value.user_message
    assert calls(openai_api, "/images/generations") == []


async def test_no_user_id_is_sent_by_default(provider, openai_api, tmp_path):
    await provider.submit(await request_for(provider, user_ref="ref-abc"))
    await provider.submit(await request_for(
        provider, task="img_edit", user_ref="ref-abc", inputs={"reference": [media(tmp_path, "a.png", PNG)]},
    ))

    assert "user" not in calls(openai_api, "/images/generations")[-1]["body"]
    assert "user" not in calls(openai_api, "/images/edits")[-1]["body"]


async def test_the_anonymous_user_id_is_sent_only_when_the_admin_turns_it_on(openai_api, tmp_path):
    made, http = build(openai_api, send_user_hash=True)
    try:
        await made.submit(await request_for(made, user_ref="ref-abc"))
        await made.submit(await request_for(made))
        await made.submit(await request_for(
            made, task="img_edit", user_ref="ref-abc", inputs={"reference": [media(tmp_path, "a.png", PNG)]},
        ))
    finally:
        await http.close()

    first, second = (call["body"] for call in calls(openai_api, "/images/generations"))
    assert first["user"] == "ref-abc" and "user" not in second
    assert calls(openai_api, "/images/edits")[-1]["body"]["user"] == "ref-abc"


async def test_the_content_filter_level_is_sent_only_when_lowered(openai_api):
    made, http = build(openai_api, moderation="low")
    try:
        await made.submit(await request_for(made))
    finally:
        await http.close()
    plain, plain_http = build(openai_api)
    try:
        await plain.submit(await request_for(plain))
    finally:
        await plain_http.close()

    lowered, default = (call["body"] for call in calls(openai_api, "/images/generations"))
    assert lowered["moderation"] == "low" and "moderation" not in default


async def test_organization_and_project_headers_are_sent_when_set(openai_api):
    made, http = build(openai_api, organization="org-abc", project="proj_123")
    try:
        await made.discover()
    finally:
        await http.close()
    plain, plain_http = build(openai_api)
    try:
        await plain.discover()
    finally:
        await plain_http.close()

    scoped, unscoped = (call["headers"] for call in calls(openai_api, "/models"))
    assert scoped["OpenAI-Organization"] == "org-abc" and scoped["OpenAI-Project"] == "proj_123"
    assert "OpenAI-Organization" not in unscoped and "OpenAI-Project" not in unscoped


@pytest.mark.parametrize(
    "mode,kind",
    [("moderation", "refused"), ("auth", "auth"), ("quota", "credits"), ("rate", "rate_limited"),
     ("rate_reset", "rate_limited"), ("forbidden", "refused"), ("unknown_model", "invalid_request"),
     ("bad", "invalid_request"), ("server", "unavailable"), ("overloaded", "unavailable"), ("empty", "failed")],
)
async def test_failures_map_to_their_kinds(provider, openai_api, mode, kind):
    request = await request_for(provider)
    openai_api.mode = mode

    with pytest.raises(CloudError) as raised:
        await provider.submit(request)

    assert raised.value.kind == kind and raised.value.user_message


async def test_a_moderation_block_is_plain_for_users_and_detailed_for_admins(provider, openai_api):
    request = await request_for(provider)
    openai_api.mode = "moderation"

    with pytest.raises(CloudError) as raised:
        await provider.submit(request)

    error = raised.value
    assert error.user_message == "The model's content filter refused this request. Try a different prompt or picture."
    assert "moderation_blocked" in error.detail and "violence" in error.detail
    assert "stage: input" in error.detail and "req_fail" in error.detail
    assert "violence" not in error.user_message
    assert "SECRET PROMPT TEXT" not in error.detail and "SECRET PROMPT TEXT" not in error.user_message


async def test_rate_limits_keep_the_retry_delay_from_either_header(provider, openai_api):
    request = await request_for(provider)
    delays = {}
    for mode in ("rate", "rate_reset"):
        openai_api.mode = mode
        with pytest.raises(CloudError) as raised:
            await provider.submit(request)
        delays[mode] = raised.value.retry_after_s

    assert delays == {"rate": 7.0, "rate_reset": 90.0}


async def test_a_failure_after_sending_is_reported_as_sent_and_never_marked_safe_to_repeat(provider, openai_api):
    request = await request_for(provider)
    openai_api.mode = "server"

    with pytest.raises(CloudError) as raised:
        await provider.submit(request)

    assert raised.value.request_sent is True and provider.idempotent_submit is False and provider.supports_cancel is False


async def test_the_key_never_appears_in_errors_or_logs(provider, openai_api, caplog):
    caplog.set_level(logging.DEBUG)
    request = await request_for(provider)
    openai_api.mode = "auth"

    with pytest.raises(CloudError) as raised:
        await provider.submit(request)

    error = raised.value
    assert KEY not in error.user_message and KEY not in error.detail and KEY not in repr(error)
    assert KEY not in caplog.text


async def test_check_reports_the_state_of_the_key(openai_api):
    made, http = build(openai_api)
    try:
        assert (await made.check()).ok is True
        openai_api.models_mode = "no_scope"
        limited = await made.check()
        openai_api.models_mode = "forbidden"
        unverified = await made.check()
        openai_api.models_mode = "auth"
        rejected = await made.check()
    finally:
        await http.close()

    assert limited.ok is True and limited.message
    assert unverified.ok is False and "verified" in unverified.message
    assert rejected.ok is False and KEY not in (rejected.message or "")


async def test_an_edit_picture_openai_cannot_read_is_refused_before_sending(provider, openai_api, tmp_path):
    path = tmp_path / "a.heic"
    path.write_bytes(PNG)
    request = await request_for(provider, task="img_edit", inputs={"reference": [LocalMedia(path, "application/octet-stream", path.stat().st_size)]})

    with pytest.raises(CloudError) as raised:
        await provider.submit(request)

    assert raised.value.kind == "invalid_request" and raised.value.request_sent is False
    assert calls(openai_api, "/images/edits") == []


async def test_pictures_are_read_off_the_event_loop_thread(provider, monkeypatch, tmp_path):
    import backend.provider as provider_module

    real = provider_module.edit_form
    threads = []

    def spy(*args, **kwargs):
        threads.append(threading.get_ident())
        return real(*args, **kwargs)

    monkeypatch.setattr(provider_module, "edit_form", spy)
    request = await request_for(provider, task="img_edit", inputs={"reference": [media(tmp_path, "a.png", PNG)]})

    await provider.submit(request)

    assert threads and threads[0] != threading.get_ident()


async def test_the_data_notice_says_where_the_data_goes(provider):
    assert "OpenAI" in provider.data_notice and "No user name or email" in provider.data_notice


async def test_the_suggested_models_are_in_the_catalog(provider):
    offered = {spec.provider_model_id for spec in await provider.discover()}

    assert provider.suggested_model_ids() and set(provider.suggested_model_ids()) <= offered


async def test_the_contract_kit_passes_against_the_recorded_fixtures(provider, openai_api, tmp_path):
    async def advance(seconds):
        return None

    def failing(mode):
        async def probe():
            openai_api.mode = mode
            try:
                await provider.submit(CloudRequest(model=(await provider.discover())[0], task="txt2img", prompt="x"))
            finally:
                openai_api.mode = "ok"

        return probe

    case = ContractCase(
        make_provider=lambda scenario: provider,
        advance=advance,
        scenarios=(SCENARIO_SYNC,),
        make_request=lambda specs, scenario: CloudRequest(model=specs[0], task="txt2img", prompt="a lighthouse", client_reference="contract"),
        error_probes={
            "auth": failing("auth"), "credits": failing("quota"), "refused": failing("moderation"),
            "rate_limited": failing("rate"), "unavailable": failing("server"), "invalid_request": failing("bad"),
        },
    )

    ran = await run_contract(case, tmp_path)

    assert set(ran) == set(applicable_checks(case))
    assert {"discover_returns_valid_specs", "sync_submit_returns_result", "fetch_writes_artifacts", "errors_map_to_kinds"} <= set(ran)
