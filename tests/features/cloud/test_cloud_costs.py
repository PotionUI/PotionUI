import json
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.features.cloud.contracts import CloudModelSpec, ParamSpec, PriceLine
from src.features.cloud.cost_repository import GenerationCostRepository
from src.features.cloud.costs import estimate
from src.features.cloud.testing.fake import fake_specs
from src.features.generation.handlers.cost_handler import CostGenerationOutputHandler
from src.features.generation.output_types import output_type_registry
from src.features.generation.records import Generation
from src.features.generation.repository import generation_repo
from src.features.generation.routes import GenerationController
from src.features.stats.routes import StatsController, build_router as build_stats_router
from src.pipelines.outputs import CostGenerationOutput
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User
from tests.features.cloud.conftest import returning

IMAGE = "fake/image-1"
VIDEO = "fake/video-1"


def spec_with(*lines, params=()):
    return replace(fake_specs()[0], pricing=tuple(lines), params=params)


def test_a_per_request_price_is_charged_once_whatever_the_count():
    result = estimate(spec_with(PriceLine("request", Decimal("0.10"))), "txt2img", {}, count=4, outputs=4)

    assert result.amount_usd == Decimal("0.10")


def test_a_per_image_price_is_charged_for_each_image_produced():
    result = estimate(spec_with(PriceLine("image", Decimal("0.04"))), "txt2img", {}, count=4, outputs=3)

    assert result.amount_usd == Decimal("0.12")
    assert result.detail["lines"] == [{"unit": "image", "usd": "0.04", "quantity": "3", "amount": "0.12"}]


def test_the_requested_count_is_used_when_the_output_count_is_unknown():
    assert estimate(spec_with(PriceLine("image", Decimal("0.04"))), "txt2img", {}, count=2).amount_usd == Decimal("0.08")


def test_a_per_second_price_uses_the_requested_duration():
    result = estimate(spec_with(PriceLine("second", Decimal("0.40"))), "txt2video", {"duration_s": 5}, count=1, outputs=1)

    assert result.amount_usd == Decimal("2.00")


def test_a_per_second_price_is_charged_for_every_clip_produced():
    result = estimate(spec_with(PriceLine("second", Decimal("0.40"))), "txt2video", {"duration_s": 5}, count=2, outputs=2)

    assert result.amount_usd == Decimal("4.00")


def test_every_resolution_tier_has_its_own_megapixel_size():
    line = PriceLine("megapixel", Decimal("1"))

    tiers = {tier: estimate(spec_with(line), "txt2img", {"resolution": tier}, count=1).amount_usd for tier in ("512", "1K", "2K", "4K")}

    assert tiers == {
        "512": Decimal("0.262144"), "1K": Decimal("1.048576"), "2K": Decimal("4.194304"), "4K": Decimal("16.777216"),
    }


def test_a_per_second_price_falls_back_to_the_models_default_duration():
    spec = spec_with(PriceLine("second", Decimal("0.40")), params=(ParamSpec(name="duration_s", kind="range", minimum=1, maximum=10, default=4),))

    assert estimate(spec, "txt2video", {}, count=1).amount_usd == Decimal("1.60")


def test_a_per_second_price_cannot_be_estimated_without_any_duration():
    assert estimate(spec_with(PriceLine("second", Decimal("0.40"))), "txt2video", {}, count=1) is None


def test_a_per_megapixel_price_uses_the_size_or_the_resolution_tier():
    line = PriceLine("megapixel", Decimal("0.02"))

    by_size = estimate(spec_with(line), "txt2img", {"size": "1000x2000"}, count=1)
    by_tier = estimate(spec_with(line), "txt2img", {"resolution": "1K"}, count=2)

    assert by_size.amount_usd == Decimal("0.04")
    assert by_tier.amount_usd == Decimal("0.02") * Decimal("1.048576") * 2
    assert estimate(spec_with(line), "txt2img", {}, count=1) is None


def test_token_and_sku_prices_cannot_be_estimated_and_are_reported_as_skipped():
    spec = spec_with(PriceLine("token", Decimal("0.001")), PriceLine("image", Decimal("0.04")))

    result = estimate(spec, "txt2img", {}, count=1)

    assert result.amount_usd == Decimal("0.04")
    assert result.detail["skipped_units"] == ["token"]
    assert estimate(spec_with(PriceLine("sku", Decimal("1"))), "txt2img", {}, count=1) is None


