import base64
import logging
from decimal import Decimal

import pytest

from src.plugin_api.cloud import CloudError, CloudHttp, CloudRequest, LocalMedia, spec_problems
from src.plugin_api.cloud_testing import SCENARIO_SYNC, ContractCase, FakeClock, applicable_checks, run_contract

from backend.config import OpenRouterConfig
from backend.provider import OpenRouterProvider

from .fixtures import KEY, PNG


def build(fixture, **config):
    settings = {"id": "or-1", "name": "OpenRouter", "api_key": KEY, "base_url": fixture.api_url, **config}
    cfg = OpenRouterConfig(**settings)
    http = CloudHttp(
        OpenRouterProvider.api_base_url(cfg),
        auth_headers=OpenRouterProvider.auth_headers(cfg),
        timeout_s=10,
        clock=FakeClock(),
        allow_private_targets=True,
    )
    return OpenRouterProvider(cfg, http), http


@pytest.fixture
async def provider(openrouter):
    made, http = build(openrouter)
    try:
        yield made
    finally:
        await http.close()


async def spec_of(provider, model_id):
    return next(spec for spec in await provider.discover() if spec.provider_model_id == model_id)


async def request_for(provider, task="txt2img", model_id="vendor-a/image-pro", **fields):
    return CloudRequest(model=await spec_of(provider, model_id), task=task, prompt="a lighthouse", client_reference="gen-1", **fields)


def generate_calls(fixture):
    return [r for r in fixture.requests if r["path"] == "/images"]


async def test_discovery_keeps_image_models_and_skips_what_does_not_map(provider):
    specs = await provider.discover()

    assert sorted(spec.provider_model_id for spec in specs) == ["vendor-a/image-pro", "vendor-b/text-only", "vendor-d/odd"]
    assert all(spec_problems(spec) == [] for spec in specs)


async def test_an_image_model_maps_to_tasks_params_inputs_and_prices(provider):
    spec = await spec_of(provider, "vendor-a/image-pro")
    params = {param.name: param for param in spec.params}

    assert spec.label == "Image Pro" and spec.vendor == "vendor-a" and spec.outputs == {"image"}
    assert spec.tasks == {"txt2img", "img_edit"}
    assert spec.max_outputs_per_job == 4
    assert params["aspect_ratio"].values == ("1:1", "16:9", "21:9")
    assert params["quality"].values == ("low", "high")
    assert params["resolution"].values == ("512", "1K", "2K", "4K")
    assert params["background"].kind == "boolean"
    assert params["x.style"].kind == "text"
    assert not {"n", "seed", "input_references"} & set(params)
    (reference,) = spec.inputs
    assert (reference.role, reference.max_items, reference.tasks) == ("reference", 6, {"img_edit"})
    assert {(line.unit, line.usd) for line in spec.pricing} == {("image", Decimal("0.04")), ("sku", Decimal("0.1"))}
    assert "seed" in spec.raw["supported"]


async def test_a_text_only_model_offers_no_edit_and_fills_documented_defaults(provider):
    spec = await spec_of(provider, "vendor-b/text-only")
    params = {param.name: param for param in spec.params}

    assert spec.tasks == {"txt2img"} and spec.inputs == ()
    assert "aspect_ratio" not in params
    assert params["output_format"].values == ("png", "jpeg", "webp")
    assert (params["x.output_compression"].minimum, params["x.output_compression"].maximum) == (0.0, 100.0)
    assert spec.deprecated_at == "2026-12-31"
    assert spec.pricing[0].unit == "megapixel"


async def test_a_model_with_a_half_described_parameter_is_kept_without_it(provider):
    spec = await spec_of(provider, "vendor-d/odd")

    assert spec.params == () and spec.tasks == {"txt2img"}


async def test_a_failing_endpoint_lookup_only_drops_that_models_details(provider, openrouter):
    openrouter.failing_endpoints.add("vendor-a/image-pro")

    spec = await spec_of(provider, "vendor-a/image-pro")

    assert spec.pricing == ()
    assert {param.name for param in spec.params} >= {"aspect_ratio", "quality"}


async def test_sync_submit_with_inline_pictures_returns_the_files_and_the_cost(provider, openrouter, tmp_path):
    request = await request_for(provider, count=2, seed=5, params={"aspect_ratio": "16:9", "background": True, "x.style": "noir", "unknown": 1})

    job = await provider.submit(request)

    assert job.job_id == "gen-123"
    assert len(job.result.artifacts) == 2 and job.result.cost.amount_usd == Decimal("0.0412")
    written = await provider.fetch(job.result.artifacts[0], tmp_path / "a.png")
    assert written.read_bytes() == PNG
    assert generate_calls(openrouter)[-1]["body"] == {
        "model": "vendor-a/image-pro", "prompt": "a lighthouse", "n": 2, "seed": 5,
        "aspect_ratio": "16:9", "background": "transparent", "style": "noir",
    }


