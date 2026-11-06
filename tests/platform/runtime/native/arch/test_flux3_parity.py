from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import torch

from src.platform.runtime.native.arch.flux3.model import Flux3, convert_flux3_state_dict
from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.kernels import pick_operations

_FIXTURE = Path(__file__).parent / "fixtures" / "flux3_reference_dit.py"

HIDDEN, HEADS, DEPTH, DEPTH_SINGLE, MLP_RATIO = 64, 2, 1, 2, 3.0
AXES_DIM = (8, 8, 8, 8)
IN_CH, CTX = 16, 32

CONFIG = {
    "image_model": "flux3", "hidden_size": HIDDEN, "context_in_dim": CTX, "in_channels": IN_CH,
    "num_heads": HEADS, "head_dim": HIDDEN // HEADS, "depth": DEPTH, "depth_single_blocks": DEPTH_SINGLE,
    "mlp_hidden": int(HIDDEN * MLP_RATIO), "axes_dim": list(AXES_DIM), "theta": 10000,
    "flow_map": False, "has_image_cond": True,
}


def _load_reference(shrink: bool):
    spec = importlib.util.spec_from_file_location(f"flux3_reference_dit_{'tiny' if shrink else 'full'}", str(_FIXTURE))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if shrink:
        module.HIDDEN, module.HEADS, module.DEPTH, module.DEPTH_SINGLE = HIDDEN, HEADS, DEPTH, DEPTH_SINGLE
        module.AXES_DIM, module.MLP_RATIO = AXES_DIM, MLP_RATIO
        module.IN_CHANNELS = {"image": IN_CH, "image_cond": IN_CH, "text": CTX}
    return module


@pytest.fixture(scope="module")
def upstream_module():
    return _load_reference(shrink=True)


def _build_reference(upstream_module, flow_map):
    torch.manual_seed(0)
    ref = upstream_module.DiT(flow_map=flow_map)
    for name, param in ref.named_parameters():
        if name.endswith("norm.query_norm.scale") or name.endswith("norm.key_norm.scale"):
            param.data.uniform_(0.5, 1.5)
    return ref.eval()


def _build_ours(state_dict, flow_map):
    config = {**CONFIG, "flow_map": flow_map}
    ops = pick_operations(torch.float32, torch.float32)
    model = Flux3.from_config(config, ops)
    load_into_module(model, convert_flux3_state_dict(state_dict), match_model_spec(config))
    return model.eval()


def _grid_ids(h, w, layer=0.0):
    rows, cols = torch.meshgrid(torch.arange(h), torch.arange(w), indexing="ij")
    ids = torch.stack([torch.zeros_like(rows), rows, cols, torch.zeros_like(rows)], dim=-1).reshape(h * w, 4).float()
    ids[:, 1:3] *= 0.5
    ids[:, 3] = layer
    return ids


def _text_ids(length):
    positions = torch.arange(length)
    return torch.stack([torch.zeros_like(positions)] * 3 + [positions], dim=-1).float()


def _pack(upstream_module, samples):
    modes = upstream_module.MODES
    data = {m: [] for m in modes}
    ids = {m: [] for m in modes}
    mode_indices = {m: [] for m in modes}
    counts = dict.fromkeys(modes, 0)
    for text, cond, image in samples:
        for mode, item in (("text", text), ("image_cond", cond), ("image", image)):
            start = counts[mode]
            if item is not None:
                data[mode].append(item[0])
                ids[mode].append(item[1])
                counts[mode] += item[0].shape[0]
            mode_indices[mode].append((start, counts[mode]))
    width = {"image": IN_CH, "image_cond": IN_CH, "text": CTX}
    cat = lambda parts, w: torch.cat(parts) if parts else torch.zeros((0, w))
    data = {m: cat(data[m], width[m]) for m in modes}
    ids = {m: cat(ids[m], 4) for m in modes}
    timesteps = {m: torch.zeros(data[m].shape[0]) for m in modes}
    return upstream_module.Packed(data=data, ids=ids, timesteps=timesteps, mode_indices=mode_indices)


