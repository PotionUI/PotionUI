from pathlib import Path, PurePosixPath

import pytest
import yaml

from src.features.models.recommendations import RECOMMENDATION_EXTENSIONS
from src.features.presets import PresetTemplateLoader
from src.features.presets.mode_fields import field_model_type, field_recommendations, model_fields

RECIPES = Path("content/recipes/marketplace")
FLUX2_ID = "01KX46YCC5RB5EGYY38SBMVKR5"
FLUX3_ID = "01M506YWDFE0W3R42YPDXWRZYW"
VDN_ID = "01KX47H3VDNMINIMAX000000VA"
SEEDVR2_ID = "01KXB7C553THYMSMKY1QSYESFM"

USER_SUPPLIED = {
    ("Model Tools", "model"): "the model to merge or quantize is the user's own",
    ("Video Tools", "model"): "the RIFE checkpoint picker has no recommendation yet",
}


@pytest.fixture(scope="module")
def templates():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    return {template.id: template for template in loader.presets}


def _recipe_model_types():
    by_preset = {}
    for path in sorted(RECIPES.glob("*.yml")):
        recipe = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        types = {
            artifact.get("model_type")
            for artifact in recipe.get("artifacts") or []
            if (artifact.get("checksum") or {}).get("value") or artifact.get("filename")
        }
        for entry in recipe.get("presets") or []:
            by_preset.setdefault(entry.get("preset_id"), set()).update(types)
    return by_preset


def _names_a_file(recommendation):
    if recommendation.sha256:
        return True
    return PurePosixPath(recommendation.filename).suffix.lower() in RECOMMENDATION_EXTENSIONS


def _recommendations(template, mode, field):
    return field_recommendations(model_fields(template.modes[mode])[field])


def test_every_required_model_field_names_its_file(templates):
    recipe_types = _recipe_model_types()
    unnamed = []
    for template in templates.values():
        for mode_name, mode in (template.modes or {}).items():
            for field_name, spec in model_fields(mode).items():
                if spec.required is not True or (template.name, field_name) in USER_SUPPLIED:
                    continue
                if any(_names_a_file(rec) for rec in field_recommendations(spec)):
                    continue
                if field_model_type(spec) in recipe_types.get(template.id, set()):
                    continue
                unnamed.append(f"{template.name}:{mode_name}:{field_name}")
    assert unnamed == []


@pytest.mark.parametrize("mode", ["txt2img", "edit"])
def test_the_flux3_vae_is_the_flux2_file(templates, mode):
    flux2 = {rec.sha256 for rec in _recommendations(templates[FLUX2_ID], "txt2img", "vae")}
    flux3 = {rec.sha256 for rec in _recommendations(templates[FLUX3_ID], mode, "vae")}

    assert flux3 == flux2


def test_the_vdn_branch_is_saved_under_its_own_name(templates):
    [rec] = _recommendations(templates[VDN_ID], "video", "vdn_module")

    assert rec.filename == "minimax_h3_vdn_linear_branch.safetensors"
    assert rec.link.endswith("/linear_branch/model.safetensors")
    assert rec.sha256 == "dec6981c7874f5b3bc92d1a02e256b673a3b3499dc1a124714bb3b19da602855"


def test_both_seedvr2_modes_recommend_the_same_hashed_files(templates):
    seedvr2 = templates[SEEDVR2_ID]
    for field in ("diffusion_model", "vae", "prompt_embedding"):
        image = [(rec.link, rec.sha256) for rec in _recommendations(seedvr2, "upscale", field)]
        video = [(rec.link, rec.sha256) for rec in _recommendations(seedvr2, "video_upscale", field)]

        assert image == video
        assert all(sha256 for _, sha256 in image)
