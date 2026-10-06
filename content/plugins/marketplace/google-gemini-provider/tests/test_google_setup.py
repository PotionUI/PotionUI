from pathlib import Path

import pytest
import yaml

from src.plugin_api.cloud import CLOUD_BLOCKS

from backend.config import GoogleConfig

from .fixtures import KEY

ROOT = Path(__file__).resolve().parents[1]
PRESETS = ROOT / "presets"
FORMS = sorted(PRESETS.glob("*/standard/modes/*/form.yml"))
MESSAGE = "Add your Google AI Studio API key."


def _manifest():
    return yaml.safe_load((ROOT / "manifest.yml").read_text(encoding="utf-8"))


def _tabs(form_path):
    form = yaml.safe_load(form_path.read_text(encoding="utf-8"))
    return next(field for field in form["fields"] if field["type"] == "tabs")["children"]


def settings(**overrides):
    return {"id": "google-1", "name": "Google", "engine": "cloud", "driver": "cloud.google", **overrides}


def test_setup_walks_from_backend_to_assigned_presets():
    assert [step["kind"] for step in _manifest()["setup"]] == [
        "backend.added",
        "cloud.models_enabled",
        "presets.installed",
        "presets.assigned",
    ]


def test_setup_steps_and_presets_name_the_driver_the_backend_registers():
    driver = GoogleConfig.model_fields["driver"].default
    presets = [yaml.safe_load(path.read_text(encoding="utf-8")) for path in sorted(PRESETS.glob("*/standard/preset.yml"))]

    assert {step["driver"] for step in _manifest()["setup"] if "driver" in step} == {driver}
    assert [(preset["engine"], preset["driver"]) for preset in presets] == [("cloud", driver), ("cloud", driver)]
    assert sorted(preset["category"] for preset in presets) == ["image", "video"]


def test_the_key_is_a_required_secret_in_the_form_schema():
    spec = next(field for field in GoogleConfig.engine_fields() if field["name"] == "api_key")

    assert spec["required"] is True and spec["secret"] is True


@pytest.mark.parametrize("key", [None, "", "   "])
def test_a_config_without_a_key_is_refused_in_plain_words(key):
    data = settings()
    if key is not None:
        data["api_key"] = key

    with pytest.raises(ValueError, match=MESSAGE):
        GoogleConfig(**data)


def test_a_valid_key_is_accepted_and_stripped():
    assert GoogleConfig(**settings(api_key=f"  {KEY}  ")).api_key == KEY


def test_the_config_offers_no_user_id_setting():
    assert not [name for name in GoogleConfig.model_fields if "user" in name]


def test_presets_reference_only_shared_blocks_that_exist():
    referenced = []
    for path in PRESETS.rglob("*.yml"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if "paths._shared }}/cloud/" in line:
                referenced.append(line.split("/cloud/", 1)[1].strip().strip("\"'"))

    assert referenced and set(referenced) <= set(CLOUD_BLOCKS)


def test_every_mode_has_icon_tabs_and_the_provider_options_tab():
    assert len(FORMS) == 4
    for form_path in FORMS:
        tabs = _tabs(form_path)
        assert all(tab["configuration"]["icon_display"] == "icon_only" for tab in tabs)
        options = next(tab for tab in tabs if tab["label"] == "Provider options")
        assert options.get("audience", "simple") == "simple"
        assert options["children"].endswith("/cloud/tabs/provider_options.yml")


def test_the_image_edit_mode_has_a_references_tab():
    tabs = _tabs(PRESETS / "ImageGeneration/standard/modes/edit/form.yml")

    references = next(tab for tab in tabs if tab["label"] == "References")
    assert references["children"].endswith("/cloud/tabs/references_edit.yml")


def test_the_generation_tabs_hold_an_image_section_and_a_models_section():
    for mode, models_block in (("txt2img", "image.yml"), ("edit", "image_edit.yml")):
        tabs = _tabs(PRESETS / f"ImageGeneration/standard/modes/{mode}/form.yml")
        assert tabs[0]["children"].endswith(f"/cloud/tabs/{models_block}")
    for mode in ("txt2video", "img2video"):
        sections = yaml.safe_load((PRESETS / f"VideoGeneration/standard/modes/{mode}/tabs/generation.yml").read_text(encoding="utf-8"))["fields"]
        assert [(section["type"], section["label"]) for section in sections] == [("section", "Video"), ("section", "Models")]
        assert sections[1]["children"].endswith(f"/cloud/models/{mode}.yml")