def test_a_price_line_limited_to_a_tier_only_applies_to_that_tier():
    spec = spec_with(
        PriceLine("image", Decimal("0.04"), applies_to="1K"),
        PriceLine("image", Decimal("0.08"), applies_to="2K"),
        PriceLine("request", Decimal("0.01")),
    )

    assert estimate(spec, "txt2img", {"resolution": "2K"}, count=1).amount_usd == Decimal("0.09")
    assert estimate(spec, "txt2img", {"resolution": "1k"}, count=2).amount_usd == Decimal("0.09")


def test_a_model_with_no_prices_has_no_estimate():
    assert estimate(spec_with(), "txt2img", {}, count=1) is None


@pytest.fixture
def generation(mock_db):
    with mock_db.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (id, username, email, password_hash, account_type) VALUES ('u1', 'u1', 'u1@example.test', 'x', 'USER')"
        )
    return generation_repo.create(Generation(id="g1", preset_id="p", form_data={}, user_id="u1", backend_id="cloud-1"))


async def enabled_model(env, provider_model_id=IMAGE):
    slug = env.slug_of(provider_model_id)
    await env.catalog.set_enabled("cloud-1", [slug], True)
    return slug, env.model_row(slug).id


def record(slug, **kwargs):
    handler = CostGenerationOutputHandler("g1", "u1")
    return handler.handle(CostGenerationOutput(model=slug, **kwargs))


def rows(generation_id="g1"):
    return GenerationCostRepository().for_generation(generation_id)


async def test_a_cost_the_provider_reported_is_recorded_as_reported(refreshed, generation):
    slug, model_id = await enabled_model(refreshed)

    metadata = record(slug, amount_usd=Decimal("0.0731"), source="provider", task="txt2img", count=2, outputs=2)

    (row,) = rows()
    assert metadata["processed"] is True
    assert (row["amount_usd"], row["source"], row["model_id"], row["backend_id"]) == ("0.0731", "provider", model_id, "cloud-1")
    assert row["detail"]["task"] == "txt2img" and row["detail"]["outputs"] == 2
    assert row["created_at"].endswith("+00:00")


async def test_a_missing_provider_cost_is_estimated_from_the_catalog_prices(refreshed, generation):
    slug, _ = await enabled_model(refreshed)

    record(slug, amount_usd=None, task="txt2img", count=3, outputs=3)

    (row,) = rows()
    assert (row["amount_usd"], row["source"]) == ("0.12", "estimate")
    assert row["detail"]["lines"][0]["unit"] == "image"


async def test_a_provider_cost_of_zero_is_kept_not_replaced_by_an_estimate(refreshed, generation):
    slug, _ = await enabled_model(refreshed)

    record(slug, amount_usd=Decimal("0"), source="provider", count=3, outputs=3)

    (row,) = rows()
    assert (row["amount_usd"], row["source"]) == ("0", "provider")


async def test_a_cost_that_cannot_be_estimated_is_still_recorded_as_unknown(refreshed, generation):
    bare = replace(fake_specs()[0], provider_model_id="fake/free-1", label="Free", pricing=())
    refreshed.backend().provider.discover = returning([bare])
    await refreshed.catalog.refresh("cloud-1")
    slug, _ = await enabled_model(refreshed, "fake/free-1")

    record(slug, amount_usd=None, count=1, outputs=1)

    (row,) = rows()
    assert (row["amount_usd"], row["source"]) == (None, "unknown")


async def test_several_requests_of_one_generation_add_up(refreshed, generation):
    slug, _ = await enabled_model(refreshed)

    record(slug, amount_usd=Decimal("0.04"), count=1, outputs=1)
    record(slug, amount_usd=None, count=1, outputs=1)

    summary = GenerationCostRepository().summaries(["g1"])["g1"]
    assert summary == {"amount_usd": "0.08", "source": "mixed", "entries": 2, "unpriced": 0}


async def test_a_cost_for_a_model_the_catalog_does_not_know_is_still_recorded(refreshed, generation):
    record("fake~gone", amount_usd=Decimal("1"), count=1, outputs=1)

    (row,) = rows()
    assert row["model_id"] is None and row["amount_usd"] == "1"


async def test_the_cost_survives_deleting_the_generation(refreshed, generation):
    slug, _ = await enabled_model(refreshed)
    record(slug, amount_usd=Decimal("0.04"), count=1, outputs=1)

    generation_repo.delete("g1")

    assert len(rows()) == 1
    assert GenerationCostRepository().spend()["total_usd"] == "0.04"


def test_the_cost_output_is_a_server_only_output_type_with_no_serializer():
    spec = output_type_registry.spec_for(CostGenerationOutput(model="x"))

    assert spec.key == "cost" and spec.server_only is True and spec.serializer is None
    assert spec.handler_cls is CostGenerationOutputHandler


