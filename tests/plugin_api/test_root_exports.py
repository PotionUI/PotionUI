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
        "generation_state",
        "get_session",
        "get_session_snapshot",
        "list_generation_files",
        "preview_memory",
        "submit_generation",
    ],
    "src.plugin_api.hooks": ["get_app_loop", "run_on_app_loop"],
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
