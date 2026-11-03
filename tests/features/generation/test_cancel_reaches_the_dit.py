from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import torch

from src.features.generation.engine import GenerationEngine
from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import ErrorGenerationOutput, ProgressGenerationOutput
from src.pipelines.pipes.generator.flux.main import GeneratorFluxPipe
from src.pipelines.pipes.generator.txt2vid_wan22.main import GeneratorWanTxt2VidPipe
from src.platform.runtime.native.arch.flux.model import Flux
from src.platform.runtime.native.engine import NativeEngineLoader
from src.platform.runtime.native.kernels import disable_weight_init
from src.platform.runtime.native.vae import ae_2d
from src.platform.runtime.native.vae.ae_2d import AutoEncoder2D

from tests.platform.runtime.native.arch.test_wan_model import TINY_T2V
from tests.platform.runtime.native.arch.test_wan_model import _build as _build_wan
from tests.platform.runtime.native.test_engine import _FLUX1, _VAE, _StubTE, _build_and_save

GENERATION_ID = "gen-under-test"


def _engine(pipe_class) -> GenerationEngine:
    deps = {name: Mock() for name in (
        "gpu", "pipe_catalog", "settings", "system_monitor", "memory_advisor", "llm_service",
    )}
    deps["pipe_catalog"].get_pipe.return_value = pipe_class
    return GenerationEngine(**deps, plugin_registry=None)


def _run(engine: GenerationEngine, config: dict) -> list:
    outputs: list = []
    pipes = [{"name": "generator", "enabled": True, "input": [], "cache": [], "config": config}]
    with patch.object(engine, "validate_pipeline"):
        engine.generate(pipes, outputs.append, GENERATION_ID)
    return outputs


def _cancel_after_block(engine: GenerationEngine, block, ran: list, tag: int) -> None:
    forward = block.forward

    def forward_then_cancel(*args, **kwargs):
        out = forward(*args, **kwargs)
        ran.append(tag)
        engine.cancel(GENERATION_ID)
        return out

    block.forward = forward_then_cancel


def _count_block(block, ran: list, tag: int) -> None:
    forward = block.forward

    def counting(*args, **kwargs):
        ran.append(tag)
        return forward(*args, **kwargs)

    block.forward = counting


def _closing_outputs(outputs: list) -> list:
    return [
        (output.state, output.icon)
        for output in outputs
        if isinstance(output, ProgressGenerationOutput) and "cancelled" in output.state.lower()
    ]


def _assert_cancelled(outputs: list, engine: GenerationEngine) -> None:
    assert [state for state, _icon in _closing_outputs(outputs)] == ["Generation cancelled"]
    assert not any(isinstance(output, ErrorGenerationOutput) for output in outputs)
    assert engine.running_generation_id is None


@dataclass
class _WanSpec:
    variant: str = "wan_t2v_tiny"
    family: str = "wan"
    sampling_settings: dict = field(default_factory=lambda: {"guidance": "cfg", "shift": 8.0, "expert_boundary": 0.875})
    latent_format: dict = field(default_factory=lambda: {"latent_channels": 16, "format": "wan21"})


def _wan_engine():
    module = _build_wan(TINY_T2V)
    dit = SimpleNamespace(
        compute_dtype=torch.float32, spec=_WanSpec(), module=module,
        move_to=lambda device: None, offload=lambda: None,
    )
    bundle = SimpleNamespace(
        high_dit=dit, low_dit=None, vae=SimpleNamespace(offload=lambda: None),
        spec=_WanSpec(), is_dual_expert=False,
    )
    conditioning = SimpleNamespace(embeds={"context": torch.randn(1, 4, 32)}, n_embeds={})

    class _Pipe(GeneratorWanTxt2VidPipe):
        def process(self, pipe_input, generation_outputs, is_cancelled=None):
            pipe_input = PipeInput(input={"model": bundle, "conditioning": [conditioning], "seed": [7]})
            return super().process(pipe_input, generation_outputs, is_cancelled=is_cancelled)

    return _engine(_Pipe), module


