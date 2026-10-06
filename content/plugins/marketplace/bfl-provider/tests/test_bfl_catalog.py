import re
from decimal import Decimal

import pytest

from src.plugin_api.cloud import spec_problems

from backend.catalog import load_catalog, model_spec
from backend.provider import BflProvider

ALL_MODELS = {
    "flux-3-image", "flux-2-pro-preview", "flux-2-pro", "flux-2-max", "flux-2-flex",
    "flux-2-klein-9b-preview", "flux-2-klein-9b", "flux-2-klein-4b",
    "flux-kontext-pro", "flux-kontext-max", "flux-pro-1.1-ultra", "flux-pro-1.1", "flux-dev",
}
TEXT_ONLY = {"flux-pro-1.1-ultra", "flux-pro-1.1", "flux-dev"}


def documented_slug(provider_key, provider_model_id):
    body = provider_model_id.lower().replace("/", "~")
    return re.sub(r"[^a-z0-9._~-]", "", f"{provider_key}~{body}")


def params(spec):
    return {param.name: param for param in spec.params}


async def test_the_catalog_lists_every_documented_model_without_touching_the_network(provider, bfl):
    specs = await provider.discover()

    assert {spec.provider_model_id for spec in specs} == ALL_MODELS
    assert all(spec_problems(spec) == [] for spec in specs)
    assert bfl.requests == []


async def test_catalog_task_filtering_keeps_edit_to_the_models_that_take_pictures(provider):
    specs = await provider.discover()

    txt2img = {spec.provider_model_id for spec in specs if "txt2img" in spec.tasks}
    edit = {spec.provider_model_id for spec in specs if "img_edit" in spec.tasks}

    assert txt2img == ALL_MODELS
    assert edit == ALL_MODELS - TEXT_ONLY
    assert {"flux-kontext-pro", "flux-kontext-max"} <= edit
    for spec in specs:
        roles = {media.role: media for media in spec.inputs}
        if spec.provider_model_id in TEXT_ONLY:
            assert roles == {}
        else:
            assert roles["reference"].tasks == {"img_edit"} and roles["reference"].min_items == 1


@pytest.mark.parametrize("model_id,limit", [
    ("flux-kontext-pro", 4), ("flux-kontext-max", 4), ("flux-2-pro", 8), ("flux-2-flex", 8),
    ("flux-2-klein-4b", 4), ("flux-3-image", 10),
])
async def test_reference_limits_follow_the_docs(provider, model_id, limit):
    spec = next(spec for spec in await provider.discover() if spec.provider_model_id == model_id)

    assert spec.inputs[0].max_items == limit


async def test_slugs_are_the_provider_key_and_the_endpoint_name(provider):
    specs = await provider.discover()
    slugs = {documented_slug(BflProvider.key, spec.provider_model_id) for spec in specs}

    assert len(slugs) == len(specs)
    assert documented_slug(BflProvider.key, "flux-kontext-pro") == "bfl~flux-kontext-pro"
    for spec in specs:
        assert documented_slug(BflProvider.key, spec.provider_model_id) == f"bfl~{spec.provider_model_id}"
        assert "/" not in spec.provider_model_id


async def test_the_controls_follow_each_model_family(provider):
    specs = {spec.provider_model_id: spec for spec in await provider.discover()}

    flux2 = params(specs["flux-2-pro"])
    assert flux2["aspect_ratio"].values[0] == "auto" and flux2["aspect_ratio"].default == "auto"
    assert flux2["resolution"].values == ("1K", "2K")
    assert flux2["enhance_prompt"].default is True
    assert (flux2["x.safety_tolerance"].minimum, flux2["x.safety_tolerance"].maximum, flux2["x.safety_tolerance"].default) == (0, 5, 2)
    assert "guidance" not in flux2 and "steps" not in flux2

    flex = params(specs["flux-2-flex"])
    assert (flex["guidance"].minimum, flex["guidance"].maximum, flex["guidance"].integer) == (1.5, 10.0, False)
    assert (flex["steps"].maximum, flex["steps"].default) == (50.0, 50.0)

    kontext = params(specs["flux-kontext-pro"])
    assert "resolution" not in kontext and kontext["output_format"].default == "png"
    assert kontext["x.safety_tolerance"].maximum == 6

    ultra = params(specs["flux-pro-1.1-ultra"])
    assert ultra["x.raw"].kind == "boolean" and ultra["aspect_ratio"].default == "1:1" and "auto" not in ultra["aspect_ratio"].values

    flux3 = params(specs["flux-3-image"])
    assert flux3["resolution"].values == ("768sq", "1k", "1.5k", "2k", "4k")
    assert "output_format" not in flux3 and "enhance_prompt" not in flux3
    assert flux3["x.grounding"].default is True and flux3["x.safety_tolerance"].maximum == 4

    klein = params(specs["flux-2-klein-9b"])
    assert "enhance_prompt" not in klein


async def test_every_job_makes_one_picture(provider):
    assert {spec.max_outputs_per_job for spec in await provider.discover()} == {1}


async def test_prices_are_the_documented_amounts(provider):
    specs = {spec.provider_model_id: spec for spec in await provider.discover()}

    assert [(line.unit, line.usd) for line in specs["flux-kontext-pro"].pricing] == [("image", Decimal("0.04"))]
    assert [(line.unit, line.usd) for line in specs["flux-kontext-max"].pricing] == [("image", Decimal("0.08"))]
    assert {line.applies_to: line.usd for line in specs["flux-3-image"].pricing} == {
        "768sq": Decimal("0.041"), "1k": Decimal("0.048"), "2k": Decimal("0.100"), "4k": Decimal("0.607"),
    }
    assert specs["flux-dev"].pricing == ()


def test_a_malformed_entry_is_skipped():
    assert model_spec({"id": "x", "shape": "mystery"}) is None
    assert model_spec("garbage") is None
    assert model_spec({"shape": "flux3"}) is None


def test_suggested_models_are_in_the_catalog():
    data = load_catalog()

    assert set(data["suggested"]) <= {item["id"] for item in data["models"]}
    assert data["checked"] == "2026-10-06"


async def test_suggestions_come_from_the_catalog_file(provider):
    assert provider.suggested_model_ids() == ("flux-2-pro-preview", "flux-kontext-pro", "flux-2-klein-9b-preview")