async def test_a_seed_is_only_sent_when_the_model_supports_it(provider, openrouter):
    await provider.submit(await request_for(provider, model_id="vendor-b/text-only", seed=9))

    assert "seed" not in generate_calls(openrouter)[-1]["body"]


async def test_sync_submit_with_addresses_downloads_from_the_other_host_without_the_key(provider, openrouter, tmp_path):
    openrouter.mode = "url"

    job = await provider.submit(await request_for(provider))
    written = await provider.fetch(job.result.artifacts[0], tmp_path / "b.png")

    assert written.read_bytes() == PNG
    assert len(openrouter.cdn_requests) == 1
    assert "Authorization" not in openrouter.cdn_requests[0]["headers"]


async def test_edit_sends_the_reference_pictures_as_data_addresses(provider, openrouter, tmp_path):
    first, second = tmp_path / "a.png", tmp_path / "b.png"
    first.write_bytes(PNG)
    second.write_bytes(PNG + b"x")
    request = await request_for(
        provider, task="img_edit",
        inputs={"reference": [LocalMedia(first, "image/png", first.stat().st_size), LocalMedia(second, "image/png", second.stat().st_size)]},
    )

    await provider.submit(request)

    sent = generate_calls(openrouter)[-1]["body"]["input_references"]
    assert [item.split(",", 1)[0] for item in sent] == ["data:image/png;base64"] * 2
    assert [base64.b64decode(item.split(",", 1)[1]) for item in sent] == [PNG, PNG + b"x"]


async def test_the_allowed_upstream_providers_limit_routing(openrouter):
    made, http = build(openrouter, upstream_providers=" Alpha , Beta ,")
    try:
        await made.submit(await request_for(made))
    finally:
        await http.close()

    assert generate_calls(openrouter)[-1]["body"]["provider"] == {"only": ["Alpha", "Beta"]}


async def test_no_routing_limit_is_sent_by_default(provider, openrouter):
    await provider.submit(await request_for(provider))

    assert "provider" not in generate_calls(openrouter)[-1]["body"]


@pytest.mark.parametrize(
    "mode,kind",
    [("auth", "auth"), ("credits", "credits"), ("moderation", "refused"), ("forbidden", "refused"), ("rate", "rate_limited"),
     ("down", "unavailable"), ("routing", "unavailable"), ("bad", "invalid_request"), ("slow", "timeout"),
     ("empty", "failed"), ("inline_error", "failed")],
)
async def test_failures_map_to_their_kinds(provider, openrouter, mode, kind):
    request = await request_for(provider)
    openrouter.mode = mode

    with pytest.raises(CloudError) as raised:
        await provider.submit(request)

    assert raised.value.kind == kind and raised.value.user_message


async def test_a_moderation_refusal_keeps_its_reasons_for_admins_and_not_for_users(provider, openrouter):
    request = await request_for(provider)
    openrouter.mode = "moderation"

    with pytest.raises(CloudError) as raised:
        await provider.submit(request)

    assert "violence" in raised.value.detail and "Alpha" in raised.value.detail
    assert "violence" not in raised.value.user_message
    assert "SECRET PROMPT TEXT" not in raised.value.detail and "SECRET PROMPT TEXT" not in raised.value.user_message


async def test_rate_limits_credits_and_routing_failures_keep_the_retry_delay(provider, openrouter):
    request = await request_for(provider)
    for mode in ("rate", "credits", "routing"):
        openrouter.mode = mode
        with pytest.raises(CloudError) as raised:
            await provider.submit(request)
        assert raised.value.retry_after_s == 7.0


async def test_a_failure_after_sending_is_reported_as_sent_and_never_marked_safe_to_repeat(provider, openrouter):
    request = await request_for(provider)
    openrouter.mode = "down"

    with pytest.raises(CloudError) as raised:
        await provider.submit(request)

    assert raised.value.request_sent is True and provider.idempotent_submit is False


async def test_the_key_goes_only_to_the_api_host(provider, openrouter, tmp_path):
    await provider.discover()
    openrouter.mode = "url"
    job = await provider.submit(await request_for(provider))
    await provider.fetch(job.result.artifacts[0], tmp_path / "c.png")

    assert all(r["headers"]["Authorization"] == f"Bearer {KEY}" for r in openrouter.requests)
    assert all("Authorization" not in r["headers"] for r in openrouter.cdn_requests)


async def test_attribution_headers_are_sent_when_set(openrouter):
    made, http = build(openrouter, app_title="My Studio", app_url="https://studio.example")
    try:
        await made.discover()
    finally:
        await http.close()

    headers = openrouter.requests[0]["headers"]
    assert headers["X-OpenRouter-Title"] == "My Studio" and headers["HTTP-Referer"] == "https://studio.example"


