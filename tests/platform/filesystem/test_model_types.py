import pytest

from src.platform.filesystem import model_types as mt
from src.platform.filesystem.model_types import (
    CORE_MODEL_TYPE_DEFINITIONS,
    CORE_MODEL_TYPE_TO_DIRECTORY,
    CORE_MODEL_TYPES,
    DEFAULT_MODEL_EXTENSIONS,
    DuplicateModelTypeError,
    InvalidModelTypeError,
    ModelTypeDefinition,
    ModelTypeRegistry,
    directory_names,
    directory_to_type,
    model_types,
    type_to_directory,
)


def _plugin_type(**overrides):
    values = dict(id="pose_rig", label="Pose rig", folder="pose_rigs", source="fixture-plugin")
    values.update(overrides)
    return ModelTypeDefinition(**values)


@pytest.fixture
def registry():
    return ModelTypeRegistry(CORE_MODEL_TYPE_DEFINITIONS)


@pytest.fixture
def shared_plugin_type():
    mt.model_type_registry.register(_plugin_type(extensions=frozenset({".onnx"}), aliases=("PoseRigs",)))
    yield
    mt.model_type_registry.unregister_source("fixture-plugin")


def test_the_inverse_map_round_trips_every_entry():
    for directory, model_type in directory_to_type().items():
        assert type_to_directory()[model_type] == directory


def test_no_directory_or_type_is_mapped_twice():
    assert len(set(directory_to_type().values())) == len(directory_to_type())
    assert len(set(type_to_directory().values())) == len(type_to_directory())


def test_derived_collections_match_the_canonical_map():
    assert set(directory_names()) == set(directory_to_type().keys())
    assert set(model_types()) == set(directory_to_type().values())


def test_core_registers_every_core_type_through_the_registry_in_order():
    assert model_types()[: len(CORE_MODEL_TYPES)] == CORE_MODEL_TYPES
    assert all(d.source == "core" for d in CORE_MODEL_TYPE_DEFINITIONS)
    assert dict(CORE_MODEL_TYPE_TO_DIRECTORY) == {d.id: d.folder for d in CORE_MODEL_TYPE_DEFINITIONS}


def test_llm_is_the_directory_per_model_type_and_not_assignable():
    assert mt.directory_layout_types() == {"llm"}
    assert "llm" not in mt.assignable_model_types()


def test_conditioning_encoders_have_exactly_one_home():
    assert directory_to_type()["text_encoders"] == "text_encoder"
    assert "clip_vision" not in directory_to_type()


def test_folder_aliases_resolve_case_insensitively():
    assert mt.type_for_folder_name("Stable-diffusion") == "checkpoint"
    assert mt.type_for_folder_name("LYCORIS") == "lora"
    assert mt.type_for_folder_name("clip") == "text_encoder"
    assert mt.type_for_folder_name("nothing_here") is None


def test_comfyui_folders_keep_their_core_types():
    assert directory_to_type()["model_patches"] == "model_patch"
    assert directory_to_type()["geometry_estimation"] == "geometry_estimation"
    assert directory_to_type()["vae_approx"] == "preview_decoder"


def test_a_plugin_type_is_registered_with_its_folder_aliases_and_extensions(registry):
    registry.register(_plugin_type(extensions=frozenset({".onnx"}), aliases=("PoseRigs",)))

    view = registry.view
    assert view.types[-1] == "pose_rig"
    assert view.type_to_directory["pose_rig"] == "pose_rigs"
    assert view.folder_lookup["poserigs"] == "pose_rig"
    assert view.extensions_by_type["pose_rig"] == {".onnx"}
    assert ".onnx" in view.extensions
    assert "pose_rig" in view.assignable


def test_unregistering_a_plugin_removes_only_its_types(registry):
    registry.register(_plugin_type())
    registry.register(_plugin_type(id="pose_bank", folder="pose_banks"))

    assert registry.unregister_source("fixture-plugin") == 2
    assert registry.view.types == CORE_MODEL_TYPES
    assert registry.unregister_source("fixture-plugin") == 0


