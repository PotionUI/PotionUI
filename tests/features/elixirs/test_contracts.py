from pathlib import Path

import pytest
from pydantic import ValidationError

from src.features.elixirs.catalog import installed_catalog
from src.features.elixirs.contracts import (
    lint_contract,
    lint_contracts,
    load_contracts,
    parse_contracts,
)

KREA = "4TK1KBQZ2XMB8ME0PTMXS1YJQP"
H3 = "01KX47H3MINIMAX000000000VA"
REPO_ROOT = Path(__file__).resolve().parents[3]


def document(mode_contract, preset_id=KREA, mode="txt2img"):
    mode_contract = {"label": "Mode", **mode_contract}
    return {"schema_version": 1, "presets": {preset_id: {"name": "Krea-2", "modes": {mode: mode_contract}}}}


def images_out():
    return {"images": {"label": "Final images", "media_type": "image"}}


def test_shipped_contracts_cover_the_proposal_examples():
    contracts = load_contracts()

    photo = contracts[(KREA, "txt2img")]
    video = contracts[(H3, "video")]
    assert photo.outputs["images"].media_type == "image"
    assert photo.outputs["images"].selector == {"is_final": True, "is_derived": False}
    assert video.inputs["first_frame"].target.kind == "video_director_role"
    assert video.inputs["first_frame"].target.role == "first"
    assert video.inputs["last_frame"].target.role == "last"
    assert video.outputs["videos"].media_type == "video"


@pytest.fixture(scope="module")
def marketplace_catalog():
    from src.features.presets.loader import PresetTemplateLoader

    loader = PresetTemplateLoader([str(REPO_ROOT / "content" / "presets" / "marketplace")])
    loader._ensure_loaded()
    from src.features.presets.file_repository import FilePresetRepository

    return installed_catalog(FilePresetRepository(loader), [*(template.id for template in loader.presets), "NOT-INSTALLED"])


def test_shipped_contracts_match_the_installed_presets(marketplace_catalog):
    assert lint_contracts(load_contracts(), marketplace_catalog) == []


def test_the_catalog_reads_modes_media_fields_and_director_modes(marketplace_catalog):
    krea = marketplace_catalog[KREA]
    h3 = marketplace_catalog[H3]

    assert {"txt2img", "enhance"} <= set(krea["modes"])
    enhance = krea["modes"]["enhance"]
    assert enhance["forms"][enhance["default_form"]]["source_image"] == {"type": "image", "required": True, "multi": False}
    assert "video" in h3["director_modes"]
    assert {"i2v", "flf"} <= set(h3["director_shapes"]["video"])
    assert krea["director_modes"] == []
    assert "NOT-INSTALLED" not in marketplace_catalog


@pytest.mark.parametrize(
    "contract, problem",
    [
        ({"prompt": True, "quantity_field": "count", "outputs": images_out()}, "no count field"),
        (
            {
                "prompt": True,
                "inputs": {"photo": {"label": "Photo", "media_type": "image", "target": {"kind": "form_field", "field": "nope"}}},
                "outputs": images_out(),
            },
            "no nope field",
        ),
        (
            {
                "prompt": True,
                "inputs": {"clip": {"label": "Clip", "media_type": "video", "target": {"kind": "form_field", "field": "source_image"}}},
                "outputs": images_out(),
            },
            "does not take video media",
        ),
        (
            {
                "prompt": True,
                "inputs": {"first": {"label": "First", "media_type": "image", "target": {"kind": "video_director_role", "role": "first"}}},
                "outputs": images_out(),
            },
            "does not use the Video Director",
        ),
    ],
)
def test_lint_reports_contracts_that_do_not_fit_the_preset(catalog, contract, problem):
    parsed = parse_contracts(document(contract, mode="enhance"))

    problems = lint_contract(KREA, "enhance", parsed[(KREA, "enhance")], catalog)

    assert len(problems) == 1 and problem in problems[0]


def test_lint_reports_unknown_presets_and_modes(catalog):
    parsed = parse_contracts(document({"prompt": True, "outputs": images_out()}, mode="img2img"))

    assert "no img2img mode" in lint_contract(KREA, "img2img", parsed[(KREA, "img2img")], catalog)[0]
    assert "not installed" in lint_contract("MISSING", "img2img", parsed[(KREA, "img2img")], catalog)[0]


