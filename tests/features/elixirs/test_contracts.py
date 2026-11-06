import pytest
from pydantic import ValidationError

from src.features.elixirs.catalog import installed_catalog
from src.features.elixirs.contracts import ModeContract, lint_contract, lint_contracts

KREA = "4TK1KBQZ2XMB8ME0PTMXS1YJQP"
H3 = "01KX47H3MINIMAX000000000VA"


def contract(body):
    return ModeContract.model_validate({"label": "Mode", **body})


def images_out():
    return {"images": {"label": "Final images", "media_type": "image"}}


def test_the_six_original_contracts_cover_the_proposal_examples(contracts):
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
    from src.features.presets.file_repository import FilePresetRepository
    from src.features.presets.loader import PresetTemplateLoader
    from tests.features.elixirs.test_derivation import MARKETPLACE

    loader = PresetTemplateLoader([str(MARKETPLACE)])
    loader._ensure_loaded()
    return installed_catalog(FilePresetRepository(loader), [*(template.id for template in loader.presets), "NOT-INSTALLED"])


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


def test_the_original_contracts_match_the_installed_presets(contracts, marketplace_catalog):
    assert lint_contracts(contracts, marketplace_catalog) == []


@pytest.mark.parametrize(
    "body, problem",
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
def test_lint_reports_contracts_that_do_not_fit_the_preset(catalog, body, problem):
    problems = lint_contract(KREA, "enhance", contract(body), catalog)

    assert len(problems) == 1 and problem in problems[0]


def test_lint_reports_unknown_presets_and_modes(catalog):
    parsed = contract({"prompt": True, "outputs": images_out()})

    assert "no img2img mode" in lint_contract(KREA, "img2img", parsed, catalog)[0]
    assert "not installed" in lint_contract("MISSING", "img2img", parsed, catalog)[0]


@pytest.mark.parametrize(
    "body",
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
def test_malformed_contracts_are_refused(body):
    with pytest.raises(ValidationError):
        contract(body)


def test_a_preset_mode_override_can_turn_a_director_shape_off():
    from src.features.elixirs.catalog import director_shapes

    director = {
        "modes": {"t2v": {}, "i2v": {}, "flf": {}, "director": {"keyframes": "anywhere"}},
        "preset_mode_overrides": {"refs": {"modes": {"flf": None, "director": {"keyframes": None}}}},
    }

    assert director_shapes(director, "video") == ["director", "flf", "i2v", "t2v"]
    assert director_shapes(director, "refs") == ["director", "i2v", "t2v"]
