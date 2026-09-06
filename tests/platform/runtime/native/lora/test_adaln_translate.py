"""Tests for translating a dense-base MiniMax-H3 AdaLN LoRA onto a pruned twin.

A dense H3 LoRA's ``adaln_proj.linear`` targets are ``[r, 2688]`` against the
post-activation timestep embedding; a pruned repack's projection takes the
``adaln_t_table`` row (width 8) with no activation at all, so those keys map to
nothing today (``test_dense_adaln_keys_are_unmatched_without_translation``
pins that). ``lora/adaln_translate.py`` fits the dense curve as an affine
function of the table and rewrites the pair into a weight delta plus a bias
delta on the pruned Linear.
"""

from __future__ import annotations

import re

import pytest
import torch
import torch.nn.functional as F

from src.platform.runtime.native.arch.minimax_h3.config import MiniMaxH3Config
from src.platform.runtime.native.arch.minimax_h3.model import (
    MiniMaxH3AdalnProj,
    MiniMaxH3Model,
)
from src.platform.runtime.native.lora.adaln_translate import (
    AffineFit,
    adaln_curve_timesteps,
    apply_bias_delta,
    apply_bias_deltas,
    dense_silu_grid,
    fit_affine_grid,
    translate_adaln_lora,
    translate_adaln_lora_state_dict,
)
from src.platform.runtime.native.lora.apply import apply_loras_with_report
from src.platform.runtime.native.lora.key_mapping import map_lora_keys
from vendor.gpl.comfyui.ops import pick_operations

from tests.platform.runtime.native.arch.test_minimax_h3_model import TINY_COMMON

DENSE_DIM = 12          # stands in for the real checkpoint's time_embed_dim 2688
PRUNED_DIM = 6          # stands in for the released repack's table width 8
GRID = 17
HIDDEN = TINY_COMMON["hidden_size"]
EXPAND = 6
MODALITIES = 3
OUT_FEATURES = EXPAND * HIDDEN * MODALITIES
RANK = 2


def _fp32_ops():
    return pick_operations(torch.float32, torch.float32)


def _adaln_proj(t_dim: int) -> MiniMaxH3AdalnProj:
    """A block's AdaLN projection with ``apply_silu=False``, i.e. fed the
    post-activation curve directly — which is what both sides of the
    translation are defined on (the dense side's SiLU is the thing the fit
    samples; the pruned side never has one)."""
    proj = MiniMaxH3AdalnProj(t_dim, HIDDEN, EXPAND, MODALITIES, False, _fp32_ops(), dtype=torch.float32)
    with torch.no_grad():
        proj.linear.weight.copy_(torch.randn_like(proj.linear.weight) * 0.05)
        proj.linear.bias.copy_(torch.randn_like(proj.linear.bias) * 0.05)
    return proj


def _dense_lora(rank: int = RANK, dim: int = DENSE_DIM) -> tuple[torch.Tensor, torch.Tensor]:
    """(down [r, dense_dim], up [out, r]) — a dense-base AdaLN LoRA pair."""
    return torch.randn(rank, dim) * 0.3, torch.randn(OUT_FEATURES, rank) * 0.3


def _stack(chunks: tuple[torch.Tensor, ...]) -> torch.Tensor:
    return torch.cat(chunks, dim=-1)


def _pruned_twin_of(dense: MiniMaxH3AdalnProj, fit: AffineFit) -> MiniMaxH3AdalnProj:
    """The pruned repack of ``dense`` implied by ``fit``: substituting
    ``S = 1 c^T + T V^T`` into ``W S^T + b`` gives weight ``W V`` and bias
    ``b + W c``. Exact when the fit is exact — which is what makes the EXACT
    test below a real equality rather than an approximation."""
    pruned = MiniMaxH3AdalnProj(fit.V.shape[1], HIDDEN, EXPAND, MODALITIES, False,
                                _fp32_ops(), dtype=torch.float32)
    with torch.no_grad():
        pruned.linear.weight.copy_(dense.linear.weight @ fit.V)
        pruned.linear.bias.copy_(dense.linear.bias + dense.linear.weight @ fit.c)
    return pruned


def _apply_dense_lora(proj: MiniMaxH3AdalnProj, down: torch.Tensor, up: torch.Tensor,
                      alpha: float, scale: float) -> None:
    with torch.no_grad():
        proj.linear.weight.add_((scale * alpha / down.shape[0]) * (up @ down))