@pytest.mark.parametrize(
    "contract",
    [
        {"prompt": True},
        {"prompt": True, "outputs": images_out(), "extra": 1},
        {"prompt": True, "outputs": {"Images": {"label": "x", "media_type": "image"}}},
        {
            "prompt": True,
            "inputs": {"First Frame": {"label": "x", "media_type": "image", "target": {"kind": "form_field", "field": "a"}}},
            "outputs": images_out(),
        },
        {"prompt": True, "outputs": {"images": {"label": "x", "media_type": "audio"}}},
        {
            "prompt": True,
            "inputs": {"images": {"label": "x", "media_type": "image", "target": {"kind": "form_field", "field": "a"}}},
            "outputs": images_out(),
        },
        {
            "prompt": True,
            "inputs": {"photo": {"label": "x", "media_type": "image", "target": {"kind": "form_field"}}},
        },
        {
            "prompt": True,
            "inputs": {"photo": {"label": "x", "media_type": "image", "target": {"kind": "form_field", "field": "a", "role": "first"}}},
        },
        {
            "prompt": True,
            "inputs": {"photo": {"label": "x", "media_type": "image", "target": {"kind": "video_director_role"}}},
        },
        {
            "prompt": True,
            "inputs": {"photo": {"label": "x", "media_type": "image", "target": {"kind": "json_path", "field": "a"}}},
        },
    ],
)
def test_malformed_contracts_are_refused(contract):
    with pytest.raises(ValidationError):
        parse_contracts(document(contract))


def write_local(path, mode_contract, preset_id="LOCAL-PRESET", mode="restyle"):
    import yaml

    path.write_text(yaml.safe_dump(document(mode_contract, preset_id=preset_id, mode=mode)), encoding="utf-8")


def test_local_contracts_add_presets_and_replace_shipped_modes(tmp_path):
    from src.features.elixirs.contracts import (
        CONTRACTS_PATH,
        current_contracts,
        local_contracts_error,
    )

    local = tmp_path / "contracts.local.yml"
    write_local(local, {"label": "Restyle", "prompt": True, "outputs": images_out()})

    merged = current_contracts(CONTRACTS_PATH, local)

    assert merged[("LOCAL-PRESET", "restyle")].label == "Restyle"
    assert (H3, "video") in merged
    assert local_contracts_error() is None

    write_local(local, {"label": "Mine", "prompt": False, "outputs": images_out()}, preset_id=KREA, mode="txt2img")
    import os

    os.utime(local, (1, 1))
    replaced = current_contracts(CONTRACTS_PATH, local)
    assert replaced[(KREA, "txt2img")].prompt is False
    assert ("LOCAL-PRESET", "restyle") not in replaced


def test_a_broken_local_file_falls_back_to_the_shipped_contracts(tmp_path):
    from src.features.elixirs.contracts import (
        CONTRACTS_PATH,
        current_contracts,
        local_contracts_error,
    )

    local = tmp_path / "contracts.local.yml"
    local.write_text("schema_version: 1\npresets:\n  X:\n    name: x\n    modes:\n      m: {prompt: true}\n", encoding="utf-8")

    merged = current_contracts(CONTRACTS_PATH, local)

    assert (KREA, "txt2img") in merged and ("X", "m") not in merged
    assert "contracts.local.yml was not loaded" in local_contracts_error()


def test_without_a_local_file_only_the_shipped_contracts_load(tmp_path):
    from src.features.elixirs.contracts import (
        CONTRACTS_PATH,
        current_contracts,
        load_contracts,
        local_contracts_error,
    )

    assert current_contracts(CONTRACTS_PATH, tmp_path / "missing.yml") == load_contracts(CONTRACTS_PATH)
    assert local_contracts_error() is None


def test_a_preset_mode_override_can_turn_a_director_shape_off():
    from src.features.elixirs.catalog import director_shapes

    director = {
        "modes": {"t2v": {}, "i2v": {}, "flf": {}, "director": {"keyframes": "anywhere"}},
        "preset_mode_overrides": {"refs": {"modes": {"flf": None, "director": {"keyframes": None}}}},
    }

    assert director_shapes(director, "video") == ["director", "flf", "i2v", "t2v"]
    assert director_shapes(director, "refs") == ["director", "i2v", "t2v"]
