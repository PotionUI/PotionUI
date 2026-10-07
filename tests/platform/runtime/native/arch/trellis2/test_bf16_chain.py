from types import SimpleNamespace

import pytest
import torch
import torch.nn as nn
from PIL import Image
from safetensors.torch import save_file

from src.platform.runtime.native.arch.trellis2 import load as trellis2_load
from src.platform.runtime.native.arch.trellis2.conditioner import DinoV3ImageConditioner, build_dino_v3
from src.platform.runtime.native.arch.trellis2.config import (
    DinoV3Config,
    OctreeVaeDecoderConfig,
    SLatFlowConfig,
    SSFlowConfig,
    SSVAEDecoderConfig,
    StageSampling,
)
from src.platform.runtime.native.arch.trellis2.detect import (
    FLOW_PREFIXES,
    SHAPE_DECODER_PREFIX,
    STRUCTURE_DECODER_PREFIX,
    TEXTURE_DECODER_PREFIX,
)
from src.platform.runtime.native.arch.trellis2.image_to_mesh import _decode
from src.platform.runtime.native.arch.trellis2.octree_vae import FlexiDualGridVaeDecoder, SparseUnetVaeDecoder
from src.platform.runtime.native.arch.trellis2.sampling import sample_flow_stage
from src.platform.runtime.native.arch.trellis2.slat_flow import SLatFlowModel
from src.platform.runtime.native.arch.trellis2.ss_flow import SSFlowDiT
from src.platform.runtime.native.arch.trellis2.ss_vae import SSVAEDecoder
from src.platform.runtime.native.sparse3d import SparseConv3d, SparseTensor
from vendor.gpl.comfyui.ops import pick_operations

BF16 = torch.bfloat16
DINO = DinoV3Config(hidden_size=64, num_hidden_layers=2, num_attention_heads=4, intermediate_size=128)
SS_FLOW = SSFlowConfig(
    resolution=4, in_channels=4, model_channels=16, cond_channels=64, out_channels=4,
    num_blocks=1, num_heads=2, share_mod=True, qk_rms_norm=True, qk_rms_norm_cross=True,
)
SS_VAE = SSVAEDecoderConfig(
    out_channels=1, latent_channels=4, num_res_blocks=1, channels=(8, 4), norm_type="layer",
)
SHAPE_FLOW = SLatFlowConfig(
    resolution=8, in_channels=8, out_channels=8, model_channels=16, cond_channels=64,
    num_blocks=1, num_heads=2, share_mod=True, qk_rms_norm=True, qk_rms_norm_cross=True,
)
TEX_FLOW = SLatFlowConfig(
    resolution=8, in_channels=16, out_channels=8, model_channels=16, cond_channels=64,
    num_blocks=1, num_heads=2, share_mod=True, qk_rms_norm=True, qk_rms_norm_cross=True,
)
OCTREE = OctreeVaeDecoderConfig(model_channels=(16, 8), latent_channels=8, num_blocks=(1, 0))
GUIDED = StageSampling(steps=2, guidance_strength=3.0, guidance_rescale=0.5, guidance_interval=(0.0, 1.0), rescale_t=3.0)
UNGUIDED = StageSampling(steps=2, guidance_strength=1.0, guidance_rescale=0.0, guidance_interval=(0.0, 1.0), rescale_t=3.0)
MATMUL_LAYERS = (nn.Linear, nn.Conv3d, SparseConv3d)


def _randomise(module):
    generator = torch.Generator().manual_seed(0)
    with torch.no_grad():
        for p in module.parameters():
            p.copy_(torch.randn(p.shape, generator=generator) * 0.2)
    return module


def _save_bf16(path, prefix, module):
    save_file(
        {
            prefix + k: v.to(BF16).contiguous()
            for k, v in module.state_dict().items()
            if v.is_floating_point() and not v.is_complex()
        },
        str(path),
    )
    return path


