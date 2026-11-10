import importlib

import pytest

import src.plugin_api as root

REEXPORTS = {
    "src.plugin_api.generation": [
        "FormBindingError",
        "FormNotFoundException",
        "IdempotencyKeyConflict",
        "NoBackendForEngineError",
        "NoEligibleBackendError",
        "SessionNotFoundError",
        "cancel_generation",
        "generation_active",
        "generation_failure",
        "generation_record",
        "generation_state",
        "get_session",
        "get_session_snapshot",
        "list_generation_files",
        "preview_memory",
        "submit_generation",
    ],
    "src.plugin_api.backends": ["list_backends"],
    "src.plugin_api.cloud": [
        "DirectorEstimateError",
        "enabled_cloud_models",
        "estimate_cloud_cost",
        "resolve_cloud_model",
    ],
    "src.plugin_api.hooks": ["get_app_loop", "run_on_app_loop"],
    "src.plugin_api.presets": [
        "DirectorBindingError",
        "PresetInstallState",
        "PresetTemplate",
        "bind_director_media",
        "list_presets",
        "preset_install_state",
        "preset_template",
        "resolve_preset_path",
    ],
    "src.plugin_api.testing": ["scratch_database", "use_database"],
    "src.plugin_api.media": [
        "embed_images",
        "image_embedder",
        "detect_faces",
        "face_detector",
        "face_embedder",
        "embed_faces",
    ],
}


@pytest.mark.parametrize(
    ("module_name", "name"),
    [(module_name, name) for module_name, names in REEXPORTS.items() for name in names],
)
def test_a_name_reexported_at_the_root_is_the_modules_own(module_name, name):
    module = importlib.import_module(module_name)

    assert name in module.__all__
    assert name in root.__all__
    assert getattr(root, name) is getattr(module, name)


def test_every_name_the_root_lists_resolves():
    eager = [name for name in root.__all__ if name not in root._TORCH_BACKED_EXPORTS]

    assert [name for name in eager if not hasattr(root, name)] == []


def test_the_root_lists_each_name_once():
    assert len(root.__all__) == len(set(root.__all__))