def _apply_translated(proj: MiniMaxH3AdalnProj, down: torch.Tensor, up: torch.Tensor,
                      alpha: float, scale: float, fit: AffineFit, *, with_bias: bool = True) -> None:
    translated = translate_adaln_lora(down, up, alpha, scale, fit)
    delta = translated.delta
    with torch.no_grad():
        proj.linear.weight.add_(
            (delta.scale * delta.alpha / delta.down.shape[0]) * (delta.up @ delta.down)
        )
    if with_bias:
        apply_bias_delta(proj.linear, translated.bias_delta)


# --- the grid mapping -------------------------------------------------------

def test_grid_timesteps_are_the_t_values_the_lookup_returns_rows_verbatim_at():
    """``adaln_curve_timesteps(G)[i]`` must be the ``t`` at which
    ``_lookup_adaln_curve`` returns table row ``i`` unblended."""
    config = MiniMaxH3Config.from_detect_config(
        dict(TINY_COMMON, pruned=True, time_embed_dim=PRUNED_DIM, adaln_curve_grid=GRID)
    )
    model = MiniMaxH3Model(config, _fp32_ops(), dtype=torch.float32)
    with torch.no_grad():
        model.adaln_t_table.copy_(torch.randn(GRID, PRUNED_DIM))

    grid_points = adaln_curve_timesteps(GRID)
    looked_up = model._lookup_adaln_curve(grid_points)

    torch.testing.assert_close(looked_up, model.adaln_t_table, atol=1e-6, rtol=1e-6)


# --- (a) EXACT: a dense grid that really is affine in the table -------------

def test_translated_lora_reproduces_dense_modulation_when_the_fit_is_exact():
    torch.manual_seed(11)
    table = torch.randn(GRID, PRUNED_DIM)
    true_v = torch.randn(DENSE_DIM, PRUNED_DIM) * 0.5
    true_c = torch.randn(DENSE_DIM) * 0.5
    dense_grid = true_c.unsqueeze(0) + table @ true_v.T

    fit = fit_affine_grid(dense_grid, table)
    assert fit.residual < 1e-6          # exact up to the grid's own float32 rounding

    dense = _adaln_proj(DENSE_DIM)
    pruned = _pruned_twin_of(dense, fit)
    torch.testing.assert_close(_stack(dense(dense_grid)), _stack(pruned(table)), atol=1e-5, rtol=1e-4)

    down, up = _dense_lora()
    _apply_dense_lora(dense, down, up, alpha=float(RANK), scale=0.75)
    _apply_translated(pruned, down, up, alpha=float(RANK), scale=0.75, fit=fit)

    torch.testing.assert_close(_stack(dense(dense_grid)), _stack(pruned(table)), atol=1e-5, rtol=1e-4)


# --- (c) bite-checks for the test above -------------------------------------

def test_exact_translation_needs_the_bias_term():
    """Bite-check: the constant half of the affine fit carries a real part of
    the dense delta. Drop it and the EXACT test above stops holding."""
    torch.manual_seed(11)
    table = torch.randn(GRID, PRUNED_DIM)
    true_v = torch.randn(DENSE_DIM, PRUNED_DIM) * 0.5
    true_c = torch.randn(DENSE_DIM) * 0.5
    dense_grid = true_c.unsqueeze(0) + table @ true_v.T
    fit = fit_affine_grid(dense_grid, table)

    dense = _adaln_proj(DENSE_DIM)
    pruned = _pruned_twin_of(dense, fit)
    down, up = _dense_lora()
    _apply_dense_lora(dense, down, up, alpha=float(RANK), scale=0.75)
    _apply_translated(pruned, down, up, alpha=float(RANK), scale=0.75, fit=fit, with_bias=False)

    assert not torch.allclose(_stack(dense(dense_grid)), _stack(pruned(table)), atol=1e-5, rtol=1e-4)


