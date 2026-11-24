import numpy  # noqa: F401
import numpy._core.multiarray  # noqa: F401

from contextlib import contextmanager
from unittest.mock import Mock

import pytest
import torch
from PIL import Image

from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import ImageGenerationOutput
from src.pipelines.pipes.generator.sdxl import main as generator_main
from src.pipelines.pipes.generator.sdxl.main import GeneratorSDXLPipe
from src.pipelines.pipes.generator.sdxl.pipeline import StableDiffusionXLKDiffusionPipeline
from src.pipelines.pipes.checkpoint_loader.sdxl.sdxl_model import SDXLModel


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


def _pipe(**ip_adapter):
    config = GeneratorSDXLPipe.get_default_config()
    config["quantity"] = 1
    config["ip_adapter"] = ip_adapter
    return GeneratorSDXLPipe(config)


def _model(seen):
    model = Mock()

    def txt2img(*args, **kwargs):
        seen.append(model.ip_adapter_embeds)
        return ImageGenerationOutput(image=Image.new("RGB", (64, 64)), seed=1)

    model.txt2img = txt2img
    return model


@pytest.fixture
def sessions(monkeypatch):
    calls = []

    @contextmanager
    def fake_session(unet, adapter, encoder, image, scale, device, dtype):
        calls.append({"unet": unet, "adapter": adapter, "encoder": encoder, "image": image, "scale": scale})
        calls.append("enter")
        try:
            yield ["embeds"]
        finally:
            calls.append("exit")

    monkeypatch.setattr(generator_main, "reference_session", fake_session)
    return calls


def _input(model, conditioning, **extra):
    return PipeInput(input={"model": model, "conditioning": [conditioning], "seed": [1], **extra})


def test_reference_embeds_are_live_only_while_the_images_render(conditioning, sessions):
    seen = []
    model = _model(seen)
    picture = Image.new("RGB", (32, 32))
    pipe = _pipe(adapter="/a.safetensors", image_encoder="/e.safetensors", scale=0.4)
    pipe.process(_input(model, conditioning, ip_image=[picture]), Mock(), is_cancelled=lambda: False)
    assert seen == [["embeds"]]
    assert model.ip_adapter_embeds is None
    first = sessions[0]
    assert first["unet"] is model.pipe.unet
    assert (first["adapter"], first["encoder"], first["scale"]) == ("/a.safetensors", "/e.safetensors", 0.4)
    assert first["image"] is picture
    assert sessions[1:] == ["enter", "exit"]


def test_the_adapter_is_released_when_sampling_fails(conditioning, sessions):
    model = Mock()
    model.txt2img = Mock(side_effect=RuntimeError("boom"))
    pipe = _pipe(adapter="/a.safetensors", image_encoder="/e.safetensors")
    with pytest.raises(RuntimeError, match="boom"):
        pipe.process(_input(model, conditioning, ip_image=[Image.new("RGB", (8, 8))]), Mock(), is_cancelled=lambda: False)
    assert sessions[-1] == "exit"
    assert model.ip_adapter_embeds is None


@pytest.mark.parametrize(
    "settings, extra",
    [
        ({}, {"ip_image": [Image.new("RGB", (8, 8))]}),
        ({"adapter": "", "image_encoder": "/e"}, {"ip_image": [Image.new("RGB", (8, 8))]}),
        ({"adapter": "/a", "image_encoder": "/e"}, {}),
        ({"adapter": "/a", "image_encoder": "/e"}, {"ip_image": []}),
        ({"adapter": "/a", "image_encoder": "/e"}, {"ip_image": [None]}),
    ],
)
def test_no_session_without_an_adapter_and_a_picture(conditioning, sessions, settings, extra):
    seen = []
    _pipe(**settings).process(_input(_model(seen), conditioning, **extra), Mock(), is_cancelled=lambda: False)
    assert seen == [None]
    assert sessions == []


def test_an_adapter_without_an_encoder_is_an_error(conditioning, sessions):
    pipe = _pipe(adapter="/a.safetensors", image_encoder="")
    with pytest.raises(ValueError, match="image encoder"):
        pipe.process(_input(_model([]), conditioning, ip_image=[Image.new("RGB", (8, 8))]), Mock(), is_cancelled=lambda: False)
    assert sessions == []


