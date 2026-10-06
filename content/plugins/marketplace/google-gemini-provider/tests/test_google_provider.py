import base64
import logging
import re
import threading
from decimal import Decimal

import pytest

from src.plugin_api.cloud import CloudError, CloudHttp, CloudRequest, LocalMedia, spec_problems
from src.plugin_api.cloud_testing import SCENARIO_SYNC, ContractCase, FakeClock, applicable_checks, run_contract

from backend.catalog import STATIC_MODELS, static_specs
from backend.config import GoogleConfig
from backend.provider import GoogleProvider

from .fixtures import KEY, PNG


def build(fixture, **config):
    settings = {"id": "google-1", "name": "Google", "api_key": KEY, "base_url": fixture.api_url, **config}
    cfg = GoogleConfig(**settings)
    http = CloudHttp(
        GoogleProvider.api_base_url(cfg),
        auth_headers=GoogleProvider.auth_headers(cfg),
        timeout_s=10,
        clock=FakeClock(),
        allow_private_targets=True,
    )
    return GoogleProvider(cfg, http), http


@pytest.fixture
async def provider(gemini):
    made, http = build(gemini)
    try:
        yield made
    finally:
        await http.close()


async def spec_of(provider, model_id):
    return next(spec for spec in await provider.discover() if spec.provider_model_id == model_id)


async def request_for(provider, task="txt2img", model_id="gemini-3.1-flash-image", **fields):
    return CloudRequest(model=await spec_of(provider, model_id), task=task, prompt="a lighthouse", client_reference="gen-1", **fields)


def generate_calls(fixture):
    return [r for r in fixture.requests if r["path"].endswith(":generateContent")]


def picture(tmp_path, name, payload=PNG):
    path = tmp_path / name
    path.write_bytes(payload)
    return LocalMedia(path, "image/png", path.stat().st_size)


async def test_discovery_reads_every_page_and_keeps_only_image_and_video_models(provider, gemini):
    specs = await provider.discover()

    assert sorted(spec.provider_model_id for spec in specs) == [
        "gemini-3-pro-image", "gemini-3.1-flash-image", "gemini-9-ultra-image",
        "veo-3.1-generate-preview", "veo-3.1-lite-generate-preview",
    ]
    assert all(spec_problems(spec) == [] for spec in specs)
    listings = [r for r in gemini.requests if r["path"] == "/models"]
    assert [r["query"].get("pageToken") for r in listings] == [None, "page-2"]


async def test_a_known_model_keeps_its_curated_capabilities_and_prices(provider):
    spec = await spec_of(provider, "gemini-3.1-flash-image")
    params = {param.name: param for param in spec.params}

    assert spec.tasks == {"txt2img", "img_edit"} and spec.outputs == {"image"} and spec.vendor == "google"
    assert {"1:1", "16:9", "9:16", "21:9", "8:1"} <= set(params["aspect_ratio"].values)
    assert params["resolution"].values == ("1K", "2K", "4K")
    (reference,) = spec.inputs
    assert (reference.role, reference.max_items, reference.tasks) == ("reference", 14, {"img_edit"})
    assert spec.max_outputs_per_job == 1
    assert {(line.unit, line.usd, line.applies_to) for line in spec.pricing} == {
        ("image", Decimal("0.067"), None), ("image", Decimal("0.034"), "2K"), ("image", Decimal("0.084"), "4K"),
    }


async def test_an_unknown_image_model_is_offered_with_safe_defaults_and_no_price(provider):
    spec = await spec_of(provider, "gemini-9-ultra-image")

    assert spec.label == "Gemini 9 Ultra Image" and spec.description == "A model the plugin has never heard of"
    assert spec.tasks == {"txt2img", "img_edit"} and spec.pricing == ()
    assert "resolution" not in {param.name for param in spec.params}
    assert spec.deprecated_at == "2027-01-31"


async def test_retired_and_non_generating_models_are_left_out(provider):
    ids = {spec.provider_model_id for spec in await provider.discover()}

    assert not {"gemini-2.5-flash-image", "gemini-3.5-flash", "text-embedding-005", "gemini-omni-1.1-flash"} & ids


@pytest.mark.parametrize("mode", ["internal", "overloaded", "not_found", "empty"])
async def test_a_failing_or_empty_listing_falls_back_to_the_built_in_models(provider, gemini, mode):
    gemini.listing_mode = mode

    specs = await provider.discover()

    assert [spec.provider_model_id for spec in specs] == [entry.model_id for entry in STATIC_MODELS]


