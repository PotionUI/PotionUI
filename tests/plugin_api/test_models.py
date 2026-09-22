"""Tests for the `src.plugin_api.models` surface."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.plugin_api.models import get_model_provider_info


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
