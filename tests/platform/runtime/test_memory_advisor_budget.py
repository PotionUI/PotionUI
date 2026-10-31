from unittest.mock import Mock

import pytest

from src.platform.runtime.memory_advisor import UNKNOWN_VRAM_TILING_BUDGET_GB, MemoryAdvisor


def _advisor(budget):
    gpu = Mock()
    gpu.get_vram_budget.return_value = budget
    gpu.estimate_vram_usage.side_effect = lambda w, h, model_type: (w * h) / (1024 * 1024) * 3.0
    return MemoryAdvisor(gpu, Mock())


@pytest.mark.parametrize("method", ["calculate_optimal_tile_size", "calculate_optimal_tile_count"])
def test_an_unknown_gpu_budget_tiles_like_the_default_budget(method):
    unknown = getattr(_advisor(None), method)((4096, 4096))
    default = getattr(_advisor(UNKNOWN_VRAM_TILING_BUDGET_GB), method)((4096, 4096))

    assert unknown == default
