"""Translate a DENSE-base MiniMax-H3 AdaLN LoRA onto a PRUNED checkpoint.

A dense (full) H3 checkpoint feeds every block's ``adaln_proj.linear`` the
post-activation timestep embedding ``S(t) = SiLU(time_embedder(t))`` at
``time_embed_dim = 2688``. A pruned repack has no ``time_embedder`` at all: it
interpolates a small ``adaln_t_table`` (``[G, k]``, ``k = 8`` in the released
checkpoint) and feeds THAT straight into an ``adaln_proj.linear`` of input
width ``k`` (see ``arch/minimax_h3/model.py``'s ``_lookup_adaln_curve`` and
``MiniMaxH3AdalnProj.apply_silu``). A LoRA trained on the dense base therefore
carries ``[r, 2688]`` down matrices that no pruned Linear can consume, and the
key map registers no AdaLN target for the dense dialect at all — such keys come
back as ``unmatched`` and the adapter's AdaLN half is dropped.

The bridge is that the dense post-activation curve is very nearly affine in the
pruned table's own rows. Sampling both at the table's grid points gives
``S [G, 2688]`` and ``T [G, k]``; a least-squares fit yields ``V [2688, k]``
and ``c [2688]`` with ``S ~= 1 c^T + T V^T``. Substituting that into the dense
delta ``dW_dense = coeff * up @ down`` (applied to ``S(t)``) gives, on the
pruned input ``T(t)``:

    weight delta  dW = coeff * up @ (down @ V)     [out, k]
    bias   delta  db = coeff * up @ (down @ c)     [out]

``down @ V`` is still ``[r, k]``, so the weight half rides the existing
:class:`LoraDelta` path unchanged (same ``up``, same ``alpha``, same rank, so
the same ``scale * alpha / rank`` coefficient ``apply.py`` already computes).
Only the bias half is new — a dense AdaLN LoRA moves the modulation's constant
offset, and there is nowhere on the pruned weight to put it.

The fit's relative residual is the quality number: it is exactly zero when the
dense curve really is affine in the table, and its size bounds how much of the
adapter's AdaLN effect survives translation.

Pure torch. Nothing here loads a checkpoint or touches the network — the caller
supplies the dense ``time_embedder`` module (see :func:`dense_silu_grid`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor

from .key_mapping import LoraDelta, _iter_stems

# Storage dtypes a bias delta can be added into losslessly enough to be worth
# doing. The released pruned repack's adaln_proj biases are F16
# (``ai/minimax_h3/pruned_fp8_header.json``); float8 storage is not patchable
# and there is no runtime-delta path for a bias, hence the hard error.
_PATCHABLE_BIAS_DTYPES = (torch.float32, torch.float16, torch.bfloat16)

# diffusers/PEFT dialect AdaLN stem, with or without the `transformer.` prefix.
_DENSE_ADALN_STEM = re.compile(r"^(?:transformer\.)?transformer_blocks\.(\d+)\.adaln_proj\.linear$")


@dataclass(frozen=True)
class AffineFit:
    """Least-squares reconstruction of a dense post-activation curve from a
    pruned table: ``dense_row_i ~= c + V @ table_row_i``.

    ``residual`` is the relative Frobenius error of that reconstruction over
    the whole grid; 0.0 means the dense curve lies exactly in the affine span
    of the table's columns.
    """

    V: Tensor           # (D, k)
    c: Tensor           # (D,)
    residual: float


@dataclass(frozen=True)
class AdalnTranslation:
    """One dense AdaLN LoRA target re-expressed against the pruned Linear."""

    delta: LoraDelta    # rides apply.py's existing weight path
    bias_delta: Tensor  # (out,), already carries scale * alpha / rank


@dataclass(frozen=True)
class AdalnTranslationSet:
    """Every AdaLN target translated out of one dense-dialect LoRA file.

    ``lora_sd`` is a native-dialect state dict (bare ``blocks.N.adaln_proj.
    linear`` stems, which ``build_minimax_h3_lora_key_map`` already resolves
    for every native Linear) carrying the rewritten ``down @ V`` and the
    original ``up``/``alpha`` — hand it to ``apply_loras_with_report`` like any
    other adapter. ``bias_deltas`` maps the same Linears' module paths to their
    bias deltas at strength 1; apply them with :func:`apply_bias_deltas` using
    the SAME strength passed alongside ``lora_sd``. ``consumed`` lists the
    source keys this claimed, so the caller can drop them from the state dict
    it hands to the normal path.
    """

    lora_sd: dict[str, Tensor]
    bias_deltas: dict[str, Tensor]
    consumed: tuple[str, ...]


def adaln_curve_timesteps(grid: int, *, dtype: torch.dtype = torch.float32) -> Tensor:
    """The ``t`` value each ``adaln_t_table`` row is the curve's value at.

    ``_lookup_adaln_curve`` maps ``t`` to ``pos = clamp(t, 0, 1) * (grid - 1)``
    and returns row ``pos`` verbatim at an integer ``pos``, so row ``i`` is the
    curve at ``t = i / (grid - 1)`` — a uniform grid over ``[0, 1]``.
    """
    if grid < 2:
        raise ValueError(f"adaln_curve_timesteps: grid must be >= 2, got {grid}")
    return torch.linspace(0.0, 1.0, grid, dtype=dtype)


def dense_silu_grid(time_embedder: nn.Module, grid_points: Tensor) -> Tensor:
    """``SiLU(time_embedder(t))`` at ``grid_points`` — the exact activation a
    dense block's ``adaln_proj`` consumes (``MiniMaxH3AdalnProj.forward``
    applies the SiLU itself when ``apply_silu``).

    The dense ``time_embedder`` is an fp32 module and the SiLU is taken at that
    precision, matching the full-mode forward.
    """
    with torch.no_grad():
        return F.silu(time_embedder(grid_points.to(torch.float32)).to(torch.float32))


def fit_affine_grid(dense_grid: Tensor, table: Tensor) -> AffineFit:
    """Fit ``dense_grid ~= 1 c^T + table V^T`` by least squares in float64.

    ``dense_grid`` is ``[G, D]`` (the dense post-activation curve) and ``table``
    is ``[G, k]`` (the pruned ``adaln_t_table``), both sampled at the same G
    grid points.
    """
    if dense_grid.ndim != 2 or table.ndim != 2:
        raise ValueError("fit_affine_grid: dense_grid and table must both be 2-D")
    if dense_grid.shape[0] != table.shape[0]:
        raise ValueError(
            f"fit_affine_grid: grid length mismatch — dense {dense_grid.shape[0]} vs table {table.shape[0]}"
        )
    rows, width = table.shape
    if rows < width + 1:
        raise ValueError(
            f"fit_affine_grid: need at least {width + 1} grid rows to fit {width} columns plus a bias, got {rows}"
        )

    target = dense_grid.detach().to(device="cpu", dtype=torch.float64)
    design = torch.cat(
        [table.detach().to(device="cpu", dtype=torch.float64), torch.ones(rows, 1, dtype=torch.float64)],
        dim=1,
    )
    # gelsd, not the default gels: a table column that is constant (or a
    # duplicate of another) makes the design rank-deficient, and gels answers
    # such a system with garbage instead of the minimum-norm solution.
    solution = torch.linalg.lstsq(design, target, driver="gelsd").solution   # (k + 1, D)

    reconstructed = design @ solution
    denominator = torch.linalg.norm(target)
    error = torch.linalg.norm(target - reconstructed)
    residual = float(error / denominator) if float(denominator) > 0.0 else float(error)

    return AffineFit(
        V=solution[:width].T.contiguous().to(torch.float32),
        c=solution[width].contiguous().to(torch.float32),
        residual=residual,
    )


def translate_adaln_lora(down: Tensor, up: Tensor, alpha: float, scale: float, fit: AffineFit) -> AdalnTranslation:
    """Re-express one dense AdaLN LoRA pair against the pruned projection.

    ``down`` is ``[r, D]`` (lora_A, dense input width) and ``up`` is
    ``[out, r]`` (lora_B). The returned delta keeps ``up``, ``alpha`` and the
    rank, so ``apply.py`` reproduces the same ``scale * alpha / rank``
    coefficient the dense adapter would have had; the bias delta carries that
    coefficient already applied.
    """
    if down.ndim != 2 or up.ndim != 2:
        raise ValueError("translate_adaln_lora: down and up must both be 2-D")
    if down.shape[0] != up.shape[1]:
        raise ValueError(
            f"translate_adaln_lora: rank mismatch — down {tuple(down.shape)} vs up {tuple(up.shape)}"
        )
    if down.shape[1] != fit.V.shape[0]:
        raise ValueError(
            f"translate_adaln_lora: down input width {down.shape[1]} does not match the fit's dense width {fit.V.shape[0]}"
        )

    rank = down.shape[0]
    down_f32 = down.detach().to(torch.float32)
    up_f32 = up.detach().to(torch.float32)
    down_pruned = down_f32 @ fit.V.to(down_f32.device)
    bias_delta = (up_f32 @ (down_f32 @ fit.c.to(down_f32.device))) * (scale * alpha / rank)

    return AdalnTranslation(
        delta=LoraDelta(down=down_pruned, up=up_f32, alpha=alpha, scale=scale),
        bias_delta=bias_delta,
    )


def translate_adaln_lora_state_dict(
    lora_sd: dict[str, Tensor], module: nn.Module, fit: AffineFit,
) -> AdalnTranslationSet:
    """Pull every dense-dialect block AdaLN target out of ``lora_sd`` and
    re-express it against ``module``'s pruned ``blocks.N.adaln_proj.linear``.

    Targets whose block index does not exist on ``module`` are left alone (the
    caller's normal path will report them unmatched as it does today).
    """
    native_params = set(dict(module.named_parameters()))
    lora_out: dict[str, Tensor] = {}
    bias_deltas: dict[str, Tensor] = {}
    consumed: list[str] = []

    for stem, up_key, down_key in _iter_stems(lora_sd):
        match = _DENSE_ADALN_STEM.match(stem)
        if match is None:
            continue
        native_stem = f"blocks.{match.group(1)}.adaln_proj.linear"
        if f"{native_stem}.weight" not in native_params:
            continue

        alpha_key = f"{stem}.alpha"
        rank = lora_sd[down_key].shape[0]
        alpha = float(lora_sd[alpha_key].item()) if alpha_key in lora_sd else float(rank)
        translated = translate_adaln_lora(lora_sd[down_key], lora_sd[up_key], alpha, 1.0, fit)

        lora_out[f"{native_stem}.lora_down.weight"] = translated.delta.down
        lora_out[f"{native_stem}.lora_up.weight"] = translated.delta.up
        lora_out[f"{native_stem}.alpha"] = torch.tensor(alpha)
        bias_deltas[native_stem] = translated.bias_delta
        consumed.append(up_key)
        consumed.append(down_key)
        if alpha_key in lora_sd:
            consumed.append(alpha_key)

    return AdalnTranslationSet(lora_sd=lora_out, bias_deltas=bias_deltas, consumed=tuple(consumed))


def apply_bias_delta(linear: nn.Module, bias_delta: Tensor) -> None:
    """Add ``bias_delta`` into ``linear.bias``'s existing storage, at the
    bias's own dtype.

    The check is on the BIAS tensor, not on ``apply.py``'s
    ``_needs_runtime_deltas`` (which reads the WEIGHT's storage): a bias is
    added after the matmul, in the layer's output basis, so an fp8 or ConvRot
    weight does not by itself make the bias unpatchable — and there is no
    runtime-delta path for a bias to fall back on, so anything genuinely
    unpatchable has to be an error rather than a silent skip.
    """
    bias = getattr(linear, "bias", None)
    if bias is None:
        raise ValueError(
            "apply_bias_delta: target Linear has no bias — a dense AdaLN LoRA's constant "
            "term has nowhere to go on the pruned projection"
        )
    if bias.shape != bias_delta.shape:
        raise ValueError(
            f"apply_bias_delta: shape mismatch — bias {tuple(bias.shape)} vs delta {tuple(bias_delta.shape)}"
        )
    if bias.dtype not in _PATCHABLE_BIAS_DTYPES:
        raise ValueError(
            f"apply_bias_delta: bias storage dtype {bias.dtype} cannot be patched in place and "
            "there is no runtime-delta path for a bias"
        )
    with torch.no_grad():
        bias.data.add_(bias_delta.to(device=bias.device, dtype=bias.dtype))


def apply_bias_deltas(module: nn.Module, bias_deltas: dict[str, Tensor], strength: float = 1.0) -> int:
    """Apply an :class:`AdalnTranslationSet`'s bias deltas to ``module`` at
    ``strength`` — the same strength the paired ``lora_sd`` is applied at.
    Returns the number of biases patched."""
    patched = 0
    for stem, bias_delta in bias_deltas.items():
        linear = module
        for part in stem.split("."):
            linear = getattr(linear, part)
        apply_bias_delta(linear, bias_delta * strength)
        patched += 1
    return patched