def test_every_other_output_type_is_still_delivered_to_clients():
    assert [s.key for s in output_type_registry.all() if s.server_only] == ["cost"]


def admin_controller(refreshed):
    recorder = Mock()
    recorder.has_reports = Mock(return_value=set())
    recorder.get_report = Mock(return_value=None)
    facade = Mock()
    facade.query = Mock()
    return GenerationController(Mock(set_queue_listener=Mock()), facade, Mock(), recorder, GenerationCostRepository()), facade


async def test_the_admin_generation_list_carries_the_cost(refreshed, generation):
    slug, _ = await enabled_model(refreshed)
    record(slug, amount_usd=Decimal("0.0731"), count=1, outputs=1)
    controller, facade = admin_controller(refreshed)
    facade.get_history_async = AsyncMock(return_value={"generations": [{"id": "g1"}, {"id": "g-other"}], "total": 2})

    response = await controller.admin_list_generations()

    first, second = response.data["generations"]
    assert first["cost"] == {"amount_usd": "0.0731", "source": "provider", "entries": 1, "unpriced": 0}
    assert second["cost"] is None


async def test_the_admin_generation_detail_carries_the_cost_with_its_entries(refreshed, generation):
    slug, model_id = await enabled_model(refreshed)
    record(slug, amount_usd=Decimal("0.0731"), count=1, outputs=1)
    controller, _ = admin_controller(refreshed)

    response = await controller.admin_get_generation("g1")

    cost = response.data["cost"]
    assert cost["amount_usd"] == "0.0731" and cost["entries"] == 1
    (item,) = cost["items"]
    assert item["model_id"] == model_id and item["backend_id"] == "cloud-1" and item["source"] == "provider"


async def test_the_admin_generation_detail_has_no_cost_for_a_free_generation(refreshed, generation):
    controller, _ = admin_controller(refreshed)

    assert (await controller.admin_get_generation("g1")).data["cost"] is None


