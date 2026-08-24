"""Resolving an emitted model parameter to a model row.

Presets emit whatever the picker stored. Native presets emit a host path; ComfyUI presets
emit an engine-native ref. Only the first ever matched `models.file_path`, so ComfyUI
generations silently recorded no models at all.
"""

from unittest.mock import Mock

from src.features.generation.handlers.param_handler import ParamGenerationOutputHandler


def make_model(id, filename, file_path=None, model_type="lora"):
    model = Mock()
    model.id = id
    model.filename = filename
    model.file_path = file_path
    model.model_type = model_type
    return model


def make_repo(by_path=None, by_filename=None):
    repo = Mock()
    repo.get_by_file_path.side_effect = lambda p, **kw: (by_path or {}).get(p)
    repo.get_by_filename.side_effect = lambda f: list((by_filename or {}).get(f, []))
    return repo


def resolve(repo, value):
    return ParamGenerationOutputHandler._resolve_model(repo, value)


def test_exact_file_path_still_wins():
    """The native path must not regress to a filename lookup."""
    model = make_model("m1", "detail.safetensors", "models/loras/detail.safetensors")
    repo = make_repo(by_path={"models/loras/detail.safetensors": model})

    assert resolve(repo, "models/loras/detail.safetensors") is model
    repo.get_by_filename.assert_not_called()


def test_bare_filename_resolves_by_identity():
    """A ComfyUI preset emits `detail.safetensors`; this is the case that lost history."""
    model = make_model("m1", "detail.safetensors", "models/loras/detail.safetensors")
    repo = make_repo(by_filename={"detail.safetensors": [model]})

    assert resolve(repo, "detail.safetensors") is model


def test_ref_with_subdirectory_resolves_by_basename():
    """A ref's directory belongs to the engine that produced it, not to this host."""
    model = make_model("m1", "detail.safetensors", "models/loras/detail.safetensors")
    repo = make_repo(by_filename={"detail.safetensors": [model]})

    assert resolve(repo, "style/detail.safetensors") is model


def test_remote_only_model_with_null_file_path_resolves():
    """A model that exists only on a remote backend has no local path at all."""
    model = make_model("m1", "detail.safetensors", None)
    repo = make_repo(by_filename={"detail.safetensors": [model]})

    assert resolve(repo, "style/detail.safetensors") is model


def test_ambiguous_filename_across_types_refuses_to_guess():
    """Recording the wrong model is worse than recording none."""
    a = make_model("m1", "shared.safetensors", model_type="lora")
    b = make_model("m2", "shared.safetensors", model_type="checkpoint")
    repo = make_repo(by_filename={"shared.safetensors": [a, b]})

    assert resolve(repo, "shared.safetensors") is None


def test_unknown_model_returns_none():
    repo = make_repo()
    assert resolve(repo, "nothing.safetensors") is None


def test_empty_value_returns_none_without_querying():
    repo = make_repo()
    assert resolve(repo, "") is None
    repo.get_by_filename.assert_not_called()
