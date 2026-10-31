from types import SimpleNamespace

import pytest
import torch
from torch import nn

from src.pipelines.pipes.generator.sdxl.model_wrapper import ControlNetConfig, SDXLModelWrapper


class _TinyUnet(nn.Module):
    def __init__(self, dtype):
        super().__init__()
        self.proj = nn.Linear(4, 4).to(dtype)


class _TinyControlNet(nn.Module):
    def __init__(self, dtype):
        super().__init__()
        self.sample = nn.Linear(4, 4).to(dtype)
        self.time = nn.Linear(1, 4).to(dtype)
        self.text = nn.Linear(4, 4).to(dtype)
        self.pooled = nn.Linear(4, 4).to(dtype)
        self.ids = nn.Linear(6, 4).to(dtype)
        self.cond = nn.Linear(4, 4).to(dtype)
        self.seen = {}

    def forward(self, sample, timestep, encoder_hidden_states, controlnet_cond, conditioning_scale, added_cond_kwargs, return_dict):
        hidden = (
            self.sample(sample)
            + self.time(timestep.reshape(-1, 1))
            + self.text(encoder_hidden_states).mean(1)
            + self.pooled(added_cond_kwargs["text_embeds"])
            + self.ids(added_cond_kwargs["time_ids"])
            + self.cond(controlnet_cond)
        )
        self.seen["weights"] = self.sample.weight.dtype
        return [hidden.float(), hidden.float() * 2], hidden.float() * 3


def _wrapper(unet_dtype, controlnet_dtype, tensor_dtype):
    controlnet = _TinyControlNet(controlnet_dtype)
    wrapper = SDXLModelWrapper(
        unet=_TinyUnet(unet_dtype),
        scheduler=SimpleNamespace(alphas_cumprod=torch.linspace(0.9991, 0.0047, 10)),
        prompt_embeds=torch.randn(2, 3, 4, dtype=tensor_dtype),
        add_text_embeds=torch.randn(2, 4, dtype=tensor_dtype),
        add_time_ids=torch.randn(2, 6, dtype=tensor_dtype),
        num_inference_steps=10,
        guidance_scale=5.0,
        do_classifier_free_guidance=False,
        controlnet_config=ControlNetConfig(
            controlnet=controlnet,
            control_image=torch.randn(2, 4, dtype=tensor_dtype),
        ),
    )
    return wrapper, controlnet


@pytest.mark.parametrize("unet_dtype", [torch.float16, torch.bfloat16, torch.float32])
@pytest.mark.parametrize("controlnet_dtype", [torch.float16, torch.bfloat16, torch.float32])
@pytest.mark.parametrize("tensor_dtype", [torch.float32, torch.float16])
def test_controlnet_runs_in_unet_dtype(unet_dtype, controlnet_dtype, tensor_dtype):
    wrapper, controlnet = _wrapper(unet_dtype, controlnet_dtype, tensor_dtype)
    latents = torch.randn(2, 4, dtype=unet_dtype)
    timestep = torch.tensor([5, 5], dtype=torch.float32)
    down, mid = wrapper._apply_controlnet(latents, timestep)
    assert controlnet.seen["weights"] == unet_dtype
    assert mid.dtype == unet_dtype
    assert all(sample.dtype == unet_dtype for sample in down)