def _tokens(latent):
    return latent[0].flatten(1).transpose(0, 1)


def _reference_cond(refs):
    if not refs:
        return None
    return (
        torch.cat([_tokens(r) for r in refs]),
        torch.cat([_grid_ids(r.shape[-2], r.shape[-1], -(i + 1.0)) for i, r in enumerate(refs)]),
    )


def _reference_forward(ref, upstream_module, x, context, t, refs=(), target=None):
    h, w = x.shape[-2:]
    text = (context[0], _text_ids(context.shape[1]))
    image = (_tokens(x), _grid_ids(h, w))
    packed = _pack(upstream_module, [[text, _reference_cond(list(refs)), image]])
    packed.timesteps["image"].fill_(t)
    with torch.no_grad():
        out = ref(packed, timesteps_ref=target)
    return out.transpose(0, 1).reshape(1, IN_CH, h, w)


def _inputs(seed, h, w, length):
    generator = torch.Generator().manual_seed(seed)
    x = torch.randn(1, IN_CH, h, w, generator=generator)
    context = torch.randn(1, length, CTX, generator=generator)
    return x, context


def _refs(seed, shapes):
    generator = torch.Generator().manual_seed(seed)
    return [torch.randn(1, IN_CH, h, w, generator=generator) for h, w in shapes]


def test_state_dict_keys_match_reference_one_to_one(upstream_module):
    for flow_map in (False, True):
        ref = _build_reference(upstream_module, flow_map)
        with torch.device("meta"):
            ours = Flux3.from_config({**CONFIG, "flow_map": flow_map}, pick_operations(torch.float32, torch.float32))
        expected = {k: tuple(v.shape) for k, v in ref.state_dict().items()}
        assert {k: tuple(v.shape) for k, v in ours.state_dict().items()} == expected


def test_txt2img_matches_reference_on_a_non_square_grid(upstream_module):
    ref = _build_reference(upstream_module, False)
    ours = _build_ours(ref.state_dict(), False)
    x, context = _inputs(1, 3, 5, 16)
    expected = _reference_forward(ref, upstream_module, x, context, 0.37)
    with torch.no_grad():
        actual = ours(x, torch.tensor([0.37]), context)
    assert expected.abs().mean() > 1e-3
    torch.testing.assert_close(actual, expected, atol=1e-5, rtol=1e-5)


def test_edit_with_two_references_of_different_sizes_matches_reference(upstream_module):
    ref = _build_reference(upstream_module, False)
    ours = _build_ours(ref.state_dict(), False)
    x, context = _inputs(2, 4, 3, 11)
    refs = _refs(3, [(2, 3), (4, 2)])
    expected = _reference_forward(ref, upstream_module, x, context, 0.62, refs)
    with torch.no_grad():
        actual = ours(x, torch.tensor([0.62]), context, ref_latents=refs)
        without = ours(x, torch.tensor([0.62]), context)
    torch.testing.assert_close(actual, expected, atol=1e-5, rtol=1e-5)
    assert (actual - without).abs().max() > 1e-4


def test_reference_order_changes_the_output_like_the_reference(upstream_module):
    ref = _build_reference(upstream_module, False)
    ours = _build_ours(ref.state_dict(), False)
    x, context = _inputs(4, 3, 3, 7)
    refs = _refs(5, [(2, 2), (3, 2)])
    with torch.no_grad():
        forward = ours(x, torch.tensor([0.5]), context, ref_latents=refs)
        swapped = ours(x, torch.tensor([0.5]), context, ref_latents=refs[::-1])
    expected = _reference_forward(ref, upstream_module, x, context, 0.5, refs[::-1])
    assert (forward - swapped).abs().max() > 1e-4
    torch.testing.assert_close(swapped, expected, atol=1e-5, rtol=1e-5)


