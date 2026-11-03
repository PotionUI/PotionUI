"""Tests for the `src.plugin_api.models` surface."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src.features.models.records import Model
from src.plugin_api.models import (
    get_model_provider_info,
    index_model_file,
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


def _catalog_model():
    return Model(
        id="model-1", filename="a.safetensors", model_type="checkpoint", sha256="b" * 64, file_size=42,
        location={"root": "/depot", "path": "checkpoints/a.safetensors"}, prompting_guidance="admin notes",
    )


def test_model_for_path_returns_the_non_admin_dict_when_found():
    locator = Mock(model_for_path=Mock(return_value=_catalog_model()))
    container = SimpleNamespace(model_locator=locator)

    with patch("src.plugin_api.models.get_container", return_value=container):
        result = model_for_path("/depot/checkpoints/a.safetensors")

    assert result["id"] == "model-1"
    assert result["filename"] == "a.safetensors"
    assert not {"location", "file_size", "sha256", "prompting_guidance"} & result.keys()
    locator.model_for_path.assert_called_once_with("/depot/checkpoints/a.safetensors")


def test_model_for_path_hands_the_operational_fields_to_an_admin_caller_only():
    locator = Mock(model_for_path=Mock(return_value=_catalog_model()))
    container = SimpleNamespace(model_locator=locator)

    with patch("src.plugin_api.models.get_container", return_value=container):
        result = model_for_path("/depot/checkpoints/a.safetensors", admin=True)

    assert result["sha256"] == "b" * 64
    assert result["location"] == {"root": "/depot", "path": "checkpoints/a.safetensors"}


def test_model_for_path_returns_none_when_unresolvable():
    locator = Mock(model_for_path=Mock(return_value=None))
    container = SimpleNamespace(model_locator=locator)

    with patch("src.plugin_api.models.get_container", return_value=container):
        result = model_for_path("/somewhere/else.safetensors")

    assert result is None


def test_classifier_exports_are_available():
    from src.platform.filesystem.model_types import model_types
    from src.plugin_api import models as api

    for name in ("HeaderView", "TensorInfo", "FamilyMatch", "model_types", "model_classifier_registry"):
        assert name in api.__all__
        assert hasattr(api, name)
    assert api.model_types() == model_types()


def test_the_classifier_registry_view_is_read_only():
    from src.plugin_api.models import FamilyMatch, HeaderView, TensorInfo, model_classifier_registry

    assert not hasattr(model_classifier_registry, "register")
    assert not hasattr(model_classifier_registry, "unregister")
    assert not hasattr(model_classifier_registry, "unregister_source")
    keys = {d.key for d in model_classifier_registry.definitions()}
    assert {"core.native_dit", "core.sd_unet", "core.gguf_arch"} <= keys
    assert model_classifier_registry.get("core.native_dit").source == "core"
    view = HeaderView("safetensors", {"a": TensorInfo("F16", (1,))})
    assert model_classifier_registry.classify(view) is None
    assert FamilyMatch("x").family == "x"


class _Roots:
    def __init__(self, write_dirs):
        self.write_dirs = write_dirs

    def write_dir(self, model_type):
        if model_type not in self.write_dirs:
            raise LookupError(model_type)
        return SimpleNamespace(path=self.write_dirs[model_type])


class _Indexing:
    def __init__(self, outcome):
        self.outcome = outcome
        self.paths = []

    def index_path(self, path):
        self.paths.append(path)
        return dict(self.outcome)


class _Assignments:
    def __init__(self, already=()):
        self.already = set(already)
        self.calls = []

    def assign_model_to_user(self, model_id, user_id):
        from src.features.models.exceptions import ModelAlreadyAssignedException

        self.calls.append((model_id, user_id))
        if (model_id, user_id) in self.already:
            raise ModelAlreadyAssignedException("already")


def _index_container(tmp_path, outcome=None, already=()):
    write_dir = tmp_path / "models" / "loras"
    write_dir.mkdir(parents=True)
    indexing = _Indexing(outcome or {"model_id": "m-1", "indexed": True, "duplicate_of": None, "warning": None})
    assignments = _Assignments(already)
    container = SimpleNamespace(
        model_roots=_Roots({"lora": write_dir}),
        model_index_manager=SimpleNamespace(indexing=indexing, assignments=assignments),
    )
    return container, write_dir, indexing, assignments


def test_index_model_file_indexes_a_file_in_a_write_root_and_assigns_it_to_the_caller(tmp_path):
    container, write_dir, indexing, assignments = _index_container(tmp_path)
    target = write_dir / "hero.safetensors"
    target.write_bytes(b"x")

    with patch("src.plugin_api.models.get_container", return_value=container):
        result = index_model_file(target, user_id="user-1")

    assert result["model_id"] == "m-1"
    assert indexing.paths == [str(target.resolve())]
    assert assignments.calls == [("m-1", "user-1")]


def test_index_model_file_assigns_the_existing_twin_of_a_duplicate_and_tolerates_a_prior_assignment(tmp_path):
    outcome = {"model_id": None, "indexed": False, "duplicate_of": {"id": "m-0"}, "warning": None}
    container, write_dir, _, assignments = _index_container(tmp_path, outcome, already={("m-0", "user-1")})
    target = write_dir / "copy.safetensors"
    target.write_bytes(b"x")

    with patch("src.plugin_api.models.get_container", return_value=container):
        index_model_file(target, user_id="user-1")

    assert assignments.calls == [("m-0", "user-1")]


def test_index_model_file_without_a_user_assigns_nobody(tmp_path):
    container, write_dir, _, assignments = _index_container(tmp_path)
    target = write_dir / "hero.safetensors"
    target.write_bytes(b"x")

    with patch("src.plugin_api.models.get_container", return_value=container):
        index_model_file(target)

    assert assignments.calls == []


def test_index_model_file_refuses_files_outside_the_write_roots_and_missing_files(tmp_path):
    container, write_dir, indexing, _ = _index_container(tmp_path)
    outside = tmp_path / "elsewhere.safetensors"
    outside.write_bytes(b"x")
    escape = write_dir / ".." / ".." / "elsewhere.safetensors"

    with patch("src.plugin_api.models.get_container", return_value=container):
        with pytest.raises(ValueError, match="not inside a model folder"):
            index_model_file(outside, user_id="user-1")
        with pytest.raises(ValueError, match="not inside a model folder"):
            index_model_file(escape, user_id="user-1")
        with pytest.raises(ValueError, match="not a file"):
            index_model_file(write_dir / "missing.safetensors", user_id="user-1")

    assert indexing.paths == []
