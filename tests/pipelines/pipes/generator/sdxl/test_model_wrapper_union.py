import json
from types import SimpleNamespace

import pytest
import torch
from diffusers import ControlNetUnionModel
from torch import nn

from src.pipelines.pipes.controlnet_loader.sdxl.main import ASSETS
from src.pipelines.pipes.generator.sdxl.model_wrapper import ControlNetConfig, SDXLModelWrapper


@pytest.fixture(autouse=True)
def mock_cuda_for_tests():
    yield


class _Unet(nn.Module):
    def __init__(self):
        super().__init__()
        self.proj = nn.Linear(4, 4)


def _tiny_union():
    config = json.loads((ASSETS / "large" / "config.json").read_text())
    config.update(
        block_out_channels=[32, 64, 128],
        attention_head_dim=[1, 2, 4],
        transformer_layers_per_block=[1, 1, 1],
        conditioning_embedding_out_channels=[4, 8, 16, 32],
        norm_num_groups=8,
        addition_time_embed_dim=32,
        projection_class_embeddings_input_dim=1280 + 6 * 32,
        layers_per_block=1,
        num_control_type=8,
        num_trans_channel=32,
        num_proj_channel=32,
        num_trans_head=2,
        num_trans_layer=1,
    )
    return ControlNetUnionModel.from_config(config).eval()


def _wrapper(controlnet, idx, cfg=True, scale=0.7):
    batch = 4 if cfg else 2
    return SDXLModelWrapper(
        unet=_Unet(),
        scheduler=SimpleNamespace(alphas_cumprod=torch.linspace(0.9991, 0.0047, 10)),
        prompt_embeds=torch.randn(batch, 3, 2048),
        add_text_embeds=torch.randn(batch, 1280),
        add_time_ids=torch.randn(batch, 6),
        num_inference_steps=10,
        guidance_scale=5.0,
        do_classifier_free_guidance=cfg,
        controlnet_config=ControlNetConfig(
            controlnet=controlnet,
            control_image=torch.rand(2, 3, 64, 64),
            controlnet_conditioning_scale=scale,
            control_type_idx=idx,
        ),
    )


class _Recorder(nn.Module):
    def __init__(self, num_control_type=8):
        super().__init__()
        self.config = SimpleNamespace(num_control_type=num_control_type)
        self.weight = nn.Parameter(torch.zeros(1))
        self.seen = {}

    def forward(self, sample, timestep, encoder_hidden_states, controlnet_cond, **kwargs):
        self.seen = {"cond": controlnet_cond, **kwargs}
        return [sample], sample


def test_union_forward_gets_a_one_hot_control_type_row_per_batch_item():
    recorder = _Recorder()
    wrapper = _wrapper(recorder, 4)
    wrapper._apply_controlnet(torch.randn(4, 4, 8, 8), torch.full((4,), 3.0))
    control_type = recorder.seen["control_type"]
    assert control_type.shape == (4, 8)
    assert torch.equal(control_type.sum(1), torch.ones(4))
    assert torch.equal(control_type.argmax(1), torch.full((4,), 4))
    assert recorder.seen["control_type_idx"] == [4]


def test_union_forward_wraps_the_image_and_scale_in_lists():
    recorder = _Recorder()
    wrapper = _wrapper(recorder, 1, scale=0.35)
    wrapper._apply_controlnet(torch.randn(4, 4, 8, 8), torch.full((4,), 3.0))
    assert isinstance(recorder.seen["cond"], list) and len(recorder.seen["cond"]) == 1
    assert recorder.seen["cond"][0].shape[0] == 4
    assert recorder.seen["conditioning_scale"] == [pytest.approx(0.35)]


def test_plain_controlnet_path_is_untouched_when_no_union_index():
    class Plain(nn.Module):
        def __init__(self):
            super().__init__()
            self.weight = nn.Parameter(torch.zeros(1))
            self.kwargs = None

        def forward(self, sample, timestep, encoder_hidden_states, controlnet_cond, conditioning_scale, added_cond_kwargs, return_dict):
            self.kwargs = {"cond": controlnet_cond, "scale": conditioning_scale}
            return [sample], sample

    plain = Plain()
    wrapper = _wrapper(plain, None)
    wrapper._apply_controlnet(torch.randn(4, 4, 8, 8), torch.full((4,), 3.0))
    assert not isinstance(plain.kwargs["cond"], list)
    assert plain.kwargs["scale"] == 0.7


def test_real_union_model_accepts_the_wrapper_call_and_the_type_changes_the_residuals():
    torch.manual_seed(0)
    controlnet = _tiny_union()
    with torch.no_grad():
        controlnet.spatial_ch_projs.weight.normal_()
        controlnet.task_embedding.normal_()
        controlnet.controlnet_mid_block.weight.normal_()
        for block in controlnet.controlnet_down_blocks:
            block.weight.normal_()
    latents = torch.randn(4, 4, 8, 8)
    timestep = torch.full((4,), 3.0)
    outs = []
    for idx in (0, 3):
        wrapper = _wrapper(controlnet, idx)
        torch.manual_seed(1)
        wrapper.prompt_embeds = torch.ones(4, 3, 2048)
        wrapper.add_text_embeds = torch.ones(4, 1280)
        wrapper.add_time_ids = torch.ones(4, 6)
        with torch.no_grad():
            down, mid = wrapper._apply_controlnet(latents, timestep)
        assert len(down) == len(controlnet.controlnet_down_blocks)
        assert mid.shape[0] == 4
        outs.append(mid)
    assert not torch.allclose(outs[0], outs[1])


def test_cleanup_releases_a_union_model():
    wrapper = _wrapper(_Recorder(), 2)
    wrapper.cleanup()
    assert wrapper.control_image is None