def stats_client(refreshed, account):
    controller = StatsController(Mock(), generation_cost_repository=GenerationCostRepository())
    app = FastAPI()
    app.include_router(build_stats_router(SimpleNamespace(stats_controller=controller)))
    app.dependency_overrides[get_current_active_user] = lambda: User(
        id="u1", username="u", email="u@example.test", password_hash="h", account_type=account,
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_admin_spend_is_totalled_per_backend_and_per_model(refreshed, generation, mock_db):
    image_slug, image_id = await enabled_model(refreshed)
    video_slug, video_id = await enabled_model(refreshed, VIDEO)
    record(image_slug, amount_usd=Decimal("0.04"), count=1, outputs=1)
    record(image_slug, amount_usd=Decimal("0.06"), count=1, outputs=1)
    record(video_slug, amount_usd=Decimal("0.40"), count=1, outputs=1)

    async with stats_client(refreshed, AccountType.ADMIN) as client:
        data = (await client.get("/api/stats/spend")).json()["data"]

    assert data["total_usd"] == "0.50" and data["entries"] == 3 and data["unpriced"] == 0
    (backend,) = data["by_backend"]
    assert (backend["backend_id"], backend["backend_name"], backend["amount_usd"], backend["entries"]) == (
        "cloud-1", "Fake cloud", "0.50", 3,
    )
    by_model = {item["model_id"]: item for item in data["by_model"]}
    assert by_model[image_id]["amount_usd"] == "0.10" and by_model[image_id]["model"] == "Fake Image"
    assert by_model[video_id]["amount_usd"] == "0.40"
    assert [item["model_id"] for item in data["by_model"]] == [video_id, image_id]


async def test_admin_spend_counts_unpriced_entries_separately(refreshed, generation):
    slug, _ = await enabled_model(refreshed)
    record(slug, amount_usd=Decimal("0.04"), count=1, outputs=1)
    GenerationCostRepository().record(
        "g1", backend_id="cloud-1", model_id=None, user_id="u1", amount_usd=None, source="unknown", detail={},
    )

    spend = GenerationCostRepository().spend()

    assert spend["total_usd"] == "0.04" and spend["entries"] == 2 and spend["unpriced"] == 1


async def test_admin_spend_honours_the_date_range(refreshed, generation, mock_db):
    slug, _ = await enabled_model(refreshed)
    record(slug, amount_usd=Decimal("0.04"), count=1, outputs=1)
    record(slug, amount_usd=Decimal("0.06"), count=1, outputs=1)
    with mock_db.get_cursor() as cursor:
        cursor.execute("UPDATE generation_costs SET created_at = '2026-03-01T10:00:00+00:00' WHERE amount_usd = '0.04'")
        cursor.execute("UPDATE generation_costs SET created_at = '2026-03-03T23:59:59+00:00' WHERE amount_usd = '0.06'")

    async with stats_client(refreshed, AccountType.ADMIN) as client:
        whole = (await client.get("/api/stats/spend", params={"from": "2026-03-01", "to": "2026-03-03"})).json()["data"]
        first = (await client.get("/api/stats/spend", params={"from": "2026-03-01", "to": "2026-03-01"})).json()["data"]
        late = (await client.get("/api/stats/spend", params={"from": "2026-03-02"})).json()["data"]
        none = (await client.get("/api/stats/spend", params={"to": "2026-02-28"})).json()["data"]

    assert whole["total_usd"] == "0.10" and first["total_usd"] == "0.04" and late["total_usd"] == "0.06"
    assert none["total_usd"] == "0" and none["by_backend"] == [] and none["by_model"] == []


async def test_a_bad_date_is_a_400(refreshed):
    async with stats_client(refreshed, AccountType.ADMIN) as client:
        response = await client.get("/api/stats/spend", params={"from": "yesterday"})

    assert response.status_code == 400


async def test_a_regular_user_is_refused_the_spend_endpoint(refreshed, generation):
    slug, _ = await enabled_model(refreshed)
    record(slug, amount_usd=Decimal("0.0731"), count=1, outputs=1)

    async with stats_client(refreshed, AccountType.USER) as client:
        response = await client.get("/api/stats/spend")

    assert response.status_code == 403
    assert "0.0731" not in json.dumps(response.json())


async def test_the_model_picker_and_model_rows_carry_no_cost_even_after_money_was_spent(refreshed, generation):
    from src.features.models.availability import models_for_engine
    from src.features.models.repository import model_repo

    slug, model_id = await enabled_model(refreshed)
    record(slug, amount_usd=Decimal("0.0731"), count=1, outputs=1)

    for admin in (False, True):
        picker = models_for_engine("cloud", refreshed.registry, model_type="cloud", admin=admin)
        text = json.dumps([picker, model_repo.get_by_id(model_id).to_dict(admin=admin)], default=str)
        assert "0.0731" not in text and "amount_usd" not in text


async def test_a_failing_lookup_still_leaves_a_fallback_row_with_the_reason(refreshed, generation, monkeypatch):
    slug, _ = await enabled_model(refreshed)
    from src.features.models import repository as models_repository

    def broken(*args, **kwargs):
        raise RuntimeError("catalog lookup exploded")

    monkeypatch.setattr(models_repository.model_repo, "get_by_identity", broken)

    metadata = record(slug, amount_usd=Decimal("0.0731"), source="provider", task="txt2img", count=2, outputs=2)

    (row,) = rows()
    assert metadata["processed"] is False
    assert (row["amount_usd"], row["source"], row["model_id"], row["backend_id"]) == (None, "unknown", None, "cloud-1")
    assert row["detail"]["model"] == slug
    assert "catalog lookup exploded" in row["detail"]["error"]
    assert row["detail"]["provider_amount_usd"] == "0.0731"


async def test_a_failing_estimate_still_leaves_a_fallback_row(refreshed, generation, monkeypatch):
    slug, _ = await enabled_model(refreshed)
    import src.features.generation.handlers.cost_handler as cost_handler

    monkeypatch.setattr(cost_handler, "estimate", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad price table")))

    record(slug, amount_usd=None, count=1, outputs=1)

    (row,) = rows()
    assert row["source"] == "unknown" and "bad price table" in row["detail"]["error"]


async def test_a_database_that_refuses_the_write_is_logged_with_the_amount_and_does_not_fail_the_generation(
    refreshed, generation, monkeypatch, caplog
):
    slug, _ = await enabled_model(refreshed)

    def refuse(self, *args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(GenerationCostRepository, "record", refuse)

    with caplog.at_level("ERROR"):
        metadata = record(slug, amount_usd=Decimal("0.0731"), source="provider", count=1, outputs=1)

    assert metadata["processed"] is False
    message = next(r.getMessage() for r in caplog.records if "was not recorded at all" in r.getMessage())
    assert "generation g1" in message
    assert "0.0731" in message and slug in message and "disk full" in message


async def test_the_fallback_row_is_never_written_when_the_full_record_succeeded(refreshed, generation):
    slug, _ = await enabled_model(refreshed)

    record(slug, amount_usd=Decimal("0.04"), count=1, outputs=1)

    assert len(rows()) == 1 and rows()[0]["source"] == "provider"
