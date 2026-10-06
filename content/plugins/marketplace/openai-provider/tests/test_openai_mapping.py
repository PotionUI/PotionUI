from decimal import Decimal

import pytest

from backend.catalog import FLEXIBLE_ASPECTS, RESOLUTIONS, catalog_specs, load_catalog
from backend.errors import map_error, reset_seconds
from backend.mapping import flexible_size, usage_cost

RATES = {"text_input": "5.00", "image_input": "8.00", "image_output": "30.00"}


def specs():
    return {spec.provider_model_id: spec for spec in catalog_specs(load_catalog())}


@pytest.mark.parametrize("aspect", [aspect for aspect in FLEXIBLE_ASPECTS if aspect != "auto"])
@pytest.mark.parametrize("resolution", RESOLUTIONS)
def test_every_flexible_size_keeps_openais_size_rules(aspect, resolution):
    left, right = (float(part) for part in aspect.split(":"))

    width, height = flexible_size(left / right, resolution)

    assert width % 16 == 0 and height % 16 == 0
    assert max(width, height) <= 3840
    assert 655_360 <= width * height <= 8_294_400
    assert 1 / 3 <= width / height <= 3
    assert abs(width / height - left / right) < 0.05


@pytest.mark.parametrize("aspect,resolution,size", [
    (1.0, "1K", (1024, 1024)), (1.5, "1K", (1536, 1024)), (2 / 3, "1K", (1024, 1536)),
    (1.0, "2K", (2048, 2048)), (1.0, "4K", (2880, 2880)), (16 / 9, "4K", (3840, 2160)), (21 / 9, "4K", (3840, 1632)),
])
def test_known_sizes(aspect, resolution, size):
    assert flexible_size(aspect, resolution) == size


def test_cost_is_the_reported_tokens_times_the_listed_prices():
    cost = usage_cost({"input_tokens": 50, "input_tokens_details": {"text_tokens": 10, "image_tokens": 40}, "output_tokens": 4160}, RATES)

    assert cost.amount_usd == Decimal("0.12517") and cost.source == "estimate"
    assert cost.detail["usd_per_million_tokens"] == RATES


def test_input_without_a_breakdown_is_priced_as_text():
    cost = usage_cost({"input_tokens": 1000, "output_tokens": 0}, RATES)

    assert cost.amount_usd == Decimal("0.005")


@pytest.mark.parametrize("usage", [None, {}, {"input_tokens": 0, "output_tokens": 0}, {"input_tokens": "lots", "output_tokens": -5}])
def test_no_usable_usage_gives_no_cost(usage):
    assert usage_cost(usage, RATES) is None


def test_no_rates_gives_no_cost():
    assert usage_cost({"input_tokens": 5, "output_tokens": 5}, {}) is None


@pytest.mark.parametrize("text,seconds", [("1s", 1.0), ("6m0s", 360.0), ("1m30s", 90.0), ("250ms", 0.25), ("1h2m3.5s", 3723.5)])
def test_rate_limit_reset_durations_are_read(text, seconds):
    assert reset_seconds(text) == pytest.approx(seconds)


@pytest.mark.parametrize("text", [None, "", "soon", "5", "1x"])
def test_unreadable_reset_durations_give_nothing(text):
    assert reset_seconds(text) is None


def test_the_catalog_offers_text_to_image_and_edit_with_edit_only_inputs():
    for spec in specs().values():
        assert spec.tasks == {"txt2img", "img_edit"} and spec.outputs == {"image"}
        roles = {media.role: media for media in spec.inputs}
        assert set(roles) == {"reference", "mask"}
        assert all(media.tasks == {"img_edit"} for media in spec.inputs)
        assert roles["reference"].max_items == 16 and roles["mask"].max_items == 1
        assert spec.max_outputs_per_job == 10 and spec.vendor == "openai"


def test_only_the_older_models_offer_input_fidelity_and_only_for_edits():
    fidelity = {model_id: next((p for p in spec.params if p.name == "x.input_fidelity"), None) for model_id, spec in specs().items()}

    assert {model_id for model_id, param in fidelity.items() if param} == {"gpt-image-1.5", "gpt-image-1-mini"}
    assert all(param.tasks == {"img_edit"} for param in fidelity.values() if param)


def test_transparency_and_resolution_follow_the_model():
    names = {model_id: {param.name for param in spec.params} for model_id, spec in specs().items()}

    assert {model_id for model_id, params in names.items() if "background" in params} == {
        "gpt-image-2.5-sunburst", "gpt-image-2.5-flare", "gpt-image-1.5", "gpt-image-1-mini",
    }
    assert {model_id for model_id, params in names.items() if "resolution" in params} == {"gpt-image-2.5-sunburst", "gpt-image-2.5-flare"}
    assert "seed" not in set().union(*names.values())


def test_quality_follows_the_model():
    quality = {model_id: next(p for p in spec.params if p.name == "quality").values for model_id, spec in specs().items()}

    assert quality["gpt-image-2.5-flare"] == ("auto", "low", "medium", "high", "xhigh", "max")
    assert quality["gpt-image-2"] == ("auto", "low", "medium", "high")


def test_prices_are_listed_per_token_for_admins():
    lines = {(line.applies_to, line.usd) for line in specs()["gpt-image-1-mini"].pricing}

    assert lines == {("text input", Decimal("0.000002")), ("image input", Decimal("0.0000025")), ("image output", Decimal("0.000008"))}
    assert all(line.unit == "token" for spec in specs().values() for line in spec.pricing)


@pytest.mark.parametrize("code", [
    "insufficient_quota", "credit_balance_exhausted", "organization_spend_limit_exceeded",
    "project_spend_limit_exceeded", "organization_usage_limit_exceeded",
])
def test_billing_codes_mean_out_of_credit_and_are_not_retried_as_rate_limits(code):
    error = map_error(429, {"retry-after": "3"}, {"error": {"message": "limit", "type": "requests", "code": code}})

    assert error.kind == "credits" and error.retry_after_s is None


def test_a_quota_error_type_means_out_of_credit_whatever_its_code():
    error = map_error(429, {}, {"error": {"message": "quota", "type": "insufficient_quota", "code": "something_new"}})

    assert error.kind == "credits"


def test_a_plain_rate_limit_stays_a_rate_limit():
    error = map_error(429, {"retry-after": "3"}, {"error": {"message": "slow down", "type": "requests", "code": "rate_limit_exceeded"}})

    assert error.kind == "rate_limited" and error.retry_after_s == 3.0


def test_an_error_body_that_is_not_json_still_maps_by_status():
    assert map_error(502, {}, "<html>bad gateway</html>").kind == "unavailable"
    assert map_error(200, {}, {}) is None
