"""Tests for the `src.plugin_api.models` surface."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.plugin_api.models import (
    get_model_provider_info,
    model_for_path,
    model_type_dirs,
    model_write_dir,
    resolve_model_file,
)


def _provider_info(**fields):
    defaults = dict(provider="civitai", provider_model_id="123", provider_version_id="456", name="Some Model")
    defaults.update(fields)
    return SimpleNamespace(**defaults)


def test_get_model_provider_info_returns_first_matching_row():
    model_repository = Mock(get_providers=Mock(return_value=[_provider_info()]))
    container = SimpleNamespace(model_repository=model_repository)

    with patch("src.plugin_api.models.get_container", return_value=container):
        result = get_model_provider_info("model-1", provider="civitai")

    assert result == {
        "provider": "civitai",
        "provider_model_id": "123",
        "provider_version_id": "456",
        "model_name": "Some Model",
    }
    model_repository.get_providers.assert_called_once_with("model-1", provider="civitai")


def test_get_model_provider_info_returns_none_when_unlinked():
    model_repository = Mock(get_providers=Mock(return_value=[]))
    container = SimpleNamespace(model_repository=model_repository)

    with patch("src.plugin_api.models.get_container", return_value=container):
        result = get_model_provider_info("model-1", provider="civitai")

    assert result is None


def test_get_model_provider_info_defaults_provider_to_none():
    model_repository = Mock(get_providers=Mock(return_value=[]))
    container = SimpleNamespace(model_repository=model_repository)

    with patch("src.plugin_api.models.get_container", return_value=container):
        get_model_provider_info("model-1")

    model_repository.get_providers.assert_called_once_with("model-1", provider=None)


def test_model_type_dirs_returns_the_resolvers_type_dir_paths():
    type_dir = SimpleNamespace(path=Path("/depot/loras"))
    model_roots = Mock(type_dirs=Mock(return_value=[type_dir]))
    container = SimpleNamespace(model_roots=model_roots)

    with patch("src.plugin_api.models.get_container", return_value=container):
        result = model_type_dirs("lora")

    assert result == [Path("/depot/loras")]
    model_roots.type_dirs.assert_called_once_with("lora")


def test_model_write_dir_returns_the_write_roots_path():
    type_dir = SimpleNamespace(path=Path("/depot/checkpoints"))
    model_roots = Mock(write_dir=Mock(return_value=type_dir))
    container = SimpleNamespace(model_roots=model_roots)

    with patch("src.plugin_api.models.get_container", return_value=container):
        result = model_write_dir("checkpoint")

    assert result == Path("/depot/checkpoints")
    model_roots.write_dir.assert_called_once_with("checkpoint")


def test_resolve_model_file_delegates_to_the_locator():
    locator = Mock(path_for_model=Mock(return_value=Path("/depot/checkpoints/a.safetensors")))
    container = SimpleNamespace(model_locator=locator)

    with patch("src.plugin_api.models.get_container", return_value=container):
        result = resolve_model_file("model-1")

    assert result == Path("/depot/checkpoints/a.safetensors")
    locator.path_for_model.assert_called_once_with("model-1")


def test_model_for_path_returns_a_dict_when_found():
    model = Mock(to_dict=Mock(return_value={"id": "model-1"}))
    locator = Mock(model_for_path=Mock(return_value=model))
    container = SimpleNamespace(model_locator=locator)

    with patch("src.plugin_api.models.get_container", return_value=container):
        result = model_for_path("/depot/checkpoints/a.safetensors")

    assert result == {"id": "model-1"}
    model.to_dict.assert_called_once_with(include_providers=False)


def test_model_for_path_returns_none_when_unresolvable():
    locator = Mock(model_for_path=Mock(return_value=None))
    container = SimpleNamespace(model_locator=locator)

    with patch("src.plugin_api.models.get_container", return_value=container):
        result = model_for_path("/somewhere/else.safetensors")

    assert result is None