def test_a_duplicate_id_names_both_owners(registry):
    with pytest.raises(DuplicateModelTypeError) as error:
        registry.register(_plugin_type(id="lora", folder="other_loras"))

    assert "'lora' from 'fixture-plugin'" in str(error.value)
    assert "'core'" in str(error.value)
    assert registry.get("lora").source == "core"


@pytest.mark.parametrize("folder", ["LORAS", "Stable-Diffusion"])
def test_a_folder_already_used_by_another_type_is_rejected(registry, folder):
    with pytest.raises(DuplicateModelTypeError) as error:
        registry.register(_plugin_type(folder=folder))

    assert "fixture-plugin" in str(error.value)
    assert "pose_rig" not in registry.view.types


def test_an_alias_colliding_with_another_folder_is_rejected(registry):
    with pytest.raises(DuplicateModelTypeError):
        registry.register(_plugin_type(aliases=("vae_approx",)))


def test_two_plugins_cannot_claim_the_same_folder(registry):
    registry.register(_plugin_type())
    with pytest.raises(DuplicateModelTypeError) as error:
        registry.register(_plugin_type(id="other_rig", source="second-plugin"))

    assert "'second-plugin'" in str(error.value)
    assert "'fixture-plugin'" in str(error.value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": "Pose-Rig"},
        {"id": "cloud"},
        {"id": "undefined"},
        {"label": "  "},
        {"folder": "models/pose_rigs"},
        {"folder": ".."},
        {"aliases": ("a\\b",)},
        {"extensions": frozenset()},
        {"extensions": frozenset({"onnx"})},
        {"layout": "tree"},
    ],
)
def test_invalid_definitions_are_rejected_with_the_plugin_named(registry, overrides):
    with pytest.raises(InvalidModelTypeError) as error:
        registry.register(_plugin_type(**overrides))

    assert "fixture-plugin" in str(error.value)


def test_extensions_are_normalized_and_default_to_the_core_set():
    assert mt.normalize_extensions(None) == DEFAULT_MODEL_EXTENSIONS
    assert mt.normalize_extensions(["ONNX", ".Bin"]) == {".onnx", ".bin"}


def test_listeners_fire_on_change_and_a_named_listener_is_replaced(registry):
    calls = []
    registry.set_listener("roots", lambda: calls.append("first"))
    registry.set_listener("roots", lambda: calls.append("second"))

    registry.register(_plugin_type())
    registry.unregister_source("nobody")
    registry.unregister_source("fixture-plugin")
    registry.remove_listener("roots")
    registry.register(_plugin_type())

    assert calls == ["second", "second"]


def test_a_failing_listener_does_not_undo_the_registration(registry):
    registry.set_listener("broken", lambda: 1 / 0)

    registry.register(_plugin_type())

    assert "pose_rig" in registry.view.types


def test_the_catalog_serves_registered_and_virtual_types(registry):
    registry.register(_plugin_type())

    entries = {entry["id"]: entry for entry in registry.catalog()}
    assert entries["checkpoint"]["label"] == "Base model"
    assert entries["pose_rig"] == {
        "id": "pose_rig",
        "label": "Pose rig",
        "purpose": "",
        "folder": "pose_rigs",
        "aliases": [],
        "extensions": sorted(DEFAULT_MODEL_EXTENSIONS),
        "layout": "file",
        "header_classified": False,
        "assignable": True,
        "virtual": False,
        "source": "fixture-plugin",
    }
    assert entries["cloud"]["virtual"] is True


def test_module_readers_follow_the_shared_registry_at_call_time(shared_plugin_type):
    assert "pose_rig" in model_types()
    assert mt.type_for_folder_name("poserigs") == "pose_rig"
    assert mt.extensions_for("pose_rig") == {".onnx"}
    assert ".onnx" in mt.supported_extensions()
    assert "pose_rig" in mt.pickable_model_types()


def test_module_readers_drop_a_type_once_its_plugin_is_gone(shared_plugin_type):
    mt.model_type_registry.unregister_source("fixture-plugin")

    assert "pose_rig" not in model_types()
    assert ".onnx" not in mt.supported_extensions()
    assert mt.extensions_for("pose_rig") == DEFAULT_MODEL_EXTENSIONS
