from pathlib import Path

import pytest
import yaml

from src.features.recipes.schema import (
    RecipeArtifact,
    RecipeArtifactVariant,
    RecipeVariantRule,
    parse_recipe,
    validate_recipe_dict,
)
from src.features.recipes.variants import (
    assess_variant,
    choose_variant,
    resolve_selection,
    validate_selections,
)
from src.platform.runtime.gpu_profile import GpuProfile, build_gpu_profile

GB = 1024 ** 3
REPO_ROOT = Path(__file__).resolve().parents[3]

CAPABILITIES = {"ampere": (8, 6), "ada": (8, 9), "blackwell": (12, 0), "hopper": (9, 0)}


def _gpu(generation, vram_gb):
    return build_gpu_profile(CAPABILITIES[generation], vram_gb, f"{generation}-{vram_gb}")


def _variant(vid, precision, size_gb, rules=(), default=False):
    return RecipeArtifactVariant(
        id=vid,
        label=vid,
        precision=precision,
        filename=f"{vid}.safetensors",
        size_bytes=int(size_gb * GB),
        recommended_for=tuple(rules),
        default=default,
    )


def _dit():
    return RecipeArtifact(
        id="dit",
        kind="diffusion_model",
        model_type="diffusion_model",
        filename="dit_fp8.safetensors",
        display_name="DiT",
        variants=(
            _variant("dit_bf16", "bf16", 24.5, [RecipeVariantRule(min_vram_gb=40)]),
            _variant(
                "dit_fp8",
                "fp8",
                12.2,
                [RecipeVariantRule(min_vram_gb=20, generations=("ada", "hopper", "blackwell"))],
                default=True,
            ),
            _variant("dit_int8", "int8", 12.6, [RecipeVariantRule(min_vram_gb=20)]),
            _variant("dit_nvfp4", "nvfp4", 7.1, [RecipeVariantRule(generations=("blackwell",))]),
        ),
    )


def _te():
    return RecipeArtifact(
        id="te",
        kind="text_encoder",
        model_type="text_encoder",
        filename="te_fp8.safetensors",
        display_name="TE",
        variants=(
            _variant("te_bf16", "bf16", 8.3, [RecipeVariantRule(min_vram_gb=32)]),
            _variant("te_fp8", "fp8", 4.9, default=True),
        ),
    )


MATRIX = [
    ("ampere", 16, "dit_int8", "te_fp8"),
    ("ampere", 24, "dit_int8", "te_fp8"),
    ("ampere", 32, "dit_int8", "te_bf16"),
    ("ada", 16, "dit_fp8", "te_fp8"),
    ("ada", 24, "dit_fp8", "te_fp8"),
    ("ada", 32, "dit_fp8", "te_bf16"),
    ("blackwell", 16, "dit_nvfp4", "te_fp8"),
    ("blackwell", 24, "dit_fp8", "te_fp8"),
    ("blackwell", 32, "dit_fp8", "te_bf16"),
    ("hopper", 80, "dit_bf16", "te_bf16"),
]


@pytest.mark.parametrize("generation,vram,expected_dit,expected_te", MATRIX)
def test_default_selection_matrix(generation, vram, expected_dit, expected_te):
    gpu = _gpu(generation, vram)
    assert choose_variant(_dit(), gpu).variant_id == expected_dit
    assert choose_variant(_te(), gpu).variant_id == expected_te


def test_reason_names_the_vram_when_a_rule_fits():
    choice = choose_variant(_dit(), _gpu("ada", 23.99))
    assert choice.variant_id == "dit_fp8"
    assert choice.reason == "Fits your 24 GB"


def test_nothing_fits_falls_back_to_the_smallest_fast_variant_and_says_it_offloads():
    ada = choose_variant(_dit(), _gpu("ada", 16))
    assert ada.variant_id == "dit_fp8"
    assert "offload" in ada.reason and "16 GB" in ada.reason
    ampere = choose_variant(_dit(), _gpu("ampere", 12))
    assert ampere.variant_id == "dit_int8"


def test_no_gpu_uses_the_recipe_default():
    choice = choose_variant(_dit(), GpuProfile())
    assert choice.variant_id == "dit_fp8"
    assert "No GPU" in choice.reason


def test_installed_variant_beats_the_gpu_pick():
    choice = choose_variant(_dit(), _gpu("blackwell", 32), installed_ids={"dit_bf16"})
    assert choice.variant_id == "dit_bf16"
    assert "installed" in choice.reason.lower()


def test_installed_pick_matching_the_ideal_keeps_it():
    choice = choose_variant(_dit(), _gpu("ada", 24), installed_ids={"dit_fp8", "dit_bf16"})
    assert choice.variant_id == "dit_fp8"


