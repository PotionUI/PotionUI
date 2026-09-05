"""`_resolve_vram_limit_gb` must route the configured `vram_limit_gb` hint
through the GPU service's budget composition, not use it as a raw value -
otherwise a preset's pipe-level hint above the backend's configured cap would
plan a load above the backend's actual maximum."""

# This venv's accelerate/diffusers import chain (accelerate.utils.other) reads
# `numpy._core.multiarray` as an attribute access; that submodule is only
# populated on `numpy` once something has explicitly imported it, so the very
# first import of `diffusers.loaders` (pulled in transitively by
# checkpoint_loader.sdxl's package __init__) raises AttributeError unless this
# runs first.
import numpy._core.multiarray  # noqa: F401

from unittest.mock import Mock

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.checkpoint_loader.sdxl.main import CheckpointLoaderSDXLPipe


def make_pipe(vram_limit_gb=None):
    config = CheckpointLoaderSDXLPipe.get_default_config()
    config["model"] = {"file_path": "models/checkpoints/test.safetensors"}
    config["vram_limit_gb"] = vram_limit_gb
    return CheckpointLoaderSDXLPipe(config)


class TestResolveVramLimitGb:
    def test_configured_hint_is_composed_with_the_gpu_services_budget(self):
        pipe = make_pipe(vram_limit_gb=24)
        gpu_monitor = Mock()
        gpu_monitor.get_vram_budget.return_value = 8.0  # backend cap wins
        pipe_input = PipeInput(input={"GPU": gpu_monitor, "MEMORY": None})

        result = pipe._resolve_vram_limit_gb(pipe_input)

        gpu_monitor.get_vram_budget.assert_called_once_with(24)
        assert result == 8.0

    def test_no_configured_hint_still_asks_the_gpu_service(self):
        pipe = make_pipe(vram_limit_gb=None)
        gpu_monitor = Mock()
        gpu_monitor.get_vram_budget.return_value = 16.0
        pipe_input = PipeInput(input={"GPU": gpu_monitor, "MEMORY": None})

        result = pipe._resolve_vram_limit_gb(pipe_input)

        gpu_monitor.get_vram_budget.assert_called_once_with(None)
        assert result == 16.0

    def test_no_gpu_service_falls_back_to_the_raw_configured_value(self):
        pipe = make_pipe(vram_limit_gb=24)
        pipe_input = PipeInput(input={"GPU": None, "MEMORY": None})

        result = pipe._resolve_vram_limit_gb(pipe_input)

        assert result == 24

    def test_no_gpu_service_and_no_config_uses_the_conservative_default(self):
        pipe = make_pipe(vram_limit_gb=None)
        pipe_input = PipeInput(input={"GPU": None, "MEMORY": None})

        result = pipe._resolve_vram_limit_gb(pipe_input)

        assert result == 8.0