@pytest.mark.parametrize("mode,kind", [("bad_key", "auth"), ("unauthenticated", "auth"), ("forbidden", "refused"), ("prepay", "credits")])
async def test_a_rejected_key_during_listing_fails_the_refresh_instead_of_hiding_it(provider, gemini, mode, kind):
    gemini.listing_mode = mode

    with pytest.raises(CloudError) as raised:
        await provider.discover()

    assert raised.value.kind == kind


def test_the_built_in_models_are_current_and_valid():
    specs = static_specs()

    assert all(spec_problems(spec) == [] for spec in specs)
    ids = {spec.provider_model_id for spec in specs}
    assert {"gemini-3.1-flash-image", "gemini-3-pro-image", "gemini-3.1-flash-lite-image"} <= ids
    assert not any(model_id.startswith("imagen-") or model_id == "gemini-2.5-flash-image" for model_id in ids)


@pytest.mark.parametrize("task,expected", [
    ("txt2img", {"gemini-3.1-flash-image", "gemini-3-pro-image", "gemini-3.1-flash-lite-image"}),
    ("img_edit", {"gemini-3.1-flash-image", "gemini-3-pro-image", "gemini-3.1-flash-lite-image"}),
    ("txt2video", {"veo-3.1-lite-generate-preview", "veo-3.1-generate-preview", "veo-3.1-fast-generate-preview"}),
    ("img2video", {"veo-3.1-lite-generate-preview", "veo-3.1-generate-preview", "veo-3.1-fast-generate-preview"}),
])
def test_the_catalog_files_each_model_under_the_tasks_it_can_do(task, expected):
    assert {spec.provider_model_id for spec in static_specs() if task in spec.tasks} == expected


def test_image_models_are_never_offered_for_video_and_video_models_never_for_pictures():
    for spec in static_specs():
        if "video" in spec.outputs:
            assert not {"txt2img", "img_edit"} & spec.tasks
        else:
            assert not {"txt2video", "img2video"} & spec.tasks


def test_every_model_id_makes_a_clean_google_slug():
    assert GoogleProvider.key == "google"
    for spec in static_specs():
        assert re.fullmatch(r"[a-z0-9._-]+", spec.provider_model_id), spec.provider_model_id
        slug = f"{GoogleProvider.key}~{spec.provider_model_id}"
        assert "/" not in slug and slug.startswith("google~")