def test_a_reference_is_refused_next_to_self_attention_guidance(conditioning, sessions):
    model = _model([])
    model.denoising_hooks = {"sag": object()}
    pipe = _pipe(adapter="/a.safetensors", image_encoder="/e.safetensors")
    with pytest.raises(ValueError, match="Self-Attention"):
        pipe.process(_input(model, conditioning, ip_image=[Image.new("RGB", (8, 8))]), Mock(), is_cancelled=lambda: False)
    assert sessions == []


def test_the_pipe_declares_the_reference_input_and_setting():
    assert any(spec.name == "ip_image" for spec in GeneratorSDXLPipe.inputs())
    assert any(spec.name == "ip_adapter" for spec in GeneratorSDXLPipe.configuration())


def test_pipeline_prepares_negative_then_positive_embeds_for_cfg():
    embeds = torch.stack([torch.zeros(1, 4), torch.ones(1, 4)])
    out = StableDiffusionXLKDiffusionPipeline.prepare_ip_adapter_image_embeds(None, None, [embeds], "cpu", 3, True)
    assert out[0].shape == (6, 1, 4)
    assert torch.equal(out[0][:3], torch.zeros(3, 1, 4))
    assert torch.equal(out[0][3:], torch.ones(3, 1, 4))


def test_pipeline_keeps_only_the_positive_embed_without_cfg():
    embeds = torch.stack([torch.zeros(1, 4), torch.ones(1, 4)])
    out = StableDiffusionXLKDiffusionPipeline.prepare_ip_adapter_image_embeds(None, None, [embeds], "cpu", 2, False)
    assert out[0].shape == (2, 1, 4)
    assert torch.equal(out[0], torch.ones(2, 1, 4))


def test_pipeline_refuses_a_missing_reference():
    with pytest.raises(ValueError, match="precomputed"):
        StableDiffusionXLKDiffusionPipeline.prepare_ip_adapter_image_embeds(None, None, None, "cpu", 1, True)


def test_model_forwards_embeds_only_when_a_reference_is_set():
    model = SDXLModel.__new__(SDXLModel)
    model.ip_adapter_embeds = None
    assert model._ip_adapter_kwargs() == {}
    model.ip_adapter_embeds = ["e"]
    assert model._ip_adapter_kwargs() == {"ip_adapter_image_embeds": ["e"]}


def test_model_reads_the_union_control_index_of_the_first_controlnet():
    model = SDXLModel.__new__(SDXLModel)
    model.controlnets = None
    assert model._control_type_idx() is None
    model.controlnets = [{"control_type_idx": 3}]
    assert model._control_type_idx() == 3
    model.controlnets = [{"model": object()}]
    assert model._control_type_idx() is None


class _Rebuilt(Exception):
    pass


def _loaded_model(monkeypatch, current):
    model = SDXLModel.__new__(SDXLModel)
    model.config = {"path": "/ckpt.safetensors"}
    model.pipe = Mock()
    model.pipe.controlnet = current
    model.using_controlnet = True
    model.controlnets = [{"model": current, "control_type_idx": 0}]
    model.clear_cuda_cache = Mock()

    def from_single_file(**kwargs):
        raise _Rebuilt()

    monkeypatch.setattr(StableDiffusionXLKDiffusionPipeline, "from_single_file", from_single_file)
    return model


def test_the_same_controlnet_keeps_the_pipeline_and_takes_the_new_control_type(monkeypatch):
    net = torch.nn.Linear(1, 1)
    model = _loaded_model(monkeypatch, net)
    model.load_with_controlnet([{"model": net, "control_type_idx": 4}])
    assert model._control_type_idx() == 4
    assert model.pipe is not None


def test_a_different_controlnet_rebuilds_instead_of_reusing_the_stale_one(monkeypatch):
    model = _loaded_model(monkeypatch, torch.nn.Linear(1, 1))
    with pytest.raises(_Rebuilt):
        model.load_with_controlnet([{"model": torch.nn.Linear(1, 1), "control_type_idx": None}])


def test_a_union_controlnet_cannot_be_stacked_with_another(monkeypatch):
    net = torch.nn.Linear(1, 1)
    model = _loaded_model(monkeypatch, net)
    with pytest.raises(ValueError, match="union"):
        model.load_with_controlnet([{"model": net, "union": True}, {"model": torch.nn.Linear(1, 1)}])