@pytest.fixture(scope="module")
def components(tmp_path_factory):
    root = tmp_path_factory.mktemp("trellis2_bf16")
    fp32_ops = pick_operations(torch.float32, torch.float32)

    dino = _randomise(build_dino_v3(DINO))
    save_file(
        {
            (k[len("model."):] if k.startswith("model.layer.") else k): v.to(BF16).contiguous()
            for k, v in dino.state_dict().items()
        },
        str(root / "dino.safetensors"),
    )
    ss_flow = SSFlowDiT(SS_FLOW, fp32_ops)
    ss_flow.post_load()
    _save_bf16(root / "bundle_ss.safetensors", FLOW_PREFIXES["structure"], _randomise(ss_flow))
    _save_bf16(root / "shape_vae_ss.safetensors", STRUCTURE_DECODER_PREFIX, _randomise(SSVAEDecoder(SS_VAE, fp32_ops)))
    _save_bf16(root / "bundle_shape.safetensors", FLOW_PREFIXES["shape_512"], _randomise(SLatFlowModel(**SHAPE_FLOW.as_kwargs())))
    _save_bf16(root / "bundle_tex.safetensors", FLOW_PREFIXES["texture"], _randomise(SLatFlowModel(**TEX_FLOW.as_kwargs())))
    _save_bf16(
        root / "shape_vae_dec.safetensors", SHAPE_DECODER_PREFIX,
        _randomise(FlexiDualGridVaeDecoder(OCTREE, resolution=16)),
    )
    _save_bf16(
        root / "texture_vae.safetensors", TEXTURE_DECODER_PREFIX,
        _randomise(SparseUnetVaeDecoder(OCTREE, out_channels=6, pred_subdiv=False)),
    )

    return SimpleNamespace(
        conditioner=DinoV3ImageConditioner.from_file(root / "dino.safetensors", DINO, dtype=BF16),
        ss_flow=trellis2_load.load_ss_flow(root / "bundle_ss.safetensors", SS_FLOW, dtype=BF16),
        ss_vae=trellis2_load.load_ss_vae_decoder(root / "shape_vae_ss.safetensors", SS_VAE, dtype=BF16),
        shape_flow=trellis2_load.load_shape_slat_flow(root / "bundle_shape.safetensors", "512", SHAPE_FLOW, dtype=BF16),
        tex_flow=trellis2_load.load_tex_slat_flow(root / "bundle_tex.safetensors", "512", TEX_FLOW, dtype=BF16),
        shape_decoder=trellis2_load.load_shape_slat_decoder(
            root / "shape_vae_dec.safetensors", OCTREE, resolution=16, dtype=BF16,
        ),
        tex_decoder=trellis2_load.load_tex_slat_decoder(root / "texture_vae.safetensors", OCTREE, dtype=BF16),
    )


def _record_matmul_inputs(named_modules):
    seen = {}
    handles = []
    for name, module in named_modules.items():
        seen[name] = set()
        for layer in module.modules():
            if isinstance(layer, MATMUL_LAYERS):
                def hook(_layer, args, _name=name):
                    value = args[0].feats if isinstance(args[0], SparseTensor) else args[0]
                    seen[_name].add(value.dtype)
                handles.append(layer.register_forward_pre_hook(hook))
    return seen, handles


def test_every_component_of_an_all_bf16_checkpoint_loads_with_bf16_weights(components):
    for name in ("ss_flow", "ss_vae", "shape_flow", "tex_flow", "shape_decoder", "tex_decoder"):
        dtypes = {p.dtype for p in getattr(components, name).parameters()}
        assert dtypes == {BF16}, name
    assert components.conditioner.dtype == BF16


def test_an_all_bf16_checkpoint_runs_end_to_end_with_bf16_activations(components):
    named = {
        "conditioner": components.conditioner.model,
        "ss_flow": components.ss_flow,
        "ss_vae": components.ss_vae,
        "shape_flow": components.shape_flow,
        "tex_flow": components.tex_flow,
        "shape_decoder": components.shape_decoder,
        "tex_decoder": components.tex_decoder,
    }
    seen, handles = _record_matmul_inputs(named)
    try:
        with torch.no_grad():
            cond = components.conditioner.encode(Image.new("RGB", (64, 64), (90, 40, 160)), 64)
            neg = torch.zeros_like(cond)
            generator = torch.Generator().manual_seed(1)

            noise = torch.randn(1, SS_FLOW.in_channels, *([SS_FLOW.resolution] * 3), generator=generator).to(cond.dtype)
            latent = sample_flow_stage(components.ss_flow, noise, cond, neg, GUIDED)
            occupancy = components.ss_vae(latent)

            axis = torch.arange(3)
            grid = torch.stack(torch.meshgrid(axis, axis, axis, indexing="ij"), dim=-1).reshape(-1, 3)
            coords = torch.cat([torch.zeros(grid.shape[0], 1, dtype=torch.int32), grid.int()], dim=1)
            shape_noise = SparseTensor(
                feats=torch.randn(coords.shape[0], SHAPE_FLOW.in_channels, generator=generator).to(cond.dtype),
                coords=coords,
            )
            shape_slat = sample_flow_stage(components.shape_flow, shape_noise, cond, neg, GUIDED)
            tex_noise = shape_slat.replace(
                torch.randn(coords.shape[0], TEX_FLOW.in_channels - SHAPE_FLOW.out_channels, generator=generator).to(cond.dtype)
            )
            tex_slat = sample_flow_stage(
                components.tex_flow, tex_noise, cond, neg, UNGUIDED, forward_kwargs={"concat_cond": shape_slat},
            )
            volume = _decode(components, shape_slat, tex_slat, 16, "cpu", None)
    finally:
        for handle in handles:
            handle.remove()

    assert cond.dtype == BF16
    assert latent.dtype == BF16
    assert occupancy.dtype == BF16
    assert shape_slat.dtype == BF16
    assert tex_slat.dtype == BF16
    assert volume.vertices.dtype == torch.float32
    assert volume.attrs.shape[1] == 6
    for name, dtypes in seen.items():
        assert dtypes == {BF16}, f"{name} matmul inputs were {sorted(map(str, dtypes))}"
