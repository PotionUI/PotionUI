# This container pairs numpy 1.26 with an accelerate that reads
# numpy._core.multiarray, so importing diffusers dies unless that submodule is
# wired up first, and numpy 1.26's _core shim only wires it once numpy itself
# has been imported. Both lines, in this order, make this file collectible on
# its own; a sibling that imports diffusers first still poisons the process, so
# the durable home for this is tests/conftest.py.
import numpy  # noqa: F401
import numpy._core.multiarray  # noqa: F401

"""End-to-end cancellation reachability through SDXLModel.

SDXLModel.txt2img (and its img2img/*_controlnet siblings) forward
`is_cancelled` into the diffusers-shaped pipe call; the pipe threads it into
`SDXLSamplerConfig.create_k_callback`, whose per-step closure raises
`SamplingCancelled`. `FakeCancellablePipe` stands in for
`StableDiffusionXLKDiffusionPipeline.__call__`'s sampling loop (real
`create_k_callback`, fake steps) so this proves the wiring without a real
UNet/GPU.
"""
from unittest.mock import Mock

import pytest
import torch

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.checkpoint_loader.sdxl.sdxl_model import SDXLModel
from src.pipelines.pipes.generator.sdxl.main import GeneratorSDXLPipe
from src.pipelines.pipes.generator.sdxl.sampler_config import SDXLSamplerConfig
from src.platform.runtime.native.errors import SamplingCancelled


class FakeCancellablePipe:
    """Mimics the cancellation surface of
    StableDiffusionXLKDiffusionPipeline.__call__: builds the real per-step
    callback and drives it through a fake step loop with no try/except
    around the call, matching vendor/k_diffusion's own sampler shape.
    """

    def __init__(self, steps=5):
        self.steps = steps

    def __call__(self, **params):
        k_callback = SDXLSamplerConfig.create_k_callback(
            params.get("callback_on_step_end"),
            params.get("callback_on_step_end_tensor_inputs") or ["latents"],
            self,
            torch.zeros(1), torch.zeros(1), torch.zeros(1), torch.zeros(1),
            is_cancelled=params.get("is_cancelled"),
        )
        denoised = torch.zeros(1, 4, 8, 8)  # (batch, channels, h, w) -- the real step_callback's
        # live-preview branch indexes [0] into this expecting a decodable latent.
        for i in range(self.steps):
            if k_callback is not None:
                k_callback({"x": torch.zeros(1), "i": i, "sigma": torch.tensor(1.0), "denoised": denoised})
        raise AssertionError("fake sampling loop completed without being cancelled")

    def img2img(self, **params):
        return self(**params)


def _make_model():
    return SDXLModel(
        template={"base": "SDXL", "name": "test", "file_path": "/models/checkpoints/test.safetensors"},
        config={"path": "x", "device": "cpu", "dtype": "float16", "nsfw": False, "loras": [], "extras": {}},
    )


def _conditioning():
    conditioning = Mock()
    conditioning.embeds = {"embeds": torch.randn(1, 77, 2048), "pooled": torch.randn(1, 1280)}
    conditioning.n_embeds = {"embeds": torch.randn(1, 77, 2048), "pooled": torch.randn(1, 1280)}
    return conditioning


def _make_probe(cancel_after: int):
    state = {"n": 0}

    def probe():
        state["n"] += 1
        return state["n"] > cancel_after

    return probe


class TestCancellationReachesRealSampler:
    def test_sdxl_model_txt2img_forwards_is_cancelled_and_raises_at_step_k(self):
        model = _make_model()
        model.pipe = FakeCancellablePipe(steps=5)
        generator_pipe = GeneratorSDXLPipe(GeneratorSDXLPipe.get_default_config())
        generation_input = generator_pipe._build_generation_input_txt2img(0, 123, [_conditioning()], 1)

        with pytest.raises(SamplingCancelled) as excinfo:
            model.txt2img(generation_input, Mock(), is_cancelled=_make_probe(cancel_after=2))

        assert excinfo.value.step_index == 2

    def test_process_introspection_bridge_forwards_is_cancelled_through_real_sdxl_model(self):
        model = _make_model()
        model.pipe = FakeCancellablePipe(steps=5)
        generator_pipe = GeneratorSDXLPipe(GeneratorSDXLPipe.get_default_config())
        pipe_input = PipeInput(input={
            "model": model, "conditioning": [_conditioning()], "seed": [123],
        })

        # cancel_after=2: call 1 is main.py's own between-images check (still
        # False, since this is the only/first image); calls 2-3 are the
        # sampler's step 0 and step 1 -- step 1 is where it fires, proving the
        # probe main.py forwards is the SAME one the sampler callback sees.
        with pytest.raises(SamplingCancelled) as excinfo:
            generator_pipe.process(pipe_input, Mock(), is_cancelled=_make_probe(cancel_after=2))

        assert excinfo.value.step_index == 1
