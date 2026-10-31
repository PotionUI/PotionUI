from types import SimpleNamespace

from src.features.recipes.mode_slots import (
    derive_mode_slots,
    has_downloadable_modes,
    recipe_required_artifacts,
    variant_id_for,
)
from src.features.recipes.schema import Recipe, RecipeStep
from tests.features.recipes.mode_fixtures import FakeLoader, model_field, mode, preset, rec, video_preset


def test_shared_fields_collapse_into_one_slot_used_by_every_mode():
    plan = derive_mode_slots(video_preset())

    assert plan.slots["model"].used_by == ("video", "cinemagraph", "add_water", "restore", "motion")
    assert plan.slots["model"].fields == {m: "model" for m in plan.slots["model"].used_by}
    assert plan.modes["video"].slot_ids == ("model", "text_encoder", "video_vae")


def test_same_field_name_with_different_files_gets_a_mode_suffix():
    plan = derive_mode_slots(video_preset())

    assert "effect_lora@cinemagraph" in plan.slots
    assert "effect_lora@add_water" in plan.slots
    assert "effect_lora" not in plan.slots
    assert plan.modes["cinemagraph"].slot_ids[-1] == "effect_lora@cinemagraph"
    assert plan.slots["effect_lora@add_water"].fields == {"add_water": "effect_lora"}


def test_the_widest_group_keeps_the_bare_field_name():
    base = [model_field("model", "diffusion_model", [rec("a.safetensors", 1)])]
    other = [model_field("model", "diffusion_model", [rec("b.safetensors", 1)])]
    plan = derive_mode_slots(preset({"one": mode(base), "two": mode(base), "three": mode(other)}))

    assert set(plan.slots) == {"model", "model@three"}
    assert plan.slots["model"].used_by == ("one", "two")


def test_optional_fields_with_recommendations_are_extras_and_required_without_files_are_manual():
    plan = derive_mode_slots(video_preset())

    assert plan.modes["video"].extra_slot_ids == ("audio_vae",)
    assert plan.slots["audio_vae"].extra is True
    assert plan.slots["own_upscaler"].manual is True
    assert "own_upscaler" in plan.modes["motion"].slot_ids
    assert "loras" not in plan.slots


def test_field_requirement_makes_an_optional_field_required():
    plan = derive_mode_slots(video_preset())

    assert "control_model" in plan.modes["motion"].slot_ids
    assert plan.slots["control_model"].required is True


def test_any_of_requirement_groups_optional_fields():
    plan = derive_mode_slots(video_preset())

    restore = plan.modes["restore"]
    assert restore.any_of == (("restore_lora", "deblur_lora"),)
    assert set(restore.extra_slot_ids) == {"restore_lora", "deblur_lora"}


def test_recommendations_become_variants_with_default_and_rules():
    artifact = derive_mode_slots(video_preset()).slots["model"].artifact

    assert [v.id for v in artifact.variants] == ["dit-int8", "dit-nvfp4", "dit-bf16"]
    assert artifact.default_variant.id == "dit-int8"
    assert artifact.variants[1].recommended_for[0].generations == ("blackwell",)
    assert artifact.variants[0].provider_hint == {"source": "huggingface", "model_id": "org/repo", "version_id": "main@dit-int8.safetensors"}
    assert artifact.variants[2].size_bytes == 40 * 1024 ** 3


def test_explicit_default_recommendation_wins():
    field = model_field("model", "checkpoint", [rec("a.safetensors", 1), rec("b.safetensors", 1, default=True)])
    artifact = derive_mode_slots(preset({"m": mode([field])})).slots["model"].artifact

    assert artifact.default_variant.filename == "b.safetensors"
    assert artifact.filename == "b.safetensors"


def test_variant_ids_are_slugged_filename_stems():
    assert variant_id_for("ltx-2.5-22b-lora-cinemagraph-0.9.safetensors") == "ltx-2-5-22b-lora-cinemagraph-0-9"
    assert variant_id_for("Wan2.2_TI2V_5B.safetensors") == "wan2-2_ti2v_5b"


def test_link_recommendation_takes_its_filename_from_the_url():
    field = model_field("up", "upscaler", [{"name": "Nice Upscaler", "link": "https://example.test/files/x4.pth?download=1"}])
    artifact = derive_mode_slots(preset({"m": mode([field])})).slots["up"].artifact

    assert artifact.variants[0].filename == "x4.pth"
    assert artifact.variants[0].provider_hint == {"download_url": "https://example.test/files/x4.pth?download=1"}


def test_has_downloadable_modes():
    assert has_downloadable_modes(video_preset()) is True
    bare = preset({"m": mode([model_field("model", "checkpoint", [])])})
    assert has_downloadable_modes(bare) is False


def test_recipe_required_artifacts_follow_the_selected_and_locked_modes():
    step = RecipeStep(key="modes.plan", kind="modes.plan", title="Plan", params={"preset_id": "VID", "selected": ["cinemagraph"], "locked": ["video"]})
    recipe = Recipe(id="r", schema_version=1, version=1, name="R", engine="native", steps=[step])

    artifacts = recipe_required_artifacts(recipe, FakeLoader(video_preset()))

    assert sorted(a.id for a in artifacts) == ["effect_lora@cinemagraph", "model", "text_encoder", "video_vae"]
    assert recipe_required_artifacts(recipe, None) == []
    assert recipe_required_artifacts(SimpleNamespace(artifacts=[], steps=[]), FakeLoader()) == []


def test_two_fields_of_one_mode_with_the_same_files_stay_separate_slots():
    files = [rec("same.safetensors", 1)]
    high = model_field("high_model", "diffusion_model", files)
    low = model_field("low_model", "diffusion_model", files)
    plan = derive_mode_slots(preset({"m": mode([high, low])}))

    assert plan.modes["m"].slot_ids == ("high_model", "low_model")
    assert plan.slots["low_model"].fields == {"m": "low_model"}


def test_an_optional_field_requirement_keeps_the_field_an_extra():
    lora = model_field("style_lora", "lora", [rec("style.safetensors", 1)], required=False)
    plan = derive_mode_slots(preset({"m": mode([lora], requirements=[{"type": "model", "field": "style_lora", "optional": True}])}))

    assert plan.modes["m"].slot_ids == ()
    assert plan.modes["m"].extra_slot_ids == ("style_lora",)
