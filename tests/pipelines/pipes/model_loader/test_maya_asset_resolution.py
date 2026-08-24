"""Tests that Maya's weights cannot be fetched outside the download manager.

`MayaModel` hands its configured `model_id` / `snac_model` straight to
`AutoModelForCausalLM.from_pretrained`, `AutoTokenizer.from_pretrained` and
`SNAC.from_pretrained`. A repo id in either would make those libraries fetch
the weights themselves, with no history, containment or progress - so the pipe
mirrors the repos through the ASSETS service and the model refuses anything
that is not already a local directory.
"""

import sys
from unittest.mock import Mock, patch

import pytest

from src.pipelines.models import BaseModel
from src.pipelines.pipes._shared.models.maya.maya_model import MayaModel
from src.pipelines.pipes.model_loader.maya.main import ModelLoaderMayaPipe

_resolve = ModelLoaderMayaPipe._resolve_repo


def _assets(depot):
    def _ensure(repo_id, *, subdir, **kwargs):
        target = depot / subdir
        target.mkdir(parents=True, exist_ok=True)
        return target

    assets = Mock()
    assets.ensure_asset_repo = Mock(side_effect=_ensure)
    return assets


class TestResolveRepo:
    def test_repo_id_is_mirrored_into_the_depot(self, tmp_path):
        assets = _assets(tmp_path)

        result = _resolve(assets, "maya-research/maya1", "tts", "Maya model")

        assert result == str(tmp_path / "tts" / "maya-research-maya1")
        assert assets.ensure_asset_repo.call_args.kwargs["subdir"] == (
            "tts/maya-research-maya1"
        )

    def test_existing_local_directory_is_passed_through(self, tmp_path):
        """A preset may point straight at a directory on disk; nothing to fetch."""
        assets = _assets(tmp_path)
        local = tmp_path / "already-here"
        local.mkdir()

        result = _resolve(assets, str(local), "tts", "Maya model")

        assert result == str(local)
        assets.ensure_asset_repo.assert_not_called()

    def test_repo_id_without_a_service_raises(self):
        with pytest.raises(RuntimeError) as exc:
            _resolve(None, "maya-research/maya1", "tts", "Maya model")

        assert "ASSETS" in str(exc.value)
        assert "maya-research/maya1" in str(exc.value)

    def test_destination_is_depot_relative(self, tmp_path):
        assets = _assets(tmp_path)

        _resolve(assets, "hubertsiuzdak/snac_24khz", "tts", "SNAC codec")

        assert not assets.ensure_asset_repo.call_args.kwargs["subdir"].startswith("/")


@pytest.fixture
def stub_libraries():
    """Stub `transformers`/`snac` so a regression fails fast.

    Without this, dropping the guard makes these tests hand a real repo id to
    the real `from_pretrained` and wait on a multi-gigabyte download - the
    tests would hang instead of failing, and would hit the network from a unit
    run. Stubbed, a lost guard shows up as "DID NOT RAISE" in a second.
    """
    transformers = Mock()
    transformers.AutoModelForCausalLM.from_pretrained.return_value = Mock()
    transformers.AutoTokenizer.from_pretrained.return_value = Mock()
    snac = Mock()
    snac.SNAC.from_pretrained.return_value = Mock()

    with patch.dict(sys.modules, {"transformers": transformers, "snac": snac}):
        yield transformers, snac


class TestMayaModelRefusesRepoIds:
    def _model(self, model_id, snac_model):
        return MayaModel(
            template={"base": BaseModel.MAYA},
            config={
                "model_id": model_id,
                "snac_model": snac_model,
                "device": "cpu",
                "dtype": "float32",
            },
        )

    def test_no_library_call_happens_on_refusal(self, tmp_path, stub_libraries):
        """The refusal must land before any `from_pretrained`, which is the
        moment the library would start fetching on its own."""
        transformers, snac = stub_libraries
        good = tmp_path / "snac"
        good.mkdir()

        with pytest.raises(ValueError):
            self._model("maya-research/maya1", str(good)).load()

        transformers.AutoModelForCausalLM.from_pretrained.assert_not_called()
        transformers.AutoTokenizer.from_pretrained.assert_not_called()
        snac.SNAC.from_pretrained.assert_not_called()

    def test_repo_id_as_model_id_is_refused(self, tmp_path, stub_libraries):
        snac = tmp_path / "snac"
        snac.mkdir()

        with pytest.raises(ValueError) as exc:
            self._model("maya-research/maya1", str(snac)).load()

        assert "model_id" in str(exc.value)

    def test_repo_id_as_snac_model_is_refused(self, tmp_path, stub_libraries):
        maya = tmp_path / "maya"
        maya.mkdir()

        with pytest.raises(ValueError) as exc:
            self._model(str(maya), "hubertsiuzdak/snac_24khz").load()

        assert "snac_model" in str(exc.value)

    def test_nonexistent_directory_is_refused(self, tmp_path, stub_libraries):
        """Not merely "looks like a path": the fetch must actually have
        happened, or the libraries would fall back to hub resolution."""
        snac = tmp_path / "snac"
        snac.mkdir()

        with pytest.raises(ValueError):
            self._model(str(tmp_path / "never-fetched"), str(snac)).load()

    def test_empty_config_value_is_refused(self, tmp_path, stub_libraries):
        snac = tmp_path / "snac"
        snac.mkdir()

        with pytest.raises(ValueError):
            self._model("", str(snac)).load()