_WAN_CONFIG = {
    "device": "cpu", "steps": 3, "cfg": 1.0, "sampler": "euler", "resolution": "32x32", "frames": 5, "preview": False,
}


def test_a_video_cancel_goes_from_the_engine_to_the_next_wan_block():
    engine, module = _wan_engine()
    ran: list = []
    _cancel_after_block(engine, module.blocks[0], ran, 0)
    _count_block(module.blocks[1], ran, 1)

    outputs = _run(engine, _WAN_CONFIG)

    assert ran == [0]
    _assert_cancelled(outputs, engine)


def test_a_video_cancel_inside_a_step_closes_the_run_like_one_between_two_steps():
    inside, inside_module = _wan_engine()
    _cancel_after_block(inside, inside_module.blocks[0], [], 0)
    between, between_module = _wan_engine()
    between_ran: list = []
    _count_block(between_module.blocks[0], between_ran, 0)
    _cancel_after_block(between, between_module.blocks[1], between_ran, 1)

    closed_inside = _run(inside, _WAN_CONFIG)
    closed_between = _run(between, _WAN_CONFIG)

    assert between_ran == [0, 1]
    assert _closing_outputs(closed_inside) == _closing_outputs(closed_between)
    _assert_cancelled(closed_inside, inside)
    _assert_cancelled(closed_between, between)


@pytest.fixture(scope="module")
def flux_files(tmp_path_factory):
    patcher = pytest.MonkeyPatch()
    patcher.setattr(ae_2d, "_CH", 32)
    folder = tmp_path_factory.mktemp("cancel")
    _build_and_save(Flux.from_config(_FLUX1, disable_weight_init), folder / "dit.safetensors")
    _build_and_save(AutoEncoder2D.from_config(_VAE, disable_weight_init), folder / "vae.safetensors")
    yield folder
    patcher.undo()


def _flux_engine(folder):
    loader = NativeEngineLoader()
    dit = loader.load(folder / "dit.safetensors", "diffusion_model")
    vae = loader.load(folder / "vae.safetensors", "vae")
    bundle = SimpleNamespace(dit=dit, te_encoder=_StubTE(), vae=vae)
    conditioning = SimpleNamespace(embeds={"context": torch.randn(1, 4, 32), "pooled": torch.randn(1, 768)}, n_embeds={})

    class _Pipe(GeneratorFluxPipe):
        def process(self, pipe_input, generation_outputs, is_cancelled=None):
            pipe_input = PipeInput(input={"model": bundle, "conditioning": [conditioning], "seed": [7]})
            return super().process(pipe_input, generation_outputs, is_cancelled=is_cancelled)

    return _engine(_Pipe), dit.module


_FLUX_CONFIG = {
    "device": "cpu", "steps": 3, "guidance": 1.0, "sampler": "euler", "resolution": "64x64", "preview": False,
}


def test_an_image_cancel_goes_from_the_engine_to_the_next_flux_block(flux_files):
    engine, module = _flux_engine(flux_files)
    ran: list = []
    _cancel_after_block(engine, module.double_blocks[0], ran, 0)
    _count_block(module.single_blocks[0], ran, 1)

    outputs = _run(engine, _FLUX_CONFIG)

    assert ran == [0]
    _assert_cancelled(outputs, engine)


def test_an_image_cancel_inside_a_step_closes_the_run_like_one_between_two_steps(flux_files):
    inside, inside_module = _flux_engine(flux_files)
    _cancel_after_block(inside, inside_module.double_blocks[0], [], 0)
    between, between_module = _flux_engine(flux_files)
    between_ran: list = []
    _count_block(between_module.double_blocks[0], between_ran, 0)
    _cancel_after_block(between, between_module.single_blocks[0], between_ran, 1)

    closed_inside = _run(inside, _FLUX_CONFIG)
    closed_between = _run(between, _FLUX_CONFIG)

    assert between_ran == [0, 1]
    assert _closing_outputs(closed_inside) == _closing_outputs(closed_between)
    _assert_cancelled(closed_inside, inside)
    _assert_cancelled(closed_between, between)
