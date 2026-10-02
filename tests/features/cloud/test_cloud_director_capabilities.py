from dataclasses import replace
from decimal import Decimal

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.features.cloud.contracts import MediaInputSpec, ParamSpec, PriceLine
from src.features.cloud.director import DirectorEstimateError, allowed_durations, director_overlay, estimate_shots
from src.features.cloud.routes import build_router
from src.features.cloud.spec_codec import spec_from_json, spec_to_json
from src.features.cloud.testing.fake import fake_director_specs, fake_specs
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User
from tests.features.cloud.conftest import returning

FULL, START, TEXT = fake_director_specs()


def user(account_type):
    return User(id="u1", username="u1", email="u1@example.test", password_hash="h", account_type=account_type)


def client_as(container, account_type):
    app = FastAPI()
    app.include_router(build_router(container))
    app.dependency_overrides[get_current_active_user] = lambda: user(account_type)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def director_models(fake_backend):
    fake_backend.backend().provider.discover = returning([*fake_specs(), *fake_director_specs()])
    await fake_backend.catalog.refresh("cloud-1")
    ids = {}
    for spec in fake_director_specs():
        slug = fake_backend.slug_of(spec.provider_model_id)
        await fake_backend.catalog.set_enabled("cloud-1", [slug], True)
        ids[spec.provider_model_id] = fake_backend.model_row(slug).id
    return ids


def test_a_model_with_start_and_end_frames_keeps_every_shape_and_lists_its_lengths():
    overlay = director_overlay(FULL)

    assert overlay["limits"] == {"durations": [2, 4, 6], "default_duration": 4, "max_duration": 6, "default_fps": 24}
    assert overlay["modes"] == {"director": {"max_frames_per_segment": 144, "max_segments": 4}}
    assert overlay["model_label"] == "Fake Director"


def test_a_model_without_an_end_frame_drops_first_last_frame_only():
    overlay = director_overlay(START)

    assert overlay["modes"]["flf"] is None
    assert "i2v" not in overlay["modes"] and "keyframes" not in overlay["modes"]["director"]
    assert overlay["limits"]["durations"] == [3, 5] and overlay["limits"]["default_duration"] == 3


def test_a_text_only_model_gets_no_start_or_end_picture_and_no_continuation():
    overlay = director_overlay(TEXT)

    assert overlay["modes"]["i2v"] is None and overlay["modes"]["flf"] is None
    director = overlay["modes"]["director"]
    assert director["keyframes"] is None and director["continuation"] is None
    assert overlay["limits"]["durations"] == [5]


def test_an_image_to_video_only_model_cannot_open_on_a_prompt():
    image_only = replace(FULL, tasks=frozenset({"img2video"}))

    assert director_overlay(image_only)["modes"]["t2v"] is None


def test_a_length_range_is_listed_step_by_step():
    ranged = replace(FULL, params=(ParamSpec(name="duration_s", kind="range", minimum=2, maximum=10, step=4, default=6),))

    assert allowed_durations(ranged) == [2, 6, 10]
    assert director_overlay(ranged)["limits"]["default_duration"] == 6


def test_a_model_that_names_no_lengths_leaves_the_presets_limits_alone():
    no_lengths = replace(FULL, params=())

    overlay = director_overlay(no_lengths)

    assert "limits" not in overlay
    assert overlay["modes"] == {"director": {"max_segments": 4}}


def test_what_the_model_declares_wins_over_what_is_derived():
    declared = replace(START, director={
        "limits": {"default_fps": 30},
        "modes": {"director": {"continuation": None}, "i2v": None},
        "model_label": "Shown name",
    })

    overlay = director_overlay(declared)

    assert overlay["limits"]["default_fps"] == 30 and overlay["limits"]["durations"] == [3, 5]
    assert overlay["modes"]["director"] == {"max_frames_per_segment": 150, "continuation": None}
    assert overlay["modes"]["i2v"] is None and overlay["model_label"] == "Shown name"