def test_exact_translation_needs_the_right_t_mapping():
    """Bite-check: the fit is only meaningful if the dense curve is sampled at
    exactly the ``t`` values the table's rows stand for. Shift the grid by one
    row and the translation no longer reproduces the dense modulation."""
    torch.manual_seed(11)
    config = MiniMaxH3Config.from_detect_config(
        dict(TINY_COMMON, pruned=False, time_embed_dim=DENSE_DIM, freq_dim=8, time_embed_hidden_dim=16)
    )
    full = MiniMaxH3Model(config, _fp32_ops(), dtype=torch.float32)
    with torch.no_grad():
        for p in full.parameters():
            p.copy_(torch.randn_like(p) * 0.2)

    correct_grid = dense_silu_grid(full.time_embedder, adaln_curve_timesteps(GRID))
    shifted_grid = dense_silu_grid(full.time_embedder, adaln_curve_timesteps(GRID + 1)[1:])
    table = _pca_table(correct_grid, PRUNED_DIM)

    correct_fit = fit_affine_grid(correct_grid, table)
    shifted_fit = fit_affine_grid(shifted_grid, table)

    assert shifted_fit.residual > correct_fit.residual

    dense = _adaln_proj(DENSE_DIM)
    down, up = _dense_lora()
    reference = _adaln_proj(DENSE_DIM)
    with torch.no_grad():
        reference.linear.weight.copy_(dense.linear.weight)
        reference.linear.bias.copy_(dense.linear.bias)
    _apply_dense_lora(reference, down, up, alpha=float(RANK), scale=0.75)
    expected = _stack(reference(correct_grid))

    good = _pruned_twin_of(dense, correct_fit)
    bad = _pruned_twin_of(dense, correct_fit)
    _apply_translated(good, down, up, alpha=float(RANK), scale=0.75, fit=correct_fit)
    _apply_translated(bad, down, up, alpha=float(RANK), scale=0.75, fit=shifted_fit)

    good_error = (_stack(good(table)) - expected).norm()
    bad_error = (_stack(bad(table)) - expected).norm()
    assert good_error < bad_error


# --- (b) REAL tiny time embedder: the fit is approximate --------------------

def _pca_table(dense_grid: torch.Tensor, width: int) -> torch.Tensor:
    """A stand-in for a trained ``adaln_t_table``: the dense curve's top-``width``
    principal scores. Not an exact affine basis for the curve (the discarded
    components are the fit's residual), which is the point."""
    centered = dense_grid - dense_grid.mean(dim=0, keepdim=True)
    _u, _s, vh = torch.linalg.svd(centered.to(torch.float64), full_matrices=False)
    return (centered.to(torch.float64) @ vh[:width].T).to(torch.float32)


def test_real_time_embedder_fit_is_approximate_and_translation_reduces_error():
    torch.manual_seed(23)
    config = MiniMaxH3Config.from_detect_config(
        dict(TINY_COMMON, pruned=False, time_embed_dim=DENSE_DIM, freq_dim=8, time_embed_hidden_dim=16)
    )
    full = MiniMaxH3Model(config, _fp32_ops(), dtype=torch.float32)
    with torch.no_grad():
        for p in full.parameters():
            p.copy_(torch.randn_like(p) * 0.3)

    grid_points = adaln_curve_timesteps(GRID)
    dense_grid = dense_silu_grid(full.time_embedder, grid_points)
    table = _pca_table(dense_grid, 2)          # deliberately under-ranked, so the fit cannot be exact

    fit = fit_affine_grid(dense_grid, table)
    assert fit.residual > 0.0

    reconstructed = fit.c.unsqueeze(0) + table @ fit.V.T
    independent_residual = float((dense_grid - reconstructed).norm() / dense_grid.norm())
    assert fit.residual == pytest.approx(independent_residual, rel=1e-4)

    dense = _adaln_proj(DENSE_DIM)
    pruned = _pruned_twin_of(dense, fit)
    untouched = _pruned_twin_of(dense, fit)

    down, up = _dense_lora()
    _apply_dense_lora(dense, down, up, alpha=float(RANK), scale=1.0)
    _apply_translated(pruned, down, up, alpha=float(RANK), scale=1.0, fit=fit)

    target = _stack(dense(dense_grid))
    before = float((_stack(untouched(table)) - target).norm() / target.norm())
    after = float((_stack(pruned(table)) - target).norm() / target.norm())
    assert after < before


# --- (d) end to end through the real apply machinery ------------------------

def _pruned_model(blocks: int = 2) -> MiniMaxH3Model:
    config = MiniMaxH3Config.from_detect_config(
        dict(TINY_COMMON, num_layers=blocks, pruned=True, time_embed_dim=PRUNED_DIM, adaln_curve_grid=GRID)
    )
    model = MiniMaxH3Model(config, _fp32_ops(), dtype=torch.float32)
    with torch.no_grad():
        for p in model.parameters():
            p.copy_(torch.randn_like(p) * 0.05)
        model.adaln_t_table.copy_(torch.randn(GRID, PRUNED_DIM))
    return model


