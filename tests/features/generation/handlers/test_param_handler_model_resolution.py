from unittest.mock import Mock, patch

from src.features.generation.handlers.param_handler import ParamGenerationOutputHandler


def make_model(id, filename, model_type="lora"):
    model = Mock()
    model.id = id
    model.filename = filename
    model.model_type = model_type
    return model


def make_repo(by_filename=None):
    repo = Mock()
    repo.get_by_filename.side_effect = lambda f: list((by_filename or {}).get(f, []))
    return repo


def resolve(repo, value):
    return ParamGenerationOutputHandler._resolve_model(repo, value)


def test_bare_filename_resolves_by_identity():
    model = make_model("m1", "detail.safetensors")
    repo = make_repo(by_filename={"detail.safetensors": [model]})

    assert resolve(repo, "detail.safetensors") is model


def test_ref_with_subdirectory_resolves_by_basename():
    model = make_model("m1", "detail.safetensors")
    repo = make_repo(by_filename={"detail.safetensors": [model]})

    assert resolve(repo, "style/detail.safetensors") is model


def test_remote_only_model_resolves_by_basename():
    model = make_model("m1", "detail.safetensors")
    repo = make_repo(by_filename={"detail.safetensors": [model]})

    assert resolve(repo, "style/detail.safetensors") is model


def test_ambiguous_filename_across_types_refuses_to_guess():
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


def test_resolve_models_prefers_an_injected_model_locator():
    model = make_model("m1", "detail.safetensors")
    locator = Mock()
    locator.model_for_path.return_value = model

    handler = ParamGenerationOutputHandler(generation_id="gen-1", model_locator=locator)
    resolved = handler._resolve_models(["style/detail.safetensors"])

    assert resolved == [model]
    locator.model_for_path.assert_called_once_with("style/detail.safetensors")


def test_resolve_models_falls_back_to_the_repo_lookup_without_a_locator():
    model = make_model("m1", "detail.safetensors")
    repo = make_repo(by_filename={"detail.safetensors": [model]})

    handler = ParamGenerationOutputHandler(generation_id="gen-1")
    with patch("src.features.models.repository.model_repo", repo):
        resolved = handler._resolve_models(["style/detail.safetensors"])

    assert resolved == [model]
