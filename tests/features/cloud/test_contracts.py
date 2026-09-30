from dataclasses import replace
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.features.cloud.contracts import (
    CANONICAL_PARAMS,
    CloudBackendConfig,
    CloudError,
    ParamSpec,
    PriceLine,
    is_canonical_param,
    spec_problems,
)
from src.features.cloud.http import CloudHttp
from src.features.cloud.testing.fake import FakeCloudConfig, FakeCloudProvider, fake_specs
from src.pipelines.cloud import CloudRunError


def test_cloud_error_carries_its_fields_and_is_a_run_error():
    error = CloudError("rate_limited", "slow down", detail="d", retry_after_s=3.0)
    assert (error.kind, error.user_message, error.detail, error.retry_after_s) == ("rate_limited", "slow down", "d", 3.0)
    assert str(error) == "slow down"
    assert isinstance(error, CloudRunError)


def test_cloud_error_rejects_unknown_kinds():
    with pytest.raises(ValueError):
        CloudError("nonsense", "x")


def test_config_defaults_and_bounds():
    config = CloudBackendConfig(id="a", name="b")
    assert (config.engine, config.driver, config.timeout_seconds, config.max_parallel) == ("cloud", "cloud", 1800, 4)
    for bad in (0, 33):
        with pytest.raises(ValidationError):
            CloudBackendConfig(id="a", name="b", max_parallel=bad)
    assert CloudBackendConfig(id="a", name="b", max_parallel=32).max_parallel == 32


def test_admin_form_lists_max_parallel_and_hides_base_fields():
    names = [field["name"] for field in FakeCloudConfig.engine_fields()]
    assert "max_parallel" in names
    assert "timeout_seconds" not in names
    assert FakeCloudConfig.secret_field_names() == frozenset({"api_key"})
    assert FakeCloudConfig(id="a", name="b").driver == "cloud.fake"


def test_canonical_vocabulary():
    assert is_canonical_param("aspect_ratio")
    assert is_canonical_param("x.style")
    assert not is_canonical_param("aspect")
    assert {"prompt", "seed", "duration_s", "enhance_prompt"} <= CANONICAL_PARAMS


def test_valid_fake_specs_have_no_problems():
    for spec in fake_specs():
        assert spec_problems(spec) == []


@pytest.mark.parametrize(
    "change,needle",
    [
        (lambda s: replace(s, provider_model_id=""), "provider_model_id"),
        (lambda s: replace(s, tasks=frozenset({"bogus"})), "unknown task"),
        (lambda s: replace(s, outputs=frozenset()), "no outputs"),
        (lambda s: replace(s, max_outputs_per_job=0), "max_outputs_per_job"),
        (lambda s: replace(s, params=(ParamSpec(name="mystery", kind="text"),)), "neither canonical"),
        (lambda s: replace(s, params=(ParamSpec(name="seed", kind="enum"),)), "no values"),
        (lambda s: replace(s, params=(ParamSpec(name="steps", kind="range", minimum=5, maximum=1),)), "invalid span"),
        (lambda s: replace(s, params=(ParamSpec(name="steps", kind="range", minimum=1, maximum=5, default=9),)), "outside its span"),
        (lambda s: replace(s, params=(ParamSpec(name="seed", kind="text"), ParamSpec(name="seed", kind="text"))), "duplicate"),
        (lambda s: replace(s, pricing=(PriceLine(unit="image", usd=Decimal("-1")),)), "negative price"),
    ],
)
def test_spec_problems_reports_each_defect(change, needle):
    problems = spec_problems(change(fake_specs()[0]))
    assert any(needle in problem for problem in problems), problems


def test_provider_installs_its_error_mapper_on_the_http_client():
    http = CloudHttp("http://127.0.0.1:1")
    provider = FakeCloudProvider(FakeCloudConfig(id="a", name="b"), http, clock=None)
    assert http._error_mapper == provider.map_error


def test_for_provider_reads_url_headers_and_parallelism_from_the_provider():
    class Provider(FakeCloudProvider):
        @classmethod
        def api_base_url(cls, config):
            return "http://127.0.0.1:9/api/"

        @classmethod
        def auth_headers(cls, config):
            return {"Authorization": "Bearer t0k3n-value"}

    http = CloudHttp.for_provider(Provider, FakeCloudConfig(id="a", name="b", max_parallel=7))
    assert http.base_url == "http://127.0.0.1:9/api"
    assert http.owns_url("http://127.0.0.1:9/elsewhere")
    assert not http.owns_url("http://127.0.0.1:10/elsewhere")
    assert not http.owns_url("https://127.0.0.1:9/elsewhere")
    assert http._slots._value == 7