def test_flow_map_matches_reference(upstream_module):
    ref = _build_reference(upstream_module, True)
    ours = _build_ours(ref.state_dict(), True)
    x, context = _inputs(6, 3, 4, 9)
    refs = _refs(7, [(2, 2)])
    target = torch.tensor([0.21])
    expected = _reference_forward(ref, upstream_module, x, context, 0.55, refs, target=target)
    with torch.no_grad():
        actual = ours(x, torch.tensor([0.55]), context, ref_latents=refs, flow_map_target=target)
        other = ours(x, torch.tensor([0.55]), context, ref_latents=refs, flow_map_target=torch.tensor([0.4]))
    torch.testing.assert_close(actual, expected, atol=1e-5, rtol=1e-5)
    assert (actual - other).abs().max() > 1e-4


@pytest.mark.parametrize("flow_map", [False, True])
def test_flow_map_target_is_required_by_and_only_by_a_flow_map_model(upstream_module, flow_map):
    ref = _build_reference(upstream_module, flow_map)
    ours = _build_ours(ref.state_dict(), flow_map)
    x, context = _inputs(8, 2, 2, 4)
    kwargs = {} if flow_map else {"flow_map_target": torch.tensor([0.1])}
    with pytest.raises(ValueError, match="flow_map_target"):
        ours(x, torch.tensor([0.5]), context, **kwargs)


def test_two_sample_cfg_pack_equals_two_single_forwards(upstream_module):
    ref = _build_reference(upstream_module, False)
    ours = _build_ours(ref.state_dict(), False)
    x, cond_ctx = _inputs(9, 3, 3, 10)
    uncond_ctx = torch.randn(1, 10, CTX, generator=torch.Generator().manual_seed(10))
    refs = _refs(11, [(2, 3)])
    image = (_tokens(x), _grid_ids(3, 3))
    cond = _reference_cond(refs)
    packed = _pack(upstream_module, [
        [(uncond_ctx[0], _text_ids(10)), cond, image],
        [(cond_ctx[0], _text_ids(10)), cond, image],
    ])
    packed.timesteps["image"].fill_(0.44)
    with torch.no_grad():
        unconditional, conditional = ref(packed).chunk(2)
        ours_u = ours(x, torch.tensor([0.44]), uncond_ctx, ref_latents=refs)
        ours_c = ours(x, torch.tensor([0.44]), cond_ctx, ref_latents=refs)
    as_tokens = lambda t: t[0].flatten(1).transpose(0, 1)
    torch.testing.assert_close(as_tokens(ours_u), unconditional, atol=1e-5, rtol=1e-5)
    torch.testing.assert_close(as_tokens(ours_c), conditional, atol=1e-5, rtol=1e-5)


def test_batched_forward_equals_per_sample_forwards(upstream_module):
    ref = _build_reference(upstream_module, False)
    ours = _build_ours(ref.state_dict(), False)
    generator = torch.Generator().manual_seed(12)
    x = torch.randn(3, IN_CH, 3, 2, generator=generator)
    context = torch.randn(3, 6, CTX, generator=generator)
    t = torch.tensor([0.1, 0.5, 0.9])
    refs = [torch.randn(1, IN_CH, 2, 2, generator=generator)]
    with torch.no_grad():
        batched = ours(x, t, context, ref_latents=refs)
        singles = torch.cat([ours(x[i : i + 1], t[i : i + 1], context[i : i + 1], ref_latents=refs) for i in range(3)])
    torch.testing.assert_close(batched, singles, atol=1e-5, rtol=1e-5)


def test_text_attention_mask_is_ignored_like_the_reference(upstream_module):
    ref = _build_reference(upstream_module, False)
    ours = _build_ours(ref.state_dict(), False)
    x, context = _inputs(13, 2, 3, 8)
    mask = torch.zeros(1, 8)
    mask[:, :3] = 1
    with torch.no_grad():
        masked = ours(x, torch.tensor([0.3]), context, attention_mask=mask)
        plain = ours(x, torch.tensor([0.3]), context)
    torch.testing.assert_close(masked, plain, atol=0, rtol=0)
