import numpy  # noqa: F401
import numpy._core.multiarray  # noqa: F401

from unittest.mock import Mock

import pytest
import torch
from PIL import Image

from src.pipelines.pipes.generator.sdxl.main import GeneratorSDXLPipe
from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import ImageGenerationOutput


@pytest.fixture(autouse=True)
def no_real_model_lifecycle(monkeypatch):
    monkeypatch.setattr(
        "src.platform.runtime.model_lifecycle.lifecycle.get_model_lifecycle",
        lambda: None,
    )


@pytest.fixture
def conditioning():
    c = Mock()
    c.embeds = {"embeds": torch.randn(1, 77, 2048), "pooled": torch.randn(1, 1280)}
    c.n_embeds = {"embeds": torch.randn(1, 77, 2048), "pooled": torch.randn(1, 1280)}
    return c


@pytest.fixture
def generator_pipe():
    config = GeneratorSDXLPipe.get_default_config()
    config["quantity"] = 1
    return GeneratorSDXLPipe(config)


def test_oom_during_sampling_frees_offload_hooks(generator_pipe, conditioning):
    model = Mock()
    model.txt2img = Mock(side_effect=torch.cuda.OutOfMemoryError("CUDA out of memory"))
    pipe_input = PipeInput(input={"model": model, "conditioning": [conditioning], "seed": [1]})

    with pytest.raises(torch.cuda.OutOfMemoryError):
        generator_pipe.process(pipe_input, Mock(), is_cancelled=lambda: False)

    model.pipe.maybe_free_model_hooks.assert_called_once()
    assert model.clear_cuda_cache.called


def test_success_also_frees_offload_hooks(generator_pipe, conditioning):
    model = Mock()
    model.txt2img = Mock(side_effect=lambda *a, **kw: ImageGenerationOutput(
        image=Image.new("RGB", (64, 64), color="red"), seed=1,
    ))
    pipe_input = PipeInput(input={"model": model, "conditioning": [conditioning], "seed": [1]})

    generator_pipe.process(pipe_input, Mock(), is_cancelled=lambda: False)

    model.pipe.maybe_free_model_hooks.assert_called_once()


def test_model_without_pipe_still_cleans_up(generator_pipe, conditioning):
    model = Mock(spec=["txt2img", "clear_cuda_cache"])
    model.txt2img = Mock(side_effect=RuntimeError("boom"))
    pipe_input = PipeInput(input={"model": model, "conditioning": [conditioning], "seed": [1]})

    with pytest.raises(RuntimeError):
        generator_pipe.process(pipe_input, Mock(), is_cancelled=lambda: False)

    assert model.clear_cuda_cache.called
