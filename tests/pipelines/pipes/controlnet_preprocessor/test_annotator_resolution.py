"""Tests for how `controlnet_preprocessor` obtains its annotator weights.

Seven detectors used to call `from_pretrained("lllyasviel/Annotators")`, which
made `controlnet_aux` pull weights into the Hugging Face cache with no download
history, no depot containment and no progress. They now load from a directory
the ASSETS service mirrored into the depot.
"""

from unittest.mock import Mock

import pytest

from src.pipelines.pipes.controlnet_preprocessor.main import (
    ANNOTATORS_REPO,
    ControlNetPreprocessorPipe,
)

_resolve = ControlNetPreprocessorPipe._resolve_annotators


def _assets(path="/depot/annotators/lllyasviel-annotators"):
    assets = Mock()
    assets.ensure_asset_repo = Mock(return_value=path)
    return assets


class TestResolveAnnotators:
    def test_no_fetch_when_no_preprocessor_is_enabled(self):
        assets = _assets()

        assert _resolve(assets, [{"type": "depth", "enabled": False}]) is None
        assets.ensure_asset_repo.assert_not_called()

    def test_no_fetch_for_canny_which_needs_no_weights(self):
        """Canny is pure image processing - enabling it must not pull a
        multi-gigabyte annotator repo."""
        assets = _assets()

        assert _resolve(assets, [{"type": "canny", "enabled": True}]) is None
        assets.ensure_asset_repo.assert_not_called()

    def test_no_fetch_for_an_unknown_type(self):
        assets = _assets()

        assert _resolve(assets, [{"type": "nonsense", "enabled": True}]) is None
        assets.ensure_asset_repo.assert_not_called()

    @pytest.mark.parametrize(
        "preprocessor",
        ["depth", "openpose", "normal", "scribble", "lineart", "mlsd", "hed"],
    )
    def test_every_weight_backed_preprocessor_triggers_the_fetch(self, preprocessor):
        assets = _assets()

        result = _resolve(assets, [{"type": preprocessor, "enabled": True}])

        assert result == "/depot/annotators/lllyasviel-annotators"
        assets.ensure_asset_repo.assert_called_once()
        assert assets.ensure_asset_repo.call_args.args[0] == ANNOTATORS_REPO

    def test_fetch_destination_is_depot_relative(self):
        assets = _assets()

        _resolve(assets, [{"type": "depth", "enabled": True}])

        subdir = assets.ensure_asset_repo.call_args.kwargs["subdir"]
        assert subdir == "annotators/lllyasviel-annotators"
        assert not subdir.startswith("/")

    def test_fetched_once_for_several_enabled_detectors(self):
        """All seven detectors share one repo; mirroring it per detector would
        queue the same download several times."""
        assets = _assets()

        _resolve(
            assets,
            [
                {"type": "depth", "enabled": True},
                {"type": "openpose", "enabled": True},
                {"type": "hed", "enabled": True},
            ],
        )

        assets.ensure_asset_repo.assert_called_once()

    def test_type_matching_is_case_insensitive(self):
        assets = _assets()

        _resolve(assets, [{"type": "DEPTH", "enabled": True}])

        assets.ensure_asset_repo.assert_called_once()

    def test_missing_service_raises_rather_than_falling_back(self):
        """The old code would have quietly fetched from the hub here. Failing
        is the point: a silent fallback is the bypass coming back."""
        with pytest.raises(RuntimeError) as exc:
            _resolve(None, [{"type": "depth", "enabled": True}])

        assert "ASSETS" in str(exc.value)

    def test_missing_service_is_fine_when_nothing_needs_weights(self):
        assert _resolve(None, [{"type": "canny", "enabled": True}]) is None


class TestNoHardcodedRepoIdReachesTheLibrary:
    def test_detector_loads_take_a_directory_argument(self):
        """`from_pretrained` must be handed the mirror directory, never a repo
        id - a repo id is what makes the library fetch for itself."""
        import inspect

        from src.pipelines.pipes.controlnet_preprocessor import main

        source = inspect.getsource(main)

        assert 'from_pretrained("lllyasviel/Annotators")' not in source
        assert source.count("from_pretrained(annotators)") == 7
