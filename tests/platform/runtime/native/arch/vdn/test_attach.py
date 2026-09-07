"""Attaching the VDN branch: strict keys, no weight movement, no half-attached model."""

from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.arch.minimax_h3.model import MiniMaxH3Attention
from src.platform.runtime.native.arch.minimax_h3.vdn import (
    MiniMaxH3VdnAttention,
    attach_vdn_branch,
    vdn_attached,
)

from ..test_minimax_h3_model import TINY_FULL, _build_ready
from .test_hybrid_attention import _branch_state_dict

TENSORS_PER_BLOCK = 16


def _model():
    torch.manual_seed(41)
    return _build_ready(TINY_FULL)


def _state():
    torch.manual_seed(42)
    return _branch_state_dict(TINY_FULL["num_layers"])


def test_report_counts_every_block_and_tensor():
    model = _model()
    state = _state()
    assert len(state) == TENSORS_PER_BLOCK * TINY_FULL["num_layers"]

    report = attach_vdn_branch(model, state)

    assert report.blocks == TINY_FULL["num_layers"]
    assert report.tensors == len(state)
    assert report.bytes == sum(t.numel() * t.element_size() for t in state.values())


def test_vdn_attached_flips_and_the_refiner_is_left_alone():
    model = _model()
    assert vdn_attached(model) is False

    attach_vdn_branch(model, _state())

    assert vdn_attached(model) is True
    assert all(isinstance(b.attn, MiniMaxH3VdnAttention) for b in model.blocks)
    for block in model.token_refiner.blocks:
        assert type(block.attn) is MiniMaxH3Attention


def test_branch_tensors_are_installed_as_given_not_copied():
    model = _model()
    state = _state()

    attach_vdn_branch(model, state)

    loaded = dict(model.blocks[1].attn.linear.state_dict())
    for tail, value in loaded.items():
        # Assign-loaded, so the module shares the caller's storage rather than
        # copying it into a freshly allocated (here: meta) parameter.
        assert value.data_ptr() == state[f"transformer_blocks.1.attn.{tail}"].data_ptr(), tail


def test_a_missing_tensor_names_the_key():
    model = _model()
    state = _state()
    dropped = state.pop("transformer_blocks.1.attn.linear_attention.beta_proj.weight")
    assert dropped is not None

    with pytest.raises(ValueError, match=r"transformer_blocks\.1\.attn\.linear_attention\.beta_proj\.weight"):
        attach_vdn_branch(model, state)
    assert vdn_attached(model) is False


def test_an_unexpected_tensor_names_the_key():
    model = _model()
    state = _state()
    state["transformer_blocks.0.attn.linear_attention.gamma_proj.weight"] = torch.zeros(4)

    with pytest.raises(ValueError, match=r"transformer_blocks\.0\.attn\.linear_attention\.gamma_proj\.weight"):
        attach_vdn_branch(model, state)
    assert vdn_attached(model) is False


def test_a_key_for_a_block_the_model_does_not_have_is_refused():
    model = _model()
    state = _state()
    state["transformer_blocks.99.attn.to_out_linear.weight"] = torch.zeros(4)

    with pytest.raises(ValueError, match="names block 99"):
        attach_vdn_branch(model, state)


def test_a_key_that_is_not_a_branch_key_is_refused():
    model = _model()
    state = _state()
    state["blocks.0.attn.to_out_linear.weight"] = torch.zeros(4)

    with pytest.raises(ValueError, match="is not a"):
        attach_vdn_branch(model, state)


def test_a_second_attach_is_refused():
    model = _model()
    attach_vdn_branch(model, _state())

    with pytest.raises(ValueError, match="already carries a VDN branch"):
        attach_vdn_branch(model, _state())


def test_a_late_failure_leaves_no_block_attached():
    model = _model()
    state = _state()
    state.pop(f"transformer_blocks.{TINY_FULL['num_layers'] - 1}.attn.to_out_linear.weight")

    with pytest.raises(ValueError):
        attach_vdn_branch(model, state)

    assert all(type(b.attn) is MiniMaxH3Attention for b in model.blocks)


def test_a_device_move_after_attach_still_drops_the_rope_cache():
    model = _model()
    attach_vdn_branch(model, _state())
    model._pe_cache_key = ("stale",)
    model._pe_cache = (torch.zeros(4), torch.zeros(4))

    moved = model.to(torch.float32)

    assert moved is model
    assert model._pe_cache is None
    assert model._pe_cache_key is None
    assert model.release_derived_caches() == 0
    assert all(p.device.type == "cpu" for p in model.blocks[0].attn.linear.parameters())


def test_attached_branch_parameters_are_frozen():
    # ``load_state_dict(assign=True)`` wraps the raw checkpoint tensors in fresh
    # Parameters, which require grad by default; the scan writes its state banks
    # with ``out=`` and autograd refuses that (maintainer's first VDN run).
    model = _model()
    model.requires_grad_(False)

    attach_vdn_branch(model, _state())

    grads = [name for name, p in model.named_parameters() if p.requires_grad]
    assert grads == []
