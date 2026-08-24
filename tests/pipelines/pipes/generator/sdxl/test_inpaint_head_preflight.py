"""Tests for the inpaint-head pre-flight in `generator/sdxl`.

The head used to be fetched by `InpaintHeadLoader._download_model` with a raw
`requests.get`, deep inside `SDXLModelWrapper` construction where no service is
reachable. The pipe now fetches it through the ASSETS service before starting a
masked generation, and the loader only loads.
"""

from unittest.mock import Mock

import pytest

from src.pipelines.pipes.generator.sdxl.inpaint_head import (
    INPAINT_HEAD_FILENAME,
    INPAINT_HEAD_SUBDIR,
    INPAINT_HEAD_URL,
    inpaint_head_path,
)
from src.pipelines.pipes.generator.sdxl.main import GeneratorSDXLPipe

_ensure = GeneratorSDXLPipe._ensure_inpaint_head


def _assets():
    assets = Mock()
    assets.ensure_asset_file = Mock(return_value="/depot/inpaint/fooocus_inpaint_head.pth")
    return assets


class TestInpaintHeadPreflight:
    def test_no_fetch_without_a_mask(self):
        """img2img without a mask never builds an inpaint head."""
        assets = _assets()

        _ensure(assets, None)

        assets.ensure_asset_file.assert_not_called()

    def test_masked_generation_fetches_the_head(self):
        assets = _assets()

        _ensure(assets, Mock(name="mask"))

        assets.ensure_asset_file.assert_called_once()

    def test_fetch_targets_the_path_the_loader_will_read(self):
        """The fetch coordinates and the load path must agree; if they drift,
        every masked generation downloads to one place and loads from another."""
        assets = _assets()

        _ensure(assets, Mock(name="mask"))

        call = assets.ensure_asset_file.call_args
        assert call.args[0] == INPAINT_HEAD_URL
        assert call.kwargs["subdir"] == INPAINT_HEAD_SUBDIR
        assert call.kwargs["filename"] == INPAINT_HEAD_FILENAME

        expected = inpaint_head_path("/depot")
        assert expected.parent.name == call.kwargs["subdir"]
        assert expected.name == call.kwargs["filename"]

    def test_destination_is_depot_relative(self):
        assets = _assets()

        _ensure(assets, Mock(name="mask"))

        assert not assets.ensure_asset_file.call_args.kwargs["subdir"].startswith("/")

    def test_missing_service_does_not_raise_here(self):
        """Deferred on purpose: the loader's FileNotFoundError names the path,
        which is more useful than a service error raised earlier."""
        _ensure(None, Mock(name="mask"))

    def test_fetch_failure_propagates(self):
        """A failed fetch must fail the generation - continuing would reach the
        loader and fail there anyway, with a less specific cause."""
        assets = _assets()
        assets.ensure_asset_file.side_effect = RuntimeError("depot full")

        with pytest.raises(RuntimeError, match="depot full"):
            _ensure(assets, Mock(name="mask"))
