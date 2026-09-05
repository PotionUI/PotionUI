"""`_get_optimal_tile_grid` must route the configured `vram_limit_gb` hint
through the GPU service's budget composition, not use it as a raw value -
otherwise a preset's pipe-level hint above the backend's configured cap would
size tiles for more VRAM than the backend actually allows."""

from unittest.mock import Mock

from src.pipelines.pipes.tiled_detailer.sdxl.main import TiledDetailerSDXL


def make_pipe(vram_limit_gb=None):
    config = TiledDetailerSDXL.get_default_config()
    config["vram_limit_gb"] = vram_limit_gb
    return TiledDetailerSDXL(config)


class TestOptimalTileGridVramBudget:
    def test_configured_hint_is_composed_with_the_gpu_services_budget(self):
        pipe = make_pipe(vram_limit_gb=24)
        gpu_monitor = Mock()
        gpu_monitor.get_vram_budget.return_value = 8.0  # backend cap wins

        pipe._get_optimal_tile_grid((2048, 2048), gpu_monitor=gpu_monitor)

        gpu_monitor.get_vram_budget.assert_called_once_with(24)

    def test_no_configured_hint_still_asks_the_gpu_service(self):
        pipe = make_pipe(vram_limit_gb=None)
        gpu_monitor = Mock()
        gpu_monitor.get_vram_budget.return_value = 16.0

        pipe._get_optimal_tile_grid((2048, 2048), gpu_monitor=gpu_monitor)

        gpu_monitor.get_vram_budget.assert_called_once_with(None)

    def test_no_gpu_service_falls_back_to_the_raw_configured_value(self):
        pipe = make_pipe(vram_limit_gb=24)

        # Must not raise even without a gpu_monitor - the raw configured
        # value is used as-is (pre-existing behaviour, unchanged).
        pipe._get_optimal_tile_grid((2048, 2048), gpu_monitor=None)