def test_image_models_have_no_director_overlay():
    assert director_overlay(fake_specs()[0]) is None
    assert director_overlay(None) is None


def test_the_declared_overlay_survives_the_catalog():
    assert spec_from_json(spec_to_json(FULL)).director == {"modes": {"director": {"max_segments": 4}}}


def test_every_shot_is_priced_from_its_own_length():
    result = estimate_shots(FULL, [
        {"task": "txt2video", "params": {"duration_s": 4}},
        {"task": "img2video", "params": {"duration_s": 6}},
    ])

    assert result["known"] is True and Decimal(result["total_usd"]) == Decimal("1.00")
    assert [Decimal(shot["amount_usd"]) for shot in result["shots"]] == [Decimal("0.40"), Decimal("0.60")]
    assert result["message"] is None and result["shot_count"] == 2


def test_a_per_request_price_counts_once_per_shot():
    result = estimate_shots(START, [{"params": {"duration_s": 3}}] * 3)

    assert Decimal(result["total_usd"]) == Decimal("0.75")


def test_an_unknown_price_is_never_invented():
    result = estimate_shots(TEXT, [{"task": "txt2video", "params": {"duration_s": 5}}])

    assert result["known"] is False and result["total_usd"] is None
    assert result["message"] == "This model's price is not known, so the cost cannot be estimated."
    assert result["shots"][0]["amount_usd"] is None


def test_a_price_line_that_cannot_be_worked_out_makes_the_estimate_partial():
    mixed = replace(FULL, pricing=(PriceLine(unit="second", usd=Decimal("0.10")), PriceLine(unit="token", usd=Decimal("0.01"))))

    result = estimate_shots(mixed, [{"params": {"duration_s": 2}}])

    assert result["known"] is False and Decimal(result["total_usd"]) == Decimal("0.20")
    assert result["message"].startswith("Part of this model's price is not known")


def test_a_bad_estimate_request_is_refused_plainly():
    with pytest.raises(DirectorEstimateError):
        estimate_shots(FULL, [])
    with pytest.raises(DirectorEstimateError):
        estimate_shots(FULL, [{"task": "dance"}])


async def test_the_capabilities_endpoint_carries_the_models_director_overlay(director_models, container):
    async with client_as(container, AccountType.ADMIN) as client:
        video = (await client.get(f"/api/cloud/models/{director_models['fake/director-text-1']}/capabilities")).json()["data"]

    assert video["video_director"]["modes"]["flf"] is None
    assert video["video_director"]["limits"]["durations"] == [5]


async def test_an_admin_gets_a_cost_estimate_for_a_film(director_models, container):
    body = {"shots": [{"task": "txt2video", "params": {"duration_s": 2}}, {"task": "img2video", "params": {"duration_s": 4}}]}

    async with client_as(container, AccountType.ADMIN) as client:
        response = await client.post(f"/api/cloud/models/{director_models['fake/director-1']}/estimate", json=body)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["known"] is True and Decimal(data["total_usd"]) == Decimal("0.60")
    assert data["model_id"] == director_models["fake/director-1"]


async def test_a_regular_user_cannot_see_an_estimate(director_models, container):
    async with client_as(container, AccountType.USER) as client:
        response = await client.post(
            f"/api/cloud/models/{director_models['fake/director-1']}/estimate", json={"shots": [{"params": {}}]},
        )

    assert response.status_code == 403


async def test_an_estimate_for_an_unknown_model_or_a_bad_shot_is_refused(director_models, container):
    async with client_as(container, AccountType.ADMIN) as client:
        missing = await client.post("/api/cloud/models/nope/estimate", json={"shots": [{"params": {}}]})
        bad = await client.post(
            f"/api/cloud/models/{director_models['fake/director-1']}/estimate", json={"shots": [{"task": "dance"}]},
        )
        empty = await client.post(f"/api/cloud/models/{director_models['fake/director-1']}/estimate", json={"shots": []})

    assert missing.status_code == 404
    assert bad.status_code == 400 and "unknown task" in bad.json()["detail"]["message"]
    assert empty.status_code == 422