def _dense_dialect_sd() -> dict[str, torch.Tensor]:
    down0, up0 = _dense_lora()
    down1, up1 = _dense_lora()
    return {
        "transformer.transformer_blocks.0.adaln_proj.linear.lora_A.weight": down0,
        "transformer.transformer_blocks.0.adaln_proj.linear.lora_B.weight": up0,
        "transformer_blocks.1.adaln_proj.linear.lora_A.weight": down1,
        "transformer_blocks.1.adaln_proj.linear.lora_B.weight": up1,
    }


def test_dense_adaln_keys_are_unmatched_without_translation():
    """The defect this module exists for: both dialect spellings resolve to no
    native target, so the adapter's AdaLN half is dropped."""
    model = _pruned_model()
    _mapped, unmatched = map_lora_keys(_dense_dialect_sd(), model)

    assert sorted(unmatched) == [
        "transformer.transformer_blocks.0.adaln_proj.linear",
        "transformer_blocks.1.adaln_proj.linear",
    ]


def test_translated_state_dict_applies_through_apply_loras_with_report():
    torch.manual_seed(31)
    model = _pruned_model()
    dense_config = MiniMaxH3Config.from_detect_config(
        dict(TINY_COMMON, pruned=False, time_embed_dim=DENSE_DIM, freq_dim=8, time_embed_hidden_dim=16)
    )
    dense_model = MiniMaxH3Model(dense_config, _fp32_ops(), dtype=torch.float32)
    with torch.no_grad():
        for p in dense_model.parameters():
            p.copy_(torch.randn_like(p) * 0.2)

    dense_grid = dense_silu_grid(dense_model.time_embedder, adaln_curve_timesteps(GRID))
    fit = fit_affine_grid(dense_grid, model.adaln_t_table)

    lora_sd = _dense_dialect_sd()
    strength = 0.125
    translated = translate_adaln_lora_state_dict(lora_sd, model, fit)

    assert set(translated.bias_deltas) == {"blocks.0.adaln_proj.linear", "blocks.1.adaln_proj.linear"}
    assert set(translated.consumed) == set(lora_sd)

    before_weight = model.blocks[0].adaln_proj.linear.weight.clone()
    before_bias = model.blocks[0].adaln_proj.linear.bias.clone()

    patched, unmatched, reports = apply_loras_with_report(model, [(translated.lora_sd, strength)])
    assert patched == 2
    assert unmatched == []
    assert reports[0].matched_params == 2
    assert apply_bias_deltas(model, translated.bias_deltas, strength) == 2

    expected = translate_adaln_lora(
        lora_sd["transformer.transformer_blocks.0.adaln_proj.linear.lora_A.weight"],
        lora_sd["transformer.transformer_blocks.0.adaln_proj.linear.lora_B.weight"],
        float(RANK), strength, fit,
    )
    expected_weight = before_weight + strength * (expected.delta.up @ expected.delta.down)
    torch.testing.assert_close(model.blocks[0].adaln_proj.linear.weight, expected_weight, atol=1e-5, rtol=1e-4)
    torch.testing.assert_close(model.blocks[0].adaln_proj.linear.bias, before_bias + expected.bias_delta,
                               atol=1e-5, rtol=1e-4)


def test_translation_honours_an_alpha_sidecar():
    model = _pruned_model()
    fit = AffineFit(V=torch.randn(DENSE_DIM, PRUNED_DIM), c=torch.randn(DENSE_DIM), residual=0.0)
    lora_sd = _dense_dialect_sd()
    lora_sd["transformer.transformer_blocks.0.adaln_proj.linear.alpha"] = torch.tensor(0.25)

    translated = translate_adaln_lora_state_dict(lora_sd, model, fit)

    assert translated.lora_sd["blocks.0.adaln_proj.linear.alpha"].item() == pytest.approx(0.25)
    assert translated.lora_sd["blocks.1.adaln_proj.linear.alpha"].item() == pytest.approx(float(RANK))
    assert "transformer.transformer_blocks.0.adaln_proj.linear.alpha" in translated.consumed


def test_apply_bias_delta_refuses_unpatchable_bias_storage():
    proj = _adaln_proj(PRUNED_DIM)
    proj.linear.bias.data = proj.linear.bias.data.to(torch.float8_e4m3fn)

    with pytest.raises(ValueError, match=re.escape("cannot be patched in place")):
        apply_bias_delta(proj.linear, torch.zeros(OUT_FEATURES))


def test_fit_affine_grid_rejects_a_grid_too_short_to_determine_the_fit():
    with pytest.raises(ValueError, match="grid rows"):
        fit_affine_grid(torch.randn(PRUNED_DIM, DENSE_DIM), torch.randn(PRUNED_DIM, PRUNED_DIM))
