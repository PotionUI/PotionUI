from pathlib import Path

import pytest
import yaml

from src.plugin_api.cloud import CLOUD_BLOCKS

from backend.config import REGIONS, BflConfig
from backend.provider import BflProvider

from .fixtures import KEY

PLUGIN = Path(__file__).resolve().parents[1]
PRESET = PLUGIN / "presets" / "ImageGeneration" / "standard"
MESSAGE = "Add your BFL API key."


def settings(**overrides):
    return {"id": "bfl-1", "name": "BFL", "engine": "cloud", "driver": "cloud.bfl", "api_key": KEY, **overrides}


def test_the_key_is_a_required_secret_in_the_form_schema():
    fields = {field["name"]: field for field in BflConfig.engine_fields()}

    assert fields["api_key"]["required"] is True and fields["api_key"]["secret"] is True
    assert fields["region"]["options"] == ["global", "eu", "us"] and fields["region"]["default"] == "global"
    assert fields["send_user_hash"]["default"] is False
    assert fields["max_parallel"]["default"] == 4


@pytest.mark.parametrize("key", [None, "", "   "])
def test_a_config_without_a_key_is_refused_in_plain_words(key):
    data = settings()
    data.pop("api_key")
    if key is not None:
        data["api_key"] = key

    with pytest.raises(ValueError, match=MESSAGE):
        BflConfig(**data)


def test_a_valid_key_is_accepted_and_stripped():
    assert BflConfig(**settings(api_key=f"  {KEY}  ")).api_key == KEY


@pytest.mark.parametrize("region,address", [
    ("global", "https://api.bfl.ai/v1"), ("eu", "https://api.eu.bfl.ai/v1"), ("us", "https://api.us.bfl.ai/v1"), ("EU", "https://api.eu.bfl.ai/v1"),
])
def test_the_region_picks_the_documented_endpoint(region, address):
    config = BflConfig(**settings(region=region))

    assert BflProvider.api_base_url(config) == address


def test_an_api_address_overrides_the_region_for_a_proxy():
    config = BflConfig(**settings(region="eu", base_url=" https://proxy.example/v1/ "))

    assert BflProvider.api_base_url(config) == "https://proxy.example/v1"


@pytest.mark.parametrize("overrides", [{"region": "asia"}, {"base_url": "proxy.example"}, {"base_url": "ftp://proxy.example"}])
def test_an_unknown_region_or_address_is_refused_at_save(overrides):
    with pytest.raises(ValueError):
        BflConfig(**settings(**overrides))


def test_the_auth_header_is_x_key():
    assert BflProvider.auth_headers(BflConfig(**settings())) == {"x-key": KEY}
    assert set(REGIONS) == {"global", "eu", "us"}


def test_the_driver_and_key_match_the_registration_rules():
    assert BflConfig.model_fields["driver"].default == f"cloud.{BflProvider.key}" == "cloud.bfl"


def test_registration_adds_the_cloud_bfl_driver():
    from backend.hooks.backend_hooks import register_backend

    class Context:
        data = {}

    register_backend(Context())

    assert Context.data["cloud_providers"]["cloud.bfl"] is BflProvider
    assert Context.data["config_types"]["cloud.bfl"] is BflConfig


def _manifest():
    return yaml.safe_load((PLUGIN / "manifest.yml").read_text(encoding="utf-8"))


def test_setup_walks_from_backend_to_assigned_presets_on_this_driver():
    setup = _manifest()["setup"]

    assert [step["kind"] for step in setup] == ["backend.added", "cloud.models_enabled", "presets.installed", "presets.assigned"]
    assert {step["driver"] for step in setup if "driver" in step} == {"cloud.bfl"}


def _form(mode):
    return yaml.safe_load((PRESET / "modes" / mode / "form.yml").read_text(encoding="utf-8"))


def _tabs(mode):
    tabs = next(field for field in _form(mode)["fields"] if field["type"] == "tabs")
    return {tab["label"]: tab for tab in tabs["children"]}


def test_the_preset_is_a_cloud_preset_on_this_driver():
    preset = yaml.safe_load((PRESET / "preset.yml").read_text(encoding="utf-8"))

    assert (preset["engine"], preset["driver"], preset["modes"]) == ("cloud", "cloud.bfl", ["txt2img", "edit"])


def test_the_presets_reference_only_known_shared_blocks():
    lines = [line for path in PRESET.rglob("*.yml") for line in path.read_text(encoding="utf-8").splitlines()]
    blocks = [line.split("/cloud/", 1)[1].strip().strip("\"'") for line in lines if "paths._shared }}/cloud/" in line]

    assert blocks and all(block in CLOUD_BLOCKS for block in blocks)


def test_txt2img_follows_the_layout_standard_with_the_seed_in_the_image_section():
    tabs = _tabs("txt2img")

    assert list(tabs) == ["Generation", "Provider options"]
    assert tabs["Generation"]["configuration"]["icon_display"] == "icon_only"
    assert tabs["Generation"]["children"].endswith("/cloud/tabs/image.yml")
    image_tab = yaml.safe_load((PLUGIN.parents[2] / "presets" / "_shared" / "cloud" / "tabs" / "_image_params.yml").read_text(encoding="utf-8"))
    seed = next(field for row in image_tab["fields"] if row.get("type") == "row" for field in row["children"] if field["name"] == "seed")
    assert seed["type"] == "seed" and "capability" not in seed


def test_edit_has_a_references_tab_bound_to_the_reference_input():
    tabs = _tabs("edit")

    assert list(tabs) == ["Generation", "References", "Provider options"]
    assert tabs["References"]["children"].endswith("/cloud/tabs/references_edit.yml")
    assert tabs["Generation"]["children"].endswith("/cloud/tabs/image_edit.yml")


@pytest.mark.parametrize("mode,task", [("txt2img", "txt2img"), ("edit", "img_edit")])
def test_each_mode_runs_cloud_generate_with_its_task_and_the_seed(mode, task):
    pipeline = yaml.safe_load((PRESET / "modes" / mode / "pipeline.yml").read_text(encoding="utf-8"))["pipeline"]
    cloud = next(pipe for pipe in pipeline if pipe["name"] == "cloud_generate")

    assert cloud["configuration"]["task"] == task
    assert ["seed", "seed_generator", "seed"] in cloud["input"]
    assert set(cloud["configuration"]["params"]) == {"aspect_ratio", "resolution", "output_format"}