def test_nvfp4_is_never_the_default_off_blackwell_even_when_it_is_the_smallest():
    only_small = RecipeArtifact(
        id="x",
        kind="diffusion_model",
        model_type="diffusion_model",
        filename="a.safetensors",
        variants=(
            _variant("big_bf16", "bf16", 30, [RecipeVariantRule(min_vram_gb=64)], default=True),
            _variant("tiny_nvfp4", "nvfp4", 5),
        ),
    )
    assert choose_variant(only_small, _gpu("ada", 16)).variant_id == "big_bf16"
    assert choose_variant(only_small, _gpu("blackwell", 16)).variant_id == "tiny_nvfp4"


def test_assessment_notes():
    nvfp4 = _dit().get_variant("dit_nvfp4")
    note = assess_variant(nvfp4, _gpu("ada", 24)).note
    assert note == "nvfp4 needs an RTX 50-series (Blackwell) GPU"
    bf16 = _dit().get_variant("dit_bf16")
    assert assess_variant(bf16, _gpu("ada", 24)).note == "Best with 40 GB of VRAM or more"
    fp8 = _dit().get_variant("dit_fp8")
    ampere = assess_variant(fp8, _gpu("ampere", 24))
    assert ampere.recommended is False and ampere.note.startswith("Recommended for")
    te_fp8 = _te().get_variant("te_fp8")
    slow = assess_variant(te_fp8, _gpu("ampere", 24))
    assert slow.recommended is True and slow.fast is False
    assert "isn't hardware-accelerated" in slow.note
    assert assess_variant(fp8, _gpu("ada", 24)).note is None


def test_resolve_selection_swaps_the_file_fields():
    dit = _dit()
    resolved = resolve_selection(dit, "dit_nvfp4")
    assert resolved.filename == "dit_nvfp4.safetensors"
    assert resolved.size_bytes == int(7.1 * GB)
    assert resolved.variants == ()
    assert resolve_selection(dit, "nope").filename == "dit_fp8.safetensors"
    plain = RecipeArtifact(id="v", kind="vae", model_type="vae", filename="vae.safetensors")
    assert resolve_selection(plain, None) is plain


def test_validate_selections():
    slots = [
        {"id": "dit", "variants": [{"id": "dit_fp8"}, {"id": "dit_bf16"}]},
        {"id": "vae", "variants": [{"id": "vae"}]},
    ]
    assert validate_selections({"dit": "dit_bf16", "vae": "vae"}, slots) == []
    assert validate_selections({}, slots) == []
    assert len(validate_selections({"dit": "dit_int8"}, slots)) == 1
    assert len(validate_selections({"ghost": "x"}, slots)) == 1
    assert len(validate_selections({"dit": 3}, slots)) == 1


def _krea_recipe():
    data = yaml.safe_load((REPO_ROOT / "content/recipes/marketplace/krea2-starter.yml").read_text())
    assert validate_recipe_dict(data) == []
    return parse_recipe(data)


KREA_MATRIX = [
    ("ampere", 16, "krea2_turbo_int8_convrot", "qwen3vl_4b_fp8_scaled"),
    ("ampere", 24, "krea2_turbo_int8_convrot", "qwen3vl_4b_fp8_scaled"),
    ("ampere", 32, "krea2_turbo_int8_convrot", "qwen3vl_4b_bf16"),
    ("ada", 16, "krea2_turbo_fp8_scaled", "qwen3vl_4b_fp8_scaled"),
    ("ada", 24, "krea2_turbo_fp8_scaled", "qwen3vl_4b_fp8_scaled"),
    ("ada", 32, "krea2_turbo_fp8_scaled", "qwen3vl_4b_bf16"),
    ("blackwell", 16, "krea2_turbo_nvfp4", "qwen3vl_4b_fp8_scaled"),
    ("blackwell", 24, "krea2_turbo_fp8_scaled", "qwen3vl_4b_fp8_scaled"),
    ("blackwell", 32, "krea2_turbo_fp8_scaled", "qwen3vl_4b_bf16"),
]


@pytest.mark.parametrize("generation,vram,expected_dit,expected_te", KREA_MATRIX)
def test_krea2_recipe_defaults(generation, vram, expected_dit, expected_te):
    recipe = _krea_recipe()
    gpu = _gpu(generation, vram)
    assert choose_variant(recipe.get_artifact("krea2-diffusion-model"), gpu).variant_id == expected_dit
    assert choose_variant(recipe.get_artifact("krea2-text-encoder"), gpu).variant_id == expected_te


def test_krea2_recipe_shape():
    recipe = _krea_recipe()
    dit = recipe.get_artifact("krea2-diffusion-model")
    assert [v.precision for v in dit.variants] == ["bf16", "fp8", "int8", "nvfp4"]
    assert not any("mxfp8" in v.filename for v in dit.variants)
    assert dit.filename == "krea2_turbo_fp8_scaled.safetensors"
    assert dit.default_variant.id == "krea2_turbo_fp8_scaled"
    assert all(v.uploader == "Comfy-Org" for v in dit.variants)
    te = recipe.get_artifact("krea2-text-encoder")
    assert te.filename == "qwen3vl_4b_fp8_scaled.safetensors"
    assert recipe.get_artifact("krea2-vae").variants == ()