async def test_empty_attribution_is_not_sent(openrouter):
    made, http = build(openrouter, app_title="", app_url="")
    try:
        await made.discover()
    finally:
        await http.close()

    assert "X-OpenRouter-Title" not in openrouter.requests[0]["headers"]
    assert "HTTP-Referer" not in openrouter.requests[0]["headers"]


async def test_the_default_attribution_names_the_app_without_an_address(provider, openrouter):
    await provider.discover()

    assert openrouter.requests[0]["headers"]["X-OpenRouter-Title"] == "PotionUI"
    assert "HTTP-Referer" not in openrouter.requests[0]["headers"]


async def test_no_user_id_is_sent_by_default(provider, openrouter):
    await provider.submit(await request_for(provider, user_ref="ref-abc"))

    assert "user" not in generate_calls(openrouter)[-1]["body"]


async def test_the_anonymous_user_id_from_core_is_forwarded_when_the_admin_turns_it_on(openrouter):
    made, http = build(openrouter, send_user_hash=True)
    try:
        await made.submit(await request_for(made, user_ref="ref-abc"))
        await made.submit(await request_for(made))
    finally:
        await http.close()

    bodies = [r["body"] for r in generate_calls(openrouter)]
    assert bodies[0]["user"] == "ref-abc"
    assert "user" not in bodies[1]


async def test_a_transparent_background_is_asked_for_in_words_and_no_background_is_left_out(provider, openrouter):
    await provider.submit(await request_for(provider, params={"background": True}))
    await provider.submit(await request_for(provider, params={"background": False}))
    await provider.submit(await request_for(provider, params={"background": "opaque"}))

    bodies = [r["body"] for r in generate_calls(openrouter)]
    assert bodies[0]["background"] == "transparent"
    assert "background" not in bodies[1]
    assert bodies[2]["background"] == "opaque"


async def test_background_is_offered_as_a_yes_no_choice(provider):
    spec = await spec_of(provider, "vendor-a/image-pro")

    assert {param.name: param.kind for param in spec.params}["background"] == "boolean"


async def test_the_key_never_appears_in_errors_or_logs(provider, openrouter, caplog, tmp_path):
    caplog.set_level(logging.DEBUG)
    request = await request_for(provider)
    openrouter.mode = "echo_key"
    with pytest.raises(CloudError) as raised:
        await provider.submit(request)
    openrouter.mode = "auth_on_list"
    with pytest.raises(CloudError) as listed:
        await provider.discover()
    openrouter.mode = "url"
    job = await provider.submit(request)
    await provider.fetch(job.result.artifacts[0], tmp_path / "d.png")

    for error in (raised.value, listed.value):
        assert KEY not in error.user_message and KEY not in error.detail and KEY not in repr(error)
    assert KEY not in caplog.text


async def test_the_data_notice_says_where_the_data_goes(provider):
    assert "OpenRouter" in provider.data_notice and "company" in provider.data_notice
    assert "No user name or email" in provider.data_notice


async def test_the_suggested_models_come_from_the_plugin_file(provider):
    suggested = provider.suggested_model_ids()

    assert suggested and all("/" in model_id for model_id in suggested)


async def test_the_key_is_declared_secret_and_the_driver_is_fixed():
    assert OpenRouterConfig.secret_field_names() == frozenset({"api_key"})
    assert OpenRouterConfig.model_fields["driver"].default == "cloud.openrouter"
    assert OpenRouterProvider.key == "openrouter"


async def test_check_reports_a_rejected_key(openrouter):
    made, http = build(openrouter)
    try:
        assert (await made.check()).ok is True
        openrouter.mode = "auth_on_list"
        health = await made.check()
    finally:
        await http.close()

    assert health.ok is False and KEY not in (health.message or "")


async def test_the_contract_kit_passes_against_the_recorded_fixtures(provider, openrouter, tmp_path):
    async def advance(seconds):
        return None

    def failing(mode):
        async def probe():
            openrouter.mode = mode
            try:
                await provider.submit(CloudRequest(model=(await provider.discover())[0], task="txt2img", prompt="x"))
            finally:
                openrouter.mode = "b64"

        return probe

    case = ContractCase(
        make_provider=lambda scenario: provider,
        advance=advance,
        scenarios=(SCENARIO_SYNC,),
        make_request=lambda specs, scenario: CloudRequest(model=specs[0], task="txt2img", prompt="a lighthouse", client_reference="contract"),
        error_probes={
            "auth": failing("auth"), "credits": failing("credits"), "refused": failing("moderation"),
            "rate_limited": failing("rate"), "unavailable": failing("down"), "invalid_request": failing("bad"),
        },
    )

    ran = await run_contract(case, tmp_path)

    assert set(ran) == set(applicable_checks(case))
    assert {"discover_returns_valid_specs", "sync_submit_returns_result", "fetch_writes_artifacts", "errors_map_to_kinds"} <= set(ran)