async def test_text_to_image_sends_the_documented_generate_content_body(provider, gemini):
    request = await request_for(provider, seed=5, params={"aspect_ratio": "16:9", "resolution": "2K"})

    await provider.submit(request)

    call = generate_calls(gemini)[-1]
    assert call["path"] == "/models/gemini-3.1-flash-image:generateContent"
    assert call["body"] == {
        "contents": [{"role": "user", "parts": [{"text": "a lighthouse"}]}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"], "imageConfig": {"aspectRatio": "16:9", "imageSize": "2K"}},
    }


async def test_a_request_without_size_settings_sends_no_image_config(provider, gemini):
    lite = next(spec for spec in static_specs() if spec.provider_model_id == "gemini-3.1-flash-lite-image")

    await provider.submit(CloudRequest(model=lite, task="txt2img", prompt="a lighthouse"))

    assert generate_calls(gemini)[-1]["body"]["generationConfig"] == {"responseModalities": ["TEXT", "IMAGE"]}


async def test_edit_sends_the_prompt_then_every_reference_picture_inline(provider, gemini, tmp_path):
    first, second = picture(tmp_path, "a.png"), picture(tmp_path, "b.png", PNG + b"x")
    request = await request_for(provider, task="img_edit", inputs={"reference": [first, second]}, params={"aspect_ratio": "1:1"})

    await provider.submit(request)

    parts = generate_calls(gemini)[-1]["body"]["contents"][0]["parts"]
    assert parts[0] == {"text": "a lighthouse"}
    assert [part["inlineData"]["mimeType"] for part in parts[1:]] == ["image/png", "image/png"]
    assert [base64.b64decode(part["inlineData"]["data"]) for part in parts[1:]] == [PNG, PNG + b"x"]


async def test_the_answer_becomes_image_files_and_thought_images_are_skipped(provider, tmp_path):
    job = await provider.submit(await request_for(provider))

    assert job.job_id == "resp-123"
    (artifact,) = job.result.artifacts
    assert (artifact.modality, artifact.media_type) == ("image", "image/png")
    written = await provider.fetch(artifact, tmp_path / "out.png")
    assert written.read_bytes() == PNG


async def test_the_cost_is_recorded_from_the_reported_token_counts(provider):
    job = await provider.submit(await request_for(provider, params={"resolution": "1K"}))

    cost = job.result.cost
    expected = (Decimal(1000) * Decimal("0.50") + Decimal(1120) * Decimal("60.00") + Decimal(300) * Decimal("3.00")) / Decimal(1_000_000)
    assert cost.source == "estimate" and cost.amount_usd == expected
    assert cost.detail == {"basis": "tokens", "input_tokens": 1000, "output_image_tokens": 1120, "output_text_tokens": 300}


async def test_without_token_counts_the_cost_falls_back_to_the_listed_price_per_picture(provider, gemini):
    gemini.mode = "no_usage"

    job = await provider.submit(await request_for(provider, model_id="gemini-3-pro-image", params={"resolution": "4K"}))

    assert job.result.cost.amount_usd == Decimal("0.24") and job.result.cost.source == "estimate"
    assert job.result.cost.detail["basis"] == "per_image"


async def test_a_model_without_known_prices_records_no_amount(provider, gemini):
    gemini.mode = "no_usage"

    job = await provider.submit(await request_for(provider, model_id="gemini-9-ultra-image"))

    assert job.result.cost is None


async def test_a_blocked_prompt_is_a_refusal_with_plain_words_for_users_and_the_reason_for_admins(provider, gemini):
    gemini.mode = "prompt_blocked"

    with pytest.raises(CloudError) as raised:
        await provider.submit(await request_for(provider))

    error = raised.value
    assert error.kind == "refused"
    assert error.user_message == "The model's content filter refused this request. Try a different prompt or picture."
    assert "SAFETY" in error.detail and "HARM_CATEGORY_DANGEROUS_CONTENT=HIGH" in error.detail
    assert "SAFETY" not in error.user_message and "HARM" not in error.user_message


@pytest.mark.parametrize("mode,reason", [("image_safety", "IMAGE_SAFETY"), ("prohibited", "PROHIBITED_CONTENT")])
async def test_a_filtered_picture_is_a_refusal(provider, gemini, mode, reason):
    gemini.mode = mode

    with pytest.raises(CloudError) as raised:
        await provider.submit(await request_for(provider))

    assert raised.value.kind == "refused" and reason in raised.value.detail
    assert reason not in raised.value.user_message and "SECRET MODEL TEXT" not in raised.value.user_message


async def test_an_answer_in_words_only_fails_plainly_and_keeps_the_words_for_admins(provider, gemini):
    gemini.mode = "text_only"

    with pytest.raises(CloudError) as raised:
        await provider.submit(await request_for(provider))

    assert raised.value.kind == "failed"
    assert raised.value.user_message == "The model answered without a picture. Try rephrasing the prompt."
    assert "STOP" in raised.value.detail and "describe it in words" in raised.value.detail


@pytest.mark.parametrize(
    "mode,kind",
    [("bad_key", "auth"), ("unauthenticated", "auth"), ("forbidden", "refused"), ("prepay", "credits"),
     ("billing", "credits"), ("location", "refused"), ("rate", "rate_limited"), ("rate_header", "rate_limited"),
     ("internal", "unavailable"), ("overloaded", "unavailable"), ("deadline", "timeout"),
     ("not_found", "invalid_request"), ("invalid", "invalid_request"), ("garbage", "failed")],
)
async def test_failures_map_to_their_kinds(provider, gemini, mode, kind):
    request = await request_for(provider)
    gemini.mode = mode

    with pytest.raises(CloudError) as raised:
        await provider.submit(request)

    assert raised.value.kind == kind and raised.value.user_message


async def test_an_invalid_key_reads_as_a_key_problem_not_as_bad_settings(provider, gemini):
    request = await request_for(provider)
    gemini.mode = "bad_key"

    with pytest.raises(CloudError) as raised:
        await provider.submit(request)

    assert raised.value.user_message == "Google rejected the API key. Check it in Administration, Backends."
    assert "INVALID_ARGUMENT" in raised.value.detail


@pytest.mark.parametrize("mode,delay", [("rate", 37.0), ("rate_header", 7.0)])
async def test_a_rate_limit_keeps_googles_retry_delay(provider, gemini, mode, delay):
    request = await request_for(provider)
    gemini.mode = mode

    with pytest.raises(CloudError) as raised:
        await provider.submit(request)

    assert raised.value.retry_after_s == delay


async def test_a_server_error_after_sending_is_never_marked_safe_to_repeat(provider, gemini):
    request = await request_for(provider)
    gemini.mode = "internal"

    with pytest.raises(CloudError) as raised:
        await provider.submit(request)

    assert raised.value.request_sent is True and provider.idempotent_submit is False


async def test_the_key_travels_in_the_header_and_never_in_the_address(provider, gemini):
    await provider.discover()
    await provider.submit(await request_for(provider))

    assert gemini.requests
    for call in gemini.requests:
        assert call["headers"]["x-goog-api-key"] == KEY
        assert "key" not in call["query"] and KEY not in str(call["query"])
        assert "Authorization" not in call["headers"]


async def test_no_user_id_is_ever_sent(provider, gemini, tmp_path):
    request = await request_for(provider, task="img_edit", user_ref="ref-abc", inputs={"reference": [picture(tmp_path, "a.png")]})

    await provider.submit(request)

    call = generate_calls(gemini)[-1]
    assert "ref-abc" not in repr(call["body"]) and "ref-abc" not in repr(call["headers"]) and "ref-abc" not in repr(call["query"])
    assert not {"user", "userId", "user_id", "labels"} & set(call["body"])


async def test_the_key_never_appears_in_errors_or_logs(provider, gemini, caplog):
    caplog.set_level(logging.DEBUG)
    request = await request_for(provider)
    gemini.mode = "bad_key"
    with pytest.raises(CloudError) as raised:
        await provider.submit(request)
    gemini.listing_mode = "bad_key"
    with pytest.raises(CloudError) as listed:
        await provider.discover()

    for error in (raised.value, listed.value):
        assert KEY not in error.user_message and KEY not in error.detail and KEY not in repr(error)
    assert KEY not in caplog.text


async def test_a_model_id_that_could_change_the_address_is_refused_before_sending(provider, gemini):
    spec = await spec_of(provider, "gemini-3.1-flash-image")
    from dataclasses import replace

    hostile = replace(spec, provider_model_id="../files/x")
    before = len(gemini.requests)

    with pytest.raises(CloudError) as raised:
        await provider.submit(CloudRequest(model=hostile, task="txt2img", prompt="x"))

    assert raised.value.kind == "invalid_request" and raised.value.request_sent is False
    assert len(gemini.requests) == before


async def test_the_data_notice_says_where_the_data_goes(provider):
    assert "Google" in provider.data_notice and "Gemini API" in provider.data_notice
    assert "No user name, email or user id" in provider.data_notice


async def test_the_suggested_models_are_in_the_built_in_list(provider):
    suggested = provider.suggested_model_ids()

    assert suggested and set(suggested) <= {entry.model_id for entry in STATIC_MODELS}


async def test_the_key_is_declared_secret_and_the_driver_is_fixed():
    assert GoogleConfig.secret_field_names() == frozenset({"api_key"})
    assert GoogleConfig.model_fields["driver"].default == "cloud.google"
    assert GoogleProvider.supports_cancel is False


async def test_check_reports_a_rejected_key(gemini):
    made, http = build(gemini)
    try:
        assert (await made.check()).ok is True
        gemini.listing_mode = "bad_key"
        health = await made.check()
    finally:
        await http.close()

    assert health.ok is False and KEY not in (health.message or "")


async def test_pictures_are_read_off_the_event_loop_thread(provider, monkeypatch, tmp_path):
    import backend.provider as provider_module

    real = provider_module.build_image_body
    threads = []

    def spy(*args, **kwargs):
        threads.append(threading.get_ident())
        return real(*args, **kwargs)

    monkeypatch.setattr(provider_module, "build_image_body", spy)
    request = await request_for(provider, task="img_edit", inputs={"reference": [picture(tmp_path, "a.png")]})

    await provider.submit(request)

    assert threads and threads[0] != threading.get_ident()


async def test_the_contract_kit_passes_against_the_recorded_fixtures(provider, gemini, tmp_path):
    async def advance(seconds):
        return None

    def image_spec(specs):
        return next(spec for spec in specs if spec.provider_model_id == "gemini-3.1-flash-image")

    def failing(mode):
        async def probe():
            gemini.mode = mode
            try:
                await provider.submit(CloudRequest(model=image_spec(await provider.discover()), task="txt2img", prompt="x"))
            finally:
                gemini.mode = "image"

        return probe

    case = ContractCase(
        make_provider=lambda scenario: provider,
        advance=advance,
        scenarios=(SCENARIO_SYNC,),
        make_request=lambda specs, scenario: CloudRequest(model=image_spec(specs), task="txt2img", prompt="a lighthouse", client_reference="contract"),
        error_probes={
            "auth": failing("bad_key"), "credits": failing("prepay"), "refused": failing("prompt_blocked"),
            "rate_limited": failing("rate"), "unavailable": failing("overloaded"), "invalid_request": failing("invalid"),
            "timeout": failing("deadline"),
        },
    )

    ran = await run_contract(case, tmp_path)

    assert set(ran) == set(applicable_checks(case))
    assert {"discover_returns_valid_specs", "sync_submit_returns_result", "fetch_writes_artifacts", "errors_map_to_kinds"} <= set(ran)
