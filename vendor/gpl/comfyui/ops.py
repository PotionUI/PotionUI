# Vendored from ComfyUI — https://github.com/comfyanonymous/ComfyUI
# Upstream path: comfy/ops.py @ unknown; vendored ~2025
# License: GPL-3.0 (see LICENSE in this directory). Copyright (c) comfyanonymous and contributors.
# Local modifications: trimmed to the layer namespaces PotionUI's native engine uses; added
# runtime LoRA deltas, partial-residency streaming flags, the torch._scaled_mm fp8 fast path,
# nvfp4 dequantisation (ported from comfy/float.py + comfy/quant_ops.py), comfy_quant
# descriptor parsing (unknown formats rejected at load; the descriptor also marks a
# checkpoint as quantised for detect_quant_format), int8_tensorwise dequantisation plain
# and ConvRot (written from the published on-disk format, NOT ported — see the ConvRot
# section's own provenance note for what was and was not taken from where), and
# a token-chunked, native-dtype rewrite of ``_lora_output_branch`` (originally
# ``_nvfp4_lora_output_branch``, shared with the fp8 ``_scaled_mm`` fast path once it grew
# the same LoRA output-branch seam; its original fp32-whole-activation form OOM'd on large
# token counts; see the function's docstring), and
# a one-shot, first-occurrence-per-reason log (``_nvfp4_fast_path_reject_reason`` /
# ``_log_nvfp4_fast_path_rejection``, mirrored for fp8 as
# ``_scaled_mm_fast_path_reject_reason`` / ``_log_scaled_mm_fast_path_rejection``)
# distinguishing "took the native GEMM" from "fell through to dequant" and why --
# upstream ComfyUI has no such observability, and its absence here previously let a
# real fast-path-eligibility bug hide silently.
#
# AWQ ``pre_quant_scale`` activation smoothing (Fp8ScaledLinear/Nvfp4Linear) ported from
# comfy/ops.py @ 7d11ec31cb700d881fdf2d73731ecde0093b9540 (comfy-org/ComfyUI, fetched
# 2026-08-10), ``MixedPrecisionOps.Linear.forward`` — the
# ``input = input * cast_to_device(pre_quant_scale, input.device, input.dtype)`` step —
# and comfy/quant_ops.py's ``QUANT_ALGOS["nvfp4"]["parameters"]`` at the same commit, which
# lists ``pre_quant_scale`` as a loadable nvfp4 sidecar alongside weight_scale/weight_scale_2/
# input_scale. Ported minimally into this file's simpler dequant-on-forward Linear instead of
# upstream's QuantizedTensor/comfy_kitchen machinery, applied at the same point in the forward
# (multiply the incoming activation before any quantised matmul/dequant path runs).
#
# int8_tensorwise Embedding dequantisation ported from the SAME commit's
# ``MixedPrecisionOps.Embedding`` (comfy/ops.py, ``_load_from_state_dict`` +
# ``forward_comfy_cast_weights``): gather the int8 rows for the requested indices, THEN
# dequantise only those rows. The scale formula itself is not new — it is this file's own
# already-vendored int8_tensorwise plain/ConvRot Linear formula (``weight.to(compute) *
# weight_scale``, un-rotate after scaling), which is mathematically identical whether applied
# before or after row selection since the per-row scale and the ConvRot rotation both act
# within a row only, never across rows. Upstream's actual fused per-row kernel
# (``TensorWiseINT8Layout.dequantize_embedding``) lives in the closed-source comfy_kitchen
# package (not importable here, not vendored) — the formula was cross-checked against comfy/
# ops.py's orchestration code (which IS available) and this file's own already-verified Linear
# int8_tensorwise math, not against the kernel's internals.

"""Layer operation namespaces with cast-on-forward and scaled-fp8 support.

Ported and trimmed from ComfyUI's ``comfy/ops.py``. The whole point is the
``operations=`` seam: an arch module is written against ``operations.Linear``,
``operations.Conv2d`` etc. instead of ``torch.nn.*``, and the loader picks a
namespace that matches the checkpoint's storage dtype / quantisation:

  * ``disable_weight_init`` — plain torch layers with init skipped (weights are
    always overwritten by ``load_state_dict``). Used when storage == compute.
  * ``manual_cast`` — casts weights to the activation dtype per forward. Used
    when storage dtype (e.g. fp8 or fp16) differs from compute dtype.
  * ``fp8_ops`` — ``manual_cast`` plus a ``Linear`` that understands the
    per-tensor ``weight_scale`` / ``input_scale`` format found in modern fp8
    checkpoints (e.g. flux-2-klein-9b-fp8). v1 dequantises on forward
    (``weight.to(compute) * weight_scale``); the native fp8 matmul is a later
    optimisation that slots into ``Fp8ScaledLinear.forward_comfy_cast_weights``.

**fp8 scale loading:** ``Fp8ScaledLinear`` registers ``weight_scale`` /
``input_scale`` as non-persistent buffers and pops the matching keys out of the
state dict inside ``_load_from_state_dict`` before the base loader runs. This
means (a) strict-ish loads never see the scale keys as "unexpected", and (b) a
mixed checkpoint where only *some* Linears are fp8 works transparently: layers
without a ``weight_scale`` key fall back to the plain manual-cast path.

The ops-namespace protocol (what ``operations`` exposes) is:
    Linear, Conv2d, Conv3d, GroupNorm, LayerNorm, RMSNorm, Embedding
each constructible with the same signature as its ``torch.nn`` counterpart.
"""

from __future__ import annotations

import json
import logging
import os

import torch
import torch.nn.functional as F

logger = logging.getLogger(__name__)

_FP8_DTYPES = {torch.float8_e4m3fn, torch.float8_e5m2}

# quant_format identifiers understood by pick_operations.
QUANT_FP8_SCALED = "fp8_scaled"

# Largest finite magnitude representable in e4m3 (torch.finfo(float8_e4m3fn).max).
_E4M3_MAX = 448.0

# Native fp8 GEMM fast path (torch._scaled_mm) — see Fp8ScaledLinear.forward_comfy_cast_weights.
NATIVE_FP8_MATMUL_ENV = "NATIVE_FP8_MATMUL"
# torch._scaled_mm needs real fp8 tensor cores: Ada/Hopper/Blackwell (sm89+).
_SCALED_MM_MIN_CAP = (8, 9)

_scaled_mm_supported_cache: bool | None = None


def _scaled_mm_supported() -> bool:
    """Probe once: can this process run ``torch._scaled_mm`` (torch build exposes
    it, CUDA available, device capability >= sm89)? Cached; see
    :func:`reset_scaled_mm_probe` to re-probe (tests)."""
    global _scaled_mm_supported_cache
    if _scaled_mm_supported_cache is None:
        ok = False
        if hasattr(torch, "_scaled_mm") and torch.cuda.is_available():
            try:
                ok = torch.cuda.get_device_capability() >= _SCALED_MM_MIN_CAP
            except Exception:  # noqa: BLE001 — a driver hiccup must not break probing
                ok = False
        _scaled_mm_supported_cache = ok
        logger.debug("fp8 matmul: _scaled_mm_supported=%s", ok)
    return _scaled_mm_supported_cache


def reset_scaled_mm_probe() -> None:
    """Drop the cached ``torch._scaled_mm`` support probe (tests only)."""
    global _scaled_mm_supported_cache
    _scaled_mm_supported_cache = None


def _fp8_matmul_enabled() -> bool:
    """Native fp8 GEMM fast-path policy, read from ``$NATIVE_FP8_MATMUL``.

    ``off`` (default) never uses the fast path — GPU-unvalidated as of this
    writing; it needs an A/B image-diff benchmark against the existing dequant
    path on a real fp8 checkpoint (e.g. Klein fp8) before the default flips.
    ``on``/``auto`` both require :func:`_scaled_mm_supported`; ``auto`` has no
    extra heuristic yet (unlike ``NATIVE_FP8_QUANTIZE``'s VRAM-budget auto) so it
    currently behaves like ``on``. An unknown value is treated as ``off`` (the
    safe default), mirroring the unknown-policy handling in ``fp8_quant.py``.
    """
    policy = os.environ.get(NATIVE_FP8_MATMUL_ENV, "off").strip().lower()
    if policy == "off":
        return False
    if policy not in ("on", "auto"):
        logger.warning("fp8 matmul: unknown %s=%r; treating as 'off'", NATIVE_FP8_MATMUL_ENV, policy)
        return False
    return _scaled_mm_supported()


def _scaled_mm_fast_path_reject_reason(
    *,
    weight_dtype: torch.dtype,
    has_weight_scale: bool,
    lora_deltas: "list | None",
    input_dtype: torch.dtype,
    input_is_cuda: bool,
    weight_is_cuda: bool,
    in_features: int,
    out_features: int,
) -> str | None:
    """Which ``_scaled_mm`` fp8 fast-path precondition failed, or ``None`` if
    they all hold. Single source of truth for :func:`_scaled_mm_fast_path_ok`
    (a plain ``is None`` over this) and for the one-shot observability log at
    the ``Fp8ScaledLinear.forward_comfy_cast_weights`` call site — mirrors
    :func:`_nvfp4_fast_path_reject_reason`, which exists for the same reason:
    without a name for WHY a layer fell through to dequant, "every streamed
    leaf is silently skipping the fast path" has no signal to find it by.

    ``weight_dtype`` and ``has_weight_scale`` are fp8-only preconditions with
    no nvfp4 equivalent (an nvfp4 layer is identified by ``weight_scale_2``,
    checked before this predicate ever runs) — keep their reasons distinct
    from the device/dtype ones nvfp4 also has, never fold them together.

    LoRA: deltas no longer force the dequant fallback outright — only deltas
    :func:`_deltas_output_branch_ok` can't prove expressible as an output-side
    low-rank branch do (LoKr, anything unrecognised, or an out-of-bounds
    ``target_slice``). Mirrors :func:`_nvfp4_fast_path_reject_reason`.
    """
    if lora_deltas and not _deltas_output_branch_ok(lora_deltas, out_features):
        return "lora_deltas"
    if not has_weight_scale:
        return "no_weight_scale"
    if weight_dtype != torch.float8_e4m3fn:
        return f"weight_dtype={weight_dtype}"
    if input_dtype not in (torch.float16, torch.bfloat16):
        return f"input_dtype={input_dtype}"
    if not input_is_cuda:
        return "input_not_cuda"
    if not weight_is_cuda:
        return "weight_not_cuda"
    if in_features % 16:
        return "in_features_not_multiple_of_16"
    if out_features % 16:
        return "out_features_not_multiple_of_16"
    return None


def _scaled_mm_fast_path_ok(
    *,
    weight_dtype: torch.dtype,
    has_weight_scale: bool,
    lora_deltas: "list | None",
    input_dtype: torch.dtype,
    input_is_cuda: bool,
    weight_is_cuda: bool,
    in_features: int,
    out_features: int,
) -> bool:
    """Pure predicate for the ``_scaled_mm`` fast-path preconditions (dtype,
    device, ``_scaled_mm``'s 16-element alignment, no active LoRA delta).

    Kept separate from the ``$NATIVE_FP8_MATMUL`` gate + hardware probe
    (:func:`_fp8_matmul_enabled`) so it's testable with plain values instead of
    real CUDA tensors.
    """
    return _scaled_mm_fast_path_reject_reason(
        weight_dtype=weight_dtype,
        has_weight_scale=has_weight_scale,
        lora_deltas=lora_deltas,
        input_dtype=input_dtype,
        input_is_cuda=input_is_cuda,
        weight_is_cuda=weight_is_cuda,
        in_features=in_features,
        out_features=out_features,
    ) is None


# First-occurrence-per-reason log of why an fp8 Linear skipped the native
# ``_scaled_mm`` GEMM and fell through to dequant. Deliberately NOT a
# per-Linear per-forward log (forward_comfy_cast_weights runs ~350 times per
# step) -- one line per distinct reason for the life of the process is enough
# to tell an operator "every layer is dequanting because the weight is still
# on pinned CPU RAM" without flooding the log. Mirrors
# _nvfp4_fast_path_reject_reasons_logged.
_scaled_mm_fast_path_reject_reasons_logged: set[str] = set()


def _log_scaled_mm_fast_path_rejection(reason: str) -> None:
    if reason in _scaled_mm_fast_path_reject_reasons_logged:
        return
    _scaled_mm_fast_path_reject_reasons_logged.add(reason)
    logger.warning(
        "fp8 matmul: fast path unavailable (%s); using dequant instead "
        "(logged once per distinct reason)", reason,
    )


def reset_scaled_mm_fast_path_rejection_log() -> None:
    """Drop the one-shot rejection-reason log dedup set (tests only)."""
    _scaled_mm_fast_path_reject_reasons_logged.clear()


def _quantize_fp8_dynamic(x2d: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """Dynamic per-tensor fp8 activation quantisation for the ``_scaled_mm`` fast
    path. ``x2d`` is ``(M, K)``; returns ``(x_fp8, scale)`` with ``scale`` a 0-dim
    fp32 tensor such that ``x_fp8.to(fp32) * scale`` recovers ``x2d`` (approx)."""
    amax = x2d.detach().abs().amax().clamp(min=1e-8).to(torch.float32)
    scale = amax / _E4M3_MAX
    x_fp8 = (x2d.to(torch.float32) / scale).clamp(-_E4M3_MAX, _E4M3_MAX).to(torch.float8_e4m3fn)
    return x_fp8, scale


def cast_to(
    tensor: torch.Tensor | None,
    dtype: torch.dtype,
    device: torch.device,
    *,
    non_blocking: bool = False,
) -> torch.Tensor | None:
    if tensor is None:
        return None
    if tensor.device == device and tensor.dtype == dtype:
        return tensor
    # ``non_blocking`` is only actually async when the source is pinned CPU memory
    # (partial-residency streaming pins the streamed weights); it degrades to a
    # synchronous copy otherwise, so it is always safe to pass through.
    return tensor.to(device=device, dtype=dtype, non_blocking=non_blocking)


def cast_bias_weight(
    module: "CastWeightBiasOp",
    input: torch.Tensor | None = None,
    dtype: torch.dtype | None = None,
    device: torch.device | None = None,
) -> tuple[torch.Tensor, torch.Tensor | None]:
    """Cast a module's weight/bias to the activation dtype+device.

    A module streamed from pinned CPU RAM (partial residency) sets
    ``stream_non_blocking`` so the per-forward H2D weight copy overlaps compute on
    the current stream; resident modules leave it False (no copy happens anyway).
    """
    if input is not None:
        if dtype is None:
            dtype = input.dtype
        if device is None:
            device = input.device
    nb = getattr(module, "stream_non_blocking", False)
    weight = cast_to(module.weight, dtype, device, non_blocking=nb)
    bias = cast_to(module.bias, dtype, device, non_blocking=nb) if module.bias is not None else None
    return weight, bias


class CastWeightBiasOp:
    """Marker mixin carrying the cast-on-forward flags."""

    comfy_cast_weights = False
    weight_function: list = []
    bias_function: list = []
    # Set True on a module whose weights are STREAMED from pinned CPU RAM under
    # partial residency: the per-forward H2D copy is issued non_blocking so it
    # overlaps compute. Resident modules leave it False. See ``memory/partial.py``.
    stream_non_blocking = False
    # Optional runtime LoRA deltas applied to the (cast/dequant) weight per
    # forward. ``None`` = no LoRA (default). Populated by ``lora.apply`` on
    # cast-mode Linears; standard-mode Linears patch their weight in place
    # instead. See ``apply_lora_deltas``.
    lora_deltas: list | None = None


def apply_lora_deltas(weight: torch.Tensor, deltas: "list | None") -> torch.Tensor:
    """Return ``weight`` with low-rank LoRA deltas added (compute-side).

    Each delta is duck-typed (``lora.key_mapping.LoraDelta``): ``down`` ``(rank,
    in)``, ``up`` ``(out|slice, rank)``, ``alpha``, ``scale`` (strength), and an
    optional ``target_slice`` ``(dim, start, length)`` for partial patches of a
    fused weight (e.g. a diffusers ``to_q`` delta into the native fused ``qkv``).
    Composes with dequant/cast: the caller passes the already-materialised
    compute-dtype weight, so fp8 storage stays untouched. Math is done in fp32
    (``W += (scale * alpha / rank) * up @ down``) then cast back to ``weight``'s
    dtype, matching ComfyUI's intermediate-fp32 convention.
    """
    if not deltas:
        return weight
    out = weight.clone()
    for d in deltas:
        up = d.up.to(device=out.device, dtype=torch.float32)
        down = d.down.to(device=out.device, dtype=torch.float32)
        if getattr(d, "kron", False):
            # LoKr: up/down are the Kronecker factors w1/w2; d.alpha is the
            # pre-divided LyCORIS alpha/dim factor (see LoraDelta docstring).
            delta = torch.kron(up, down).reshape(out.shape) * (float(d.scale) * float(d.alpha))
        else:
            rank = d.down.shape[0]
            delta = (up @ down) * (float(d.scale) * float(d.alpha) / rank)
        delta = delta.to(dtype=out.dtype)
        if d.target_slice is not None:
            _dim, start, length = d.target_slice
            out[start : start + length].add_(delta)
        else:
            out.add_(delta)
    return out


class disable_weight_init:
    """torch layers with parameter init skipped (weights come from load)."""

    class Linear(torch.nn.Linear, CastWeightBiasOp):
        def reset_parameters(self) -> None:
            return None

        def forward_comfy_cast_weights(self, input: torch.Tensor) -> torch.Tensor:
            weight, bias = cast_bias_weight(self, input)
            weight = apply_lora_deltas(weight, self.lora_deltas)
            return F.linear(input, weight, bias)

        def forward(self, *args, **kwargs):
            if self.comfy_cast_weights:
                return self.forward_comfy_cast_weights(*args, **kwargs)
            return super().forward(*args, **kwargs)

    class Conv2d(torch.nn.Conv2d, CastWeightBiasOp):
        def reset_parameters(self) -> None:
            return None

        def forward_comfy_cast_weights(self, input: torch.Tensor) -> torch.Tensor:
            weight, bias = cast_bias_weight(self, input)
            return self._conv_forward(input, weight, bias)

        def forward(self, *args, **kwargs):
            if self.comfy_cast_weights:
                return self.forward_comfy_cast_weights(*args, **kwargs)
            return super().forward(*args, **kwargs)

    class Conv3d(torch.nn.Conv3d, CastWeightBiasOp):
        def reset_parameters(self) -> None:
            return None

        def forward_comfy_cast_weights(self, input: torch.Tensor) -> torch.Tensor:
            weight, bias = cast_bias_weight(self, input)
            return self._conv_forward(input, weight, bias)

        def forward(self, *args, **kwargs):
            if self.comfy_cast_weights:
                return self.forward_comfy_cast_weights(*args, **kwargs)
            return super().forward(*args, **kwargs)

    class Conv1d(torch.nn.Conv1d, CastWeightBiasOp):
        def reset_parameters(self) -> None:
            return None

        def forward_comfy_cast_weights(self, input: torch.Tensor) -> torch.Tensor:
            weight, bias = cast_bias_weight(self, input)
            return self._conv_forward(input, weight, bias)

        def forward(self, *args, **kwargs):
            if self.comfy_cast_weights:
                return self.forward_comfy_cast_weights(*args, **kwargs)
            return super().forward(*args, **kwargs)

    class ConvTranspose1d(torch.nn.ConvTranspose1d, CastWeightBiasOp):
        def reset_parameters(self) -> None:
            return None

        def forward_comfy_cast_weights(self, input: torch.Tensor, output_size=None) -> torch.Tensor:
            weight, bias = cast_bias_weight(self, input)
            output_padding = self._output_padding(
                input, output_size, self.stride, self.padding, self.kernel_size,
                self.dilation,
            )
            return F.conv_transpose1d(
                input, weight, bias, self.stride, self.padding,
                output_padding, self.groups, self.dilation,
            )

        def forward(self, *args, **kwargs):
            if self.comfy_cast_weights:
                return self.forward_comfy_cast_weights(*args, **kwargs)
            return super().forward(*args, **kwargs)

    class GroupNorm(torch.nn.GroupNorm, CastWeightBiasOp):
        def reset_parameters(self) -> None:
            return None

        def forward_comfy_cast_weights(self, input: torch.Tensor) -> torch.Tensor:
            weight, bias = cast_bias_weight(self, input)
            return F.group_norm(input, self.num_groups, weight, bias, self.eps)

        def forward(self, *args, **kwargs):
            if self.comfy_cast_weights:
                return self.forward_comfy_cast_weights(*args, **kwargs)
            return super().forward(*args, **kwargs)

    class LayerNorm(torch.nn.LayerNorm, CastWeightBiasOp):
        def reset_parameters(self) -> None:
            return None

        def forward_comfy_cast_weights(self, input: torch.Tensor) -> torch.Tensor:
            if self.weight is not None:
                weight, bias = cast_bias_weight(self, input)
            else:
                weight, bias = None, None
            return F.layer_norm(input, self.normalized_shape, weight, bias, self.eps)

        def forward(self, *args, **kwargs):
            if self.comfy_cast_weights:
                return self.forward_comfy_cast_weights(*args, **kwargs)
            return super().forward(*args, **kwargs)

    class RMSNorm(torch.nn.RMSNorm, CastWeightBiasOp):
        def reset_parameters(self) -> None:
            return None

        def forward_comfy_cast_weights(self, input: torch.Tensor) -> torch.Tensor:
            weight = cast_to(self.weight, input.dtype, input.device) if self.weight is not None else None
            return F.rms_norm(input, self.normalized_shape, weight, self.eps)

        def forward(self, *args, **kwargs):
            if self.comfy_cast_weights:
                return self.forward_comfy_cast_weights(*args, **kwargs)
            return super().forward(*args, **kwargs)

    class Embedding(torch.nn.Embedding, CastWeightBiasOp):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            # non-persistent: int8_tensorwise dequant state, absent for a plain embedding.
            self.register_buffer("_int8_weight", None, persistent=False)
            self.register_buffer("_int8_weight_scale", None, persistent=False)
            self.register_buffer("_int8_convrot_hadamard", None, persistent=False)
            self._int8_convrot_groupsize: int | None = None

        def reset_parameters(self) -> None:
            self.bias = None
            return None

        def _load_from_state_dict(
            self, state_dict, prefix, local_metadata, strict,
            missing_keys, unexpected_keys, error_msgs,
        ) -> None:
            quant_blob = state_dict.pop(prefix + "comfy_quant", None)
            layer_conf = _parse_comfy_quant(quant_blob)
            if layer_conf is not None:
                fmt = layer_conf.get("format")
                if fmt != _COMFY_QUANT_INT8_TENSORWISE:
                    raise ValueError(
                        f"embedding quant format {fmt!r} is not supported (layer '{prefix.rstrip('.')}')"
                    )
                weight = state_dict.pop(prefix + "weight", None)
                scale = state_dict.pop(prefix + "weight_scale", None)
                if weight is None or scale is None:
                    raise ValueError(
                        f"int8 embedding '{prefix.rstrip('.')}' missing weight/weight_scale"
                    )
                self._int8_weight = weight if weight.dtype == torch.int8 else weight.view(torch.int8)
                self._int8_weight_scale = scale.to(torch.float32)
                convrot, groupsize = _extract_convrot_config(layer_conf, prefix.rstrip("."))
                if convrot:
                    self._int8_convrot_groupsize = groupsize
                    self._int8_convrot_hadamard = _build_convrot_hadamard(groupsize, device="cpu", dtype=torch.float32)
                # Free the empty [num_embeddings, embedding_dim] float weight the
                # constructor allocated; the int8 table is stored on the side.
                self.weight = None
            super()._load_from_state_dict(
                state_dict, prefix, local_metadata, strict,
                missing_keys, unexpected_keys, error_msgs,
            )
            for name in ("weight", "weight_scale", "comfy_quant"):
                key = prefix + name
                if key in missing_keys:
                    missing_keys.remove(key)

        def forward_comfy_cast_weights(self, input: torch.Tensor, out_dtype: torch.dtype | None = None) -> torch.Tensor:
            if self._int8_weight is not None:
                # Optimized path: gather int8 rows first, dequantise only the
                # selected rows (matches the plain/ConvRot int8
                # Linear formula, applied post-gather since both the per-row
                # scale and the ConvRot rotation act within a row only).
                target = out_dtype if out_dtype is not None else torch.bfloat16
                rows = cast_to(self._int8_weight, torch.int8, input.device)[input]
                x = rows.to(torch.float32)
                scale = cast_to(self._int8_weight_scale, torch.float32, input.device)
                if scale.dim() > 0:
                    x = x * scale.reshape(-1)[input].unsqueeze(-1)
                else:
                    x = x * scale
                if self._int8_convrot_hadamard is not None:
                    orig_shape = x.shape
                    flat = x.reshape(-1, orig_shape[-1])
                    hadamard = cast_to(self._int8_convrot_hadamard, flat.dtype, flat.device)
                    flat = _convrot_unrotate_weight(flat, hadamard, self._int8_convrot_groupsize)
                    x = flat.reshape(orig_shape)
                return x.to(target)
            # Embeddings carry no activation to borrow a dtype from; fp8 weights
            # dequantise to out_dtype (bf16 by default).
            target = out_dtype
            if target is None:
                target = self.weight.dtype if self.weight.dtype not in _FP8_DTYPES else torch.bfloat16
            weight = cast_to(self.weight, target, self.weight.device)
            return F.embedding(
                input, weight, self.padding_idx, self.max_norm,
                self.norm_type, self.scale_grad_by_freq, self.sparse,
            )

        def forward(self, *args, **kwargs):
            if self.comfy_cast_weights or self._int8_weight is not None:
                return self.forward_comfy_cast_weights(*args, **kwargs)
            return super().forward(*args, **kwargs)


class manual_cast(disable_weight_init):
    """Same layers, but always cast weights to the activation dtype per forward."""

    class Linear(disable_weight_init.Linear):
        comfy_cast_weights = True

    class Conv2d(disable_weight_init.Conv2d):
        comfy_cast_weights = True

    class Conv3d(disable_weight_init.Conv3d):
        comfy_cast_weights = True

    class Conv1d(disable_weight_init.Conv1d):
        comfy_cast_weights = True

    class ConvTranspose1d(disable_weight_init.ConvTranspose1d):
        comfy_cast_weights = True

    class GroupNorm(disable_weight_init.GroupNorm):
        comfy_cast_weights = True

    class LayerNorm(disable_weight_init.LayerNorm):
        comfy_cast_weights = True

    class RMSNorm(disable_weight_init.RMSNorm):
        comfy_cast_weights = True

    class Embedding(disable_weight_init.Embedding):
        comfy_cast_weights = True


# ``comfy_quant`` per-layer descriptor formats this dequant path executes
# correctly: both fp8 spellings and int8_tensorwise (ComfyUI PR #14636 /
# comfy/quant_ops.py QUANT_ALGOS) with the plain ``weight.to(compute) *
# weight_scale`` formula, and int8_tensorwise ConvRot (see the dedicated
# section below ``Fp8ScaledLinear``) with the rotation-aware one. Any other
# format is unknown and must fail the load instead of silently dequantizing
# garbage.
_COMFY_QUANT_FP8_FORMATS = frozenset({"float8_e4m3fn", "float8_e5m2"})
_COMFY_QUANT_INT8_TENSORWISE = "int8_tensorwise"
_CONVROT_DEFAULT_GROUPSIZE = 256


def _parse_comfy_quant(blob: torch.Tensor | None) -> dict | None:
    """Decode a ``comfy_quant`` descriptor (UTF-8 JSON bytes stored as a uint8
    tensor) into its dict. A missing OR unparseable blob both return ``None`` --
    the caller then keeps the pre-existing bare-``weight_scale`` dequant
    behavior, matching checkpoints saved before ComfyUI added this descriptor.
    """
    if blob is None:
        return None
    try:
        parsed = json.loads(bytes(blob.tolist()).decode("utf-8"))
    except Exception:  # noqa: BLE001 — any decode/parse failure is "no descriptor"
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_convrot_config(layer_conf: dict, layer_name: str = "") -> tuple[bool, int]:
    """Read ``convrot`` + ``convrot_groupsize`` out of a comfy_quant descriptor.

    Both live at the descriptor's top level in every published checkpoint; the
    ``"params"`` nesting is also read because ComfyUI's own loader accepts it,
    though no released file uses it.

    Published ConvRot descriptors do NOT all state a group size — the compact
    43-byte variant in the wild is exactly ``{"format":...,"convrot":true}`` — so
    falling back to the format default is a real code path, not a guard. It logs,
    because un-rotating at the wrong group size is arithmetically valid and
    silently wrong output.
    """
    params_conf = layer_conf.get("params")
    if not isinstance(params_conf, dict):
        params_conf = {}
    convrot = bool(layer_conf.get("convrot", params_conf.get("convrot", False)))
    for conf in (layer_conf, params_conf):
        if "convrot_groupsize" in conf:
            return convrot, int(conf["convrot_groupsize"])
    if convrot:
        logger.info(
            "ConvRot layer '%s' states no group size (%s); using the format default %d",
            layer_name, sorted(layer_conf), _CONVROT_DEFAULT_GROUPSIZE,
        )
    return convrot, _CONVROT_DEFAULT_GROUPSIZE


def _reject_ambiguous_int8(
    weight: torch.Tensor | None, scale: torch.Tensor | None, layer_name: str,
) -> None:
    """Refuse an integer-coded weight whose rotation state cannot be determined.

    With no descriptor there is nothing to say whether int8 codes are plain or
    ConvRot-rotated. A 0-dim scale means plain tensorwise and dequantises
    correctly, but a per-output-channel scale fits both a plain per-channel
    quantiser and a rotated one — and picking wrong skips the un-rotation and
    produces confident noise. Two of four published int8 checkpoints carry no
    file-level quantisation header at all, so the per-layer descriptor is the only
    thing that ever answers this.
    """
    if weight is None or weight.is_floating_point():
        return
    if scale is None or scale.dim() == 0:
        return
    raise ValueError(
        f"int8 layer '{layer_name}' has a per-output-channel weight_scale but no comfy_quant "
        "descriptor: plain and ConvRot-rotated codes are indistinguishable here"
    )


def _check_quant_format_supported(layer_conf: dict, layer_name: str) -> None:
    """Raise for any ``comfy_quant`` format this module cannot dequantize
    correctly. See the module comment above for what's supported.
    """
    fmt = layer_conf.get("format")
    if fmt in _COMFY_QUANT_FP8_FORMATS or fmt == _COMFY_QUANT_INT8_TENSORWISE:
        return
    raise ValueError(f"quant format {fmt!r} is not supported yet (layer '{layer_name}')")


# ---------------------------------------------------------------------------
# int8_tensorwise ConvRot (QuaRot-style Hadamard rotation) dequantisation
# ---------------------------------------------------------------------------
#
# Written from the published on-disk FORMAT, not ported from an implementation.
# comfy-kitchen's `eager` backend (Apache-2.0, github.com/Comfy-Org/comfy-kitchen)
# is where the format is documented — comfy/quant_ops.py has no pure-torch path
# and dispatches this format to compiled kernels — but no code was taken from it,
# and in particular nothing from its `TensorWiseINT8Layout` (documented as coming
# from dxqb/OneTrainer, whose licence this project has NOT verified) or from the
# parts of its eager backend derived from PyTorch AO. The format facts below are
# all that is needed, and they are facts about the file, not expression:
#
#   * `<layer>.weight`       int8, [out_features, in_features]. One code per byte,
#                            so the stored shape IS the layer's true shape (unlike
#                            nvfp4, which packs two codes per byte).
#   * `<layer>.weight_scale` f32. [out_features, 1] for ConvRot (per output
#                            channel); a 0-dim scalar for plain tensorwise. The
#                            scale's rank is the on-the-wire signal for which.
#   * `<layer>.comfy_quant`  the per-layer descriptor.
#   * `<layer>.bias`         keeps the checkpoint's original float dtype.
#   There is no zero point (codes are symmetric), no `input_scale`, and no
#   `weight_scale_2`.
#
# The rotation: the weight is rotated OFFLINE, grouped along in_features in
# blocks of `convrot_groupsize`, by a normalized regular Hadamard matrix built by
# Kronecker-powering the 4x4 base below and dividing by sqrt(group_size). That
# construction is symmetric and involutory (`H == H.T == H^-1`, verified in the
# tests at 4/16/256/1024), which is what makes dequantisation possible at all:
# undo the scale, then apply the SAME rotation a second time to land back in the
# checkpoint's original, unrotated basis. Group sizes are therefore powers of 4.
#
# The result is an ordinary dequantized weight. It needs no special-casing for
# the plain (unrotated) activation this module always feeds it — activation-side
# rotation only exists on an int8-matmul fast path not implemented here — nor for
# LoRA deltas, which `apply_lora_deltas` adds in that same original basis exactly
# as for every other layer type.
#
# Fidelity note: the reference builds its Hadamard at fp32 for dequantisation,
# while this un-rotates in the activation's compute dtype to avoid a full
# weight-sized fp32 intermediate on every forward (151MB for one 6144x6144
# layer). Measured on a 512x1024 layer at group 256: int8 quantisation alone
# costs 0.786% mean relative error against the original weight, and doing the
# un-rotation in bf16 instead of fp32 takes that to 0.831% — an addition well
# inside the grid error the format already carries.
_CONVROT_HADAMARD_CACHE: dict[tuple[int, str, torch.dtype], torch.Tensor] = {}

# Format constant: THE regular 4x4 Hadamard the rotation is built from. Other
# valid H4s exist and are equally orthogonal, but would decode a ConvRot
# checkpoint to noise — this matrix is part of the file format, not a choice.
_CONVROT_H4 = ((1, 1, 1, -1), (1, 1, -1, 1), (1, -1, 1, 1), (-1, 1, 1, 1))


def _convrot_group_exponent(group_size: int) -> int:
    """The ``k`` in ``group_size == 4 ** k``, or raise.

    Integer arithmetic rather than ``log(group_size, 4)``: the group size gates a
    reshape of real checkpoint weights, and a float log landing a hair off an
    exact power would reject a valid file.
    """
    remainder = group_size
    exponent = 0
    while remainder > 1 and remainder % 4 == 0:
        remainder //= 4
        exponent += 1
    if group_size < 4 or remainder != 1:
        raise ValueError(f"ConvRot group size must be a power of 4, got {group_size}")
    return exponent


def _build_convrot_hadamard(
    group_size: int, device: "torch.device | str", dtype: torch.dtype,
) -> torch.Tensor:
    """Normalized regular Hadamard matrix for ConvRot, cached per (size, device, dtype)."""
    cache_key = (group_size, str(device), dtype)
    cached = _CONVROT_HADAMARD_CACHE.get(cache_key)
    if cached is not None:
        return cached
    exponent = _convrot_group_exponent(group_size)
    base = torch.tensor(_CONVROT_H4, dtype=dtype, device=device)
    h = base
    for _ in range(exponent - 1):
        h = torch.kron(h, base)
    h = h / (group_size ** 0.5)
    _CONVROT_HADAMARD_CACHE[cache_key] = h
    return h


def _convrot_unrotate_weight(weight: torch.Tensor, hadamard: torch.Tensor, group_size: int) -> torch.Tensor:
    """Undo ConvRot's offline weight rotation (the rotation is symmetric and
    self-inverse, so applying it a second time is the correct un-rotation)."""
    out_f, in_f = weight.shape
    if in_f % group_size != 0:
        raise ValueError(f"in_features {in_f} not divisible by ConvRot group size {group_size}")
    n_groups = in_f // group_size
    grouped = weight.reshape(out_f, n_groups, group_size)
    return torch.matmul(grouped, hadamard).reshape(out_f, in_f)


class Fp8ScaledLinear(manual_cast.Linear):
    """Linear for per-tensor scaled fp8 weights (``weight_scale``/``input_scale``),
    and for int8_tensorwise (plain or ConvRot-rotated) weights.

    v1 dequantises on forward. ``input_scale`` is captured for the future
    native-fp8 matmul fast path but is unused by the dequant path.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # non-persistent: excluded from this module's own state_dict and load.
        self.register_buffer("weight_scale", None, persistent=False)
        self.register_buffer("input_scale", None, persistent=False)
        self.register_buffer("convrot_hadamard", None, persistent=False)
        self.convrot_groupsize: int | None = None
        # AWQ per-input-channel activation smoothing scale (nvfp4 only, in the wild).
        self.register_buffer("pre_quant_scale", None, persistent=False)

    def _load_from_state_dict(
        self, state_dict, prefix, local_metadata, strict,
        missing_keys, unexpected_keys, error_msgs,
    ) -> None:
        # Two fp8-scaled spellings in the wild:
        #   modern (Klein fp8):  <layer>.weight_scale / <layer>.input_scale
        #   legacy (scaled_fp8): <layer>.scale_weight  (single scale, no input)
        ws = state_dict.pop(prefix + "weight_scale", None)
        if ws is None:
            ws = state_dict.pop(prefix + "scale_weight", None)
        # Input-activation scale has two spellings in the wild (`input_scale` on
        # Klein-fp8, `scale_input` on the qwen2.5-vl fp8 dump); captured for the
        # future native-fp8 matmul, unused by the v1 dequant path.
        is_ = state_dict.pop(prefix + "input_scale", None)
        if is_ is None:
            is_ = state_dict.pop(prefix + "scale_input", None)
        # AWQ per-input-channel activation smoothing scale (nvfp4 in the wild, e.g.
        # ModelOpt-quantised checkpoints). Applied to the ACTIVATION at forward time
        # (see forward_comfy_cast_weights / Nvfp4Linear.forward_comfy_cast_weights),
        # not to the weight — unlike weight_scale/input_scale it is not part of the
        # weight's own quantisation, so it is popped unconditionally, independent of
        # the comfy_quant format check below.
        pqs = state_dict.pop(prefix + "pre_quant_scale", None)
        # comfy_quant is a per-layer JSON descriptor blob (uint8) present on all
        # ComfyUI-quantised layers. The dequant math below is driven by the
        # scales, not the blob's other fields, EXCEPT that a format this module
        # can't correctly dequant (anything unrecognized) must fail the load
        # rather than silently running the plain weight*scale formula on codes
        # that aren't in that space, and int8_tensorwise+ConvRot needs its
        # groupsize to set up the un-rotation.
        quant_blob = state_dict.pop(prefix + "comfy_quant", None)
        layer_conf = _parse_comfy_quant(quant_blob)
        if layer_conf is not None:
            _check_quant_format_supported(layer_conf, prefix.rstrip("."))
            if layer_conf.get("format") == _COMFY_QUANT_INT8_TENSORWISE:
                convrot, groupsize = _extract_convrot_config(layer_conf, prefix.rstrip("."))
                if convrot:
                    self.convrot_groupsize = groupsize
                    # Built once here (load time), not per forward: cached
                    # globally by (groupsize, device, dtype) so every layer
                    # sharing a groupsize reuses the same kron-built matrix.
                    # torch.float32 to match the reference's dequant precision;
                    # registered as a buffer so the module's own .to()/_apply
                    # (partial-residency streaming included) moves it alongside
                    # the weight without a separate move call.
                    self.convrot_hadamard = _build_convrot_hadamard(groupsize, device="cpu", dtype=torch.float32)
        else:
            _reject_ambiguous_int8(state_dict.get(prefix + "weight"), ws, prefix.rstrip("."))
        if ws is not None:
            self.weight_scale = ws
        if is_ is not None:
            self.input_scale = is_
        if pqs is not None:
            self.pre_quant_scale = pqs
        super()._load_from_state_dict(
            state_dict, prefix, local_metadata, strict,
            missing_keys, unexpected_keys, error_msgs,
        )

    def forward_comfy_cast_weights(self, input: torch.Tensor) -> torch.Tensor:
        if self.pre_quant_scale is not None:
            # ModelOpt AWQ-style smoothing: scale the activation before anything
            # else runs (fast-path attempt or dequant), matching upstream's
            # ``input = input * cast_to_device(pre_quant_scale, input.device,
            # input.dtype)`` at the top of MixedPrecisionOps.Linear.forward.
            input = input * cast_to(
                self.pre_quant_scale, input.dtype, input.device, non_blocking=self.stream_non_blocking,
            )
        if self.weight_scale is not None:
            if _fp8_matmul_enabled():
                reject_reason = _scaled_mm_fast_path_reject_reason(
                    weight_dtype=self.weight.dtype,
                    has_weight_scale=True,
                    lora_deltas=self.lora_deltas,
                    input_dtype=input.dtype,
                    input_is_cuda=input.is_cuda,
                    weight_is_cuda=self.weight.is_cuda,
                    in_features=self.in_features,
                    out_features=self.out_features,
                )
                if reject_reason is None:
                    fast = self._forward_scaled_mm(input)
                    if fast is not None:
                        return fast
                    # else: an operand didn't meet _scaled_mm's requirements (non-scalar
                    # or non-finite/zero scale, unsupported layout, mixed device, or a
                    # runtime kernel rejection) -> fall through to the dequant path.
                else:
                    # The gate is on but a precondition never got the request
                    # anywhere near the kernel -- this is the case that used to
                    # be completely silent (see _log_scaled_mm_fast_path_rejection).
                    _log_scaled_mm_fast_path_rejection(reject_reason)
            # A quantised layer must dequant + matmul in a REAL float dtype. Some
            # archs derive their activation dtype from a weight's storage dtype
            # (Krea-2's timestep embed uses ``tmlp[0].weight.dtype``), which is fp8
            # after on-the-fly quantisation — so the incoming activation can itself
            # arrive as fp8. Upcast it to the compute dtype, exactly as a natively
            # fp8 checkpoint would run. Move to the activation device+dtype (not
            # just dtype): under partial residency a streamed fp8 layer's weight
            # lives on pinned CPU RAM, so the dequant copies it to input.device
            # (non_blocking when streamed).
            nb = self.stream_non_blocking
            dt = input.dtype if input.dtype not in _FP8_DTYPES else torch.bfloat16
            if input.dtype is not dt:
                input = input.to(dt)
            weight = cast_to(self.weight, dt, input.device, non_blocking=nb)
            weight = weight * cast_to(self.weight_scale, dt, input.device, non_blocking=nb)
            if self.convrot_hadamard is not None:
                hadamard = cast_to(self.convrot_hadamard, dt, input.device, non_blocking=nb)
                weight = _convrot_unrotate_weight(weight, hadamard, self.convrot_groupsize)
            bias = cast_to(self.bias, dt, input.device, non_blocking=nb) if self.bias is not None else None
        else:
            # non-quantised layer inside a mixed fp8 checkpoint.
            weight, bias = cast_bias_weight(self, input)
        weight = apply_lora_deltas(weight, self.lora_deltas)
        return F.linear(input, weight, bias)

    def _forward_scaled_mm(self, input: torch.Tensor) -> torch.Tensor | None:
        """Native fp8 GEMM via ``torch._scaled_mm`` — runs the matmul *in* fp8
        instead of dequantising to bf16/fp16 first, cashing in the fp8 storage for
        real compute (~1.3-1.6x on linear-dominated DiTs on sm89+ per the roadmap).
        Gated off by default behind ``$NATIVE_FP8_MATMUL`` (see
        :func:`_fp8_matmul_enabled`) until GPU-benchmarked; the caller
        (``forward_comfy_cast_weights``) has already checked the feature gate,
        the hardware/torch probe, dtype/device/shape preconditions, and that
        any active LoRA delta is expressible as an output-side low-rank branch
        (see :func:`_deltas_output_branch_ok` / :func:`_lora_output_branch`);
        anything else (LoKr, an out-of-bounds slice) stays on the dequant path.

        Returns ``None`` (signalling the caller to take the dequant fallback)
        rather than crashing whenever an operand doesn't satisfy ``_scaled_mm``:
        a non-scalar or non-finite/zero scale, an unsupported layout, operands on
        mixed devices (a streamed layer's weight may be prefetched onto the GPU
        while its scales/bias still sit on pinned CPU RAM), or a runtime kernel
        rejection (e.g. a stale process-wide capability probe on a second GPU).

        Layout invariant: the first operand must be row-major ``(M, K)`` and the
        second **column-major** ``(K, N)``. ``self.weight`` is stored
        ``(out_features, in_features)`` = ``(N, K)`` row-major, so ``weight.t()`` is
        ``(K, N)`` column-major with zero copy — but only when the stored weight is
        contiguous, which we enforce; the activation is made contiguous too.
        """
        device = input.device
        # Scalar, finite, positive scales only. The scalar-scaled fast path can't
        # express a per-output/broadcast weight_scale — that stays on dequant.
        w_scale_t, x_scale_t = self.weight_scale, self.input_scale
        if w_scale_t.numel() != 1 or (x_scale_t is not None and x_scale_t.numel() != 1):
            return None
        w_scale = w_scale_t.to(device=device, dtype=torch.float32).reshape(())
        if not bool(torch.isfinite(w_scale)) or float(w_scale) <= 0.0:
            return None

        orig_shape = input.shape
        x2d = input.reshape(-1, orig_shape[-1]).contiguous()
        if x_scale_t is not None:
            # Checkpoint-provided static activation scale — one less reduction
            # per forward than the dynamic amax below.
            x_scale = x_scale_t.to(device=device, dtype=torch.float32).reshape(())
            if not bool(torch.isfinite(x_scale)) or float(x_scale) <= 0.0:
                return None
            x_fp8 = (x2d.to(torch.float32) / x_scale).clamp(-_E4M3_MAX, _E4M3_MAX).to(torch.float8_e4m3fn)
        else:
            x_fp8, x_scale = _quantize_fp8_dynamic(x2d)
        # weight.t() is column-major only if the stored weight is contiguous row-major.
        weight = self.weight if self.weight.is_contiguous() else self.weight.contiguous()
        bias = self.bias.to(device=device, dtype=input.dtype) if self.bias is not None else None
        try:
            out = torch._scaled_mm(
                x_fp8, weight.t(),
                scale_a=x_scale, scale_b=w_scale,
                out_dtype=input.dtype, bias=bias,
            )
        except (RuntimeError, torch.cuda.OutOfMemoryError):
            # Layout/shape/device/capability rejection at the kernel — fall back.
            logger.debug("fp8 matmul: _scaled_mm rejected operands; using dequant path", exc_info=True)
            return None
        if self.lora_deltas:
            # The caller already proved (via _scaled_mm_fast_path_ok ->
            # _deltas_output_branch_ok) that every delta here is a plain
            # output-side low-rank branch, full-width or a dim-0 target_slice.
            # Added to the post-GEMM output -- bias is already baked in via
            # _scaled_mm's own bias= kwarg above.
            out = out + _lora_output_branch(x2d, self.lora_deltas, out.dtype, self.out_features)
        return out.reshape(*orig_shape[:-1], -1)


# ---------------------------------------------------------------------------
# NVFP4 (4-bit) dequantisation
# ---------------------------------------------------------------------------
#
# Format (verified against comfy/quant_ops.py QUANT_ALGOS["nvfp4"] + the pure-
# torch reference in comfy/float.py; the compiled comfy_kitchen kernel is not
# importable here, so float.py is the authority):
#   * weight        uint8  [out, in//2]     two 4-bit e2m1 codes per byte
#   * weight_scale  fp8e4m3 [out, in//16]   per-16-element block scale, stored in
#                                           NVIDIA "to_blocked" swizzled layout
#   * weight_scale_2 f32    scalar          per-tensor scale
#   dequant(v) = e2m1(code) * unblock(weight_scale) * weight_scale_2
#
# Nibble order (comfy/float.py line 95, `packed = (fp4[0::2] << 4) | fp4[1::2]`):
#   even element -> HIGH nibble, odd element -> LOW nibble.
# e2m1 code = (sign<<3)|(exp<<1)|mantissa (comfy/float.py `stochastic_float_to_fp4_e2m1`).

QUANT_NVFP4 = "nvfp4"
_NVFP4_BLOCK = 16
# Largest finite magnitude representable in e2m1 (the fp4 grid {0,.5,1,...,6}).
_F4_MAX = 6.0

# code -> signed value; code = (sign<<3)|(exp<<1)|mantissa. Magnitudes are the
# standard e2m1 grid {0,.5,1,1.5,2,3,4,6}.
_E2M1_LUT = torch.tensor(
    [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0,
     0.0, -0.5, -1.0, -1.5, -2.0, -3.0, -4.0, -6.0],
    dtype=torch.float32,
)

# 256-entry LUTs: the packed byte -> its high/low nibble's e2m1 value,
# precomputed once so the per-forward path is a single gather instead of a
# bit-shift + mask + int64 `codes` tensor built via strided [:, 0::2] writes.
# A uint8 index tensor is treated as a boolean MASK by torch's fancy indexing
# (not a gather index), so the byte range is expressed in int32 here -- the
# smallest integer dtype indexing actually accepts (not int64/long).
_BYTE_ARANGE_I32 = torch.arange(256, dtype=torch.int32)
_E2M1_LUT_HIGH = _E2M1_LUT[(_BYTE_ARANGE_I32 >> 4) & 0x0F]
_E2M1_LUT_LOW = _E2M1_LUT[_BYTE_ARANGE_I32 & 0x0F]

# Cache the inverse "to_blocked" permutation per padded (rows, cols) shape.
_INV_BLOCKED_CACHE: dict[tuple[int, int], torch.Tensor] = {}


def _to_blocked(m: torch.Tensor) -> torch.Tensor:
    """NVIDIA cublas d-block scale layout (verbatim from comfy/float.py)."""
    rows, cols = m.shape
    nrb = (rows + 127) // 128
    ncb = (cols + 3) // 4
    padded = m
    if (rows, cols) != (nrb * 128, ncb * 4):
        padded = torch.zeros((nrb * 128, ncb * 4), device=m.device, dtype=m.dtype)
        padded[:rows, :cols] = m
    blocks = padded.view(nrb, 128, ncb, 4).permute(0, 2, 1, 3)
    rearranged = blocks.reshape(-1, 4, 32, 4).transpose(1, 2).reshape(-1, 32, 16)
    return rearranged.reshape(nrb * 128, ncb * 4)


def _inv_blocked_index(rows: int, cols: int) -> torch.Tensor:
    """Permutation that un-blocks a stored [rows, cols] scale back to natural order."""
    key = (rows, cols)
    inv = _INV_BLOCKED_CACHE.get(key)
    if inv is None:
        idx = _to_blocked(torch.arange(rows * cols).reshape(rows, cols).float()).flatten().long()
        inv = torch.empty(rows * cols, dtype=torch.long)
        inv[idx] = torch.arange(rows * cols)
        _INV_BLOCKED_CACHE[key] = inv
    return inv


def _unblock_scale(block_stored: torch.Tensor, out_features: int, num_blocks: int) -> torch.Tensor:
    """Recover the natural [out, num_blocks] block scale from the swizzled store."""
    ph, pw = block_stored.shape  # padded to (mult of 128, mult of 4)
    inv = _inv_blocked_index(ph, pw).to(block_stored.device)
    natural = block_stored.to(torch.float32).flatten()[inv].reshape(ph, pw)
    return natural[:out_features, :num_blocks]


def _dequant_values(packed: torch.Tensor, out_features: int, in_features: int) -> torch.Tensor:
    """Recover the raw (unscaled) e2m1 magnitudes from packed nvfp4 codes.

    Gathers both nibbles of every byte directly from the 256-entry
    ``_E2M1_LUT_HIGH``/``_E2M1_LUT_LOW`` tables -- no int64 ``codes``
    tensor, no strided ``[:, 0::2]``/``[:, 1::2]`` scatter writes. Interleaving
    high/low via ``stack(..., dim=-1).view(...)`` reproduces the same element
    order the old scatter produced (even columns = high nibble, odd = low).
    """
    idx = packed.to(torch.int32)
    device = packed.device
    high = _E2M1_LUT_HIGH.to(device)[idx]
    low = _E2M1_LUT_LOW.to(device)[idx]
    return torch.stack((high, low), dim=-1).view(out_features, in_features)


def _scale_values(
    values: torch.Tensor, scale: torch.Tensor, out_features: int, in_features: int, num_blocks: int,
) -> torch.Tensor:
    """Apply a ``[out, num_blocks]`` scale to ``[out, in]`` dequantised values.

    Broadcasting a ``[out, blocks, 1]`` view of ``scale`` against a
    ``[out, blocks, 16]`` view of ``values`` performs the identical fp32
    elementwise multiply a ``repeat_interleave``d copy would --
    same two floats multiplied per output element, no allocation to hold the
    16x-duplicated scale.
    """
    scaled = values.view(out_features, num_blocks, _NVFP4_BLOCK) * scale.view(out_features, num_blocks, 1)
    return scaled.view(out_features, in_features)


def precompute_nvfp4_scale(
    block_scale: torch.Tensor, tensor_scale: torch.Tensor, out_features: int, in_features: int,
) -> torch.Tensor:
    """Natural (unblocked) ``[out, num_blocks]`` scale times the per-tensor
    scale, computed once at load time so ``Nvfp4Linear``'s forward
    hot path never calls ``_unblock_scale``/``_inv_blocked_index``."""
    num_blocks = in_features // _NVFP4_BLOCK
    block_nat = _unblock_scale(block_scale, out_features, num_blocks)
    return block_nat * tensor_scale.to(torch.float32)


def dequantize_nvfp4(
    packed: torch.Tensor,
    block_scale: torch.Tensor,
    tensor_scale: torch.Tensor,
    out_features: int,
    in_features: int,
) -> torch.Tensor:
    """Dequantise an nvfp4 weight to an fp32 ``[out_features, in_features]`` tensor.

    Public bit-exact reference, given the RAW on-disk tensors: unblocks
    ``block_scale`` on every call, so it is correct but not the load-once fast
    path. ``Nvfp4Linear.forward_comfy_cast_weights`` calls
    :func:`precompute_nvfp4_scale` once at load instead and never reaches this
    function.
    """
    device = packed.device
    values = _dequant_values(packed, out_features, in_features)
    num_blocks = in_features // _NVFP4_BLOCK
    scale = _unblock_scale(block_scale, out_features, num_blocks).to(device) * tensor_scale.to(torch.float32).to(device)
    return _scale_values(values, scale, out_features, in_features, num_blocks)


# ---------------------------------------------------------------------------
# NVFP4 native GEMM fast path
# ---------------------------------------------------------------------------
#
# torch 2.12.1+cu130 contract, read from THIS venv's torch source (not
# guessed) before writing any of this:
#   * torch/_meta_registrations.py:6982-7343 (_check_scaled_mm_sizes_v2, the
#     meta/shape-checker backing `aten._scaled_mm_v2` / `torch._scaled_mm_v2`):
#     a packed fp4 x fp4 GEMM needs `self` (activation) row-major, `mat2`
#     (weight) column-major, `self.size(1) % 16 == 0` and
#     `mat2.size(0/1) % 16 == 0` on the STORED (packed) shapes -- i.e. K
#     (unpacked) % 32 == 0 and N % 16 == 0. The two-level NVIDIA nvfp4 recipe
#     ("is_nv", line 7305-7325) is selected by `scale_recipe_{a,b} ==
#     [BlockWise1x16, TensorWise]` and requires per operand: a block e4m3
#     scale of `round_up(M_or_N, 128) * round_up(ceil_div(K, 16), 4)` elements
#     swizzled `SWIZZLE_32_4_4`, plus a separate scalar fp32 global scale.
#   * `round_up(M_or_N,128) * round_up(ceil_div(K,16),4)` is EXACTLY this
#     file's `_to_blocked`'s padded output element count -- confirmed against
#     `torch/testing/_internal/common_quantized.py:515-546`'s own `to_blocked`
#     reference helper, whose docstring cites the same NVIDIA cublas
#     "D-block-scaling-factors-layout" doc and has an IDENTICAL
#     view/permute/reshape/transpose chain to ours. So the checkpoint's
#     on-disk `weight_scale` (already stored in that swizzled layout, per
#     comfy_quant's own convention) is passed to the kernel AS-IS -- no
#     unblock, unlike the dequant path.
#   * v1 `torch._scaled_mm` (the fp8 fast path's kernel) CANNOT express this:
#     its `scale_a`/`scale_b` are single tensors, not the `[block, global]`
#     pair the nvfp4 recipe needs. The public wrapper that reaches
#     `_scaled_mm_v2` with the matching two-level recipe is
#     `torch.nn.functional.scaled_mm` (functional.py:6785-6866) -- confirmed
#     working end-to-end for exactly this recipe by
#     `sample_inputs_scaled_mm_v2` (common_methods_invocations.py:9101-9123),
#     which is the canonical OpInfo sample for "NVFP4" (two-level, as opposed
#     to the "Single-level NVFP4" sample just above it).
#   * `ScalingType`/`SwizzleType` are `torch._C._ScalingType`/`_SwizzleType`,
#     re-exported as `torch.nn.functional.ScalingType`/`SwizzleType`
#     (functional.py:14-15, 36-37) -- confirmed present in this build via
#     `ScalingType.__members__`/`SwizzleType.__members__`.

# Native nvfp4 GEMM fast path (torch._scaled_mm_v2 via F.scaled_mm) — see
# Nvfp4Linear.forward_comfy_cast_weights.
NATIVE_NVFP4_MATMUL_ENV = "NATIVE_NVFP4_MATMUL"
# Packed fp4 x fp4 tensor cores are Blackwell-only (sm120+, e.g. RTX 5090).
_NVFP4_MM_MIN_CAP = (12, 0)

_nvfp4_scaled_mm_supported_cache: bool | None = None


def _nvfp4_trial_gemm_ok() -> bool:
    """Run one tiny real nvfp4 x nvfp4 GEMM on the current CUDA device.

    A capability-number check alone (as the fp8 probe does) only proves the
    driver *reports* the right SM version -- it says nothing about whether
    this torch build's ``_scaled_mm_v2`` kernel is actually wired for the
    two-level NVIDIA recipe on it. ``_scaled_mm_v2`` is a much newer, less
    battle-tested surface than v1's ``_scaled_mm``, so a live trial run is the
    only thing that proves the whole chain (dtype support, swizzle handling,
    kernel dispatch) works, not just that the Python API exists.
    """
    try:
        device = torch.device("cuda")
        m, k, n = 16, 32, 16
        a = torch.zeros(m, k // 2, dtype=torch.uint8, device=device).view(torch.float4_e2m1fn_x2)
        b_row = torch.zeros(n, k // 2, dtype=torch.uint8, device=device).view(torch.float4_e2m1fn_x2)
        num_blocks = k // _NVFP4_BLOCK
        scale_a = _to_blocked(torch.ones(m, num_blocks, device=device)).to(torch.float8_e4m3fn)
        scale_b = _to_blocked(torch.ones(n, num_blocks, device=device)).to(torch.float8_e4m3fn)
        global_a = torch.ones((), dtype=torch.float32, device=device)
        global_b = torch.ones((), dtype=torch.float32, device=device)
        F.scaled_mm(
            a, b_row.t(),
            [scale_a, global_a], [F.ScalingType.BlockWise1x16, F.ScalingType.TensorWise],
            [scale_b, global_b], [F.ScalingType.BlockWise1x16, F.ScalingType.TensorWise],
            swizzle_a=[F.SwizzleType.SWIZZLE_32_4_4, F.SwizzleType.NO_SWIZZLE],
            swizzle_b=[F.SwizzleType.SWIZZLE_32_4_4, F.SwizzleType.NO_SWIZZLE],
            output_dtype=torch.bfloat16,
        )
        return True
    except Exception:  # noqa: BLE001 — any failure means "not usable here"
        return False


def _nvfp4_scaled_mm_supported() -> bool:
    """Probe once: can this process run the nvfp4 x nvfp4 ``_scaled_mm_v2``
    fast path (torch build exposes ``float4_e2m1fn_x2``/``F.scaled_mm``, CUDA
    available, device capability >= sm120, AND a live trial GEMM succeeds)?
    Cached; see :func:`reset_nvfp4_scaled_mm_probe` to re-probe (tests)."""
    global _nvfp4_scaled_mm_supported_cache
    if _nvfp4_scaled_mm_supported_cache is None:
        ok = False
        if (
            hasattr(torch, "float4_e2m1fn_x2")
            and hasattr(F, "scaled_mm")
            and hasattr(F, "ScalingType")
            and hasattr(F, "SwizzleType")
            and torch.cuda.is_available()
        ):
            try:
                if torch.cuda.get_device_capability() >= _NVFP4_MM_MIN_CAP:
                    ok = _nvfp4_trial_gemm_ok()
            except Exception:  # noqa: BLE001 — a driver hiccup must not break probing
                ok = False
        _nvfp4_scaled_mm_supported_cache = ok
        logger.debug("nvfp4 matmul: _nvfp4_scaled_mm_supported=%s", ok)
    return _nvfp4_scaled_mm_supported_cache


def reset_nvfp4_scaled_mm_probe() -> None:
    """Drop the cached nvfp4 ``_scaled_mm_v2`` support probe (tests only)."""
    global _nvfp4_scaled_mm_supported_cache
    _nvfp4_scaled_mm_supported_cache = None


def _nvfp4_matmul_enabled() -> bool:
    """Native nvfp4 GEMM fast-path policy, read from ``$NATIVE_NVFP4_MATMUL``.

    ``off`` (default) never uses the fast path — GPU-unvalidated as of this
    writing; it needs an A/B wall-time AND output-quality comparison against
    the LUT dequant path on a real nvfp4 checkpoint before the default
    flips (nvfp4 activation quantisation is lossier than fp8's dynamic
    quant, so a quality regression is a real possibility, not just a perf
    question). ``on``/``auto`` both require :func:`_nvfp4_scaled_mm_supported`;
    ``auto`` has no extra heuristic yet, mirroring ``_fp8_matmul_enabled``'s
    same "on until benchmarked" ``auto`` behavior. An unknown value is
    treated as ``off``.
    """
    policy = os.environ.get(NATIVE_NVFP4_MATMUL_ENV, "off").strip().lower()
    if policy == "off":
        return False
    if policy not in ("on", "auto"):
        logger.warning("nvfp4 matmul: unknown %s=%r; treating as 'off'", NATIVE_NVFP4_MATMUL_ENV, policy)
        return False
    return _nvfp4_scaled_mm_supported()


def _deltas_output_branch_ok(deltas: "list | None", out_features: int) -> bool:
    """True iff every delta in ``deltas`` is expressible as an output-side
    low-rank branch: ``((x @ down.T) @ up.T) * (scale * alpha /
    rank)``, algebraically identical to :func:`apply_lora_deltas`'s
    weight-side ``up @ down`` add for that delta.

    A dim-0 ``target_slice`` delta (e.g. a q/k/v-fused weight, where LoRA
    deltas authored per-projection land as a slice of the fused output rows)
    is included: :func:`_lora_output_branch` adds its term into
    ``out[..., start:start+length]``, mirroring the weight-side add's
    ``out[_slice_of(target_slice)]`` exactly. ``out_features`` is required
    here to validate the slice's bounds — the branch function only ever sees
    one delta at a time and can't cross-check them against each other.

    Excluded, conservatively:

      * LoKr (``d.kron``) — the branch math above only covers the plain
        ``up @ down`` rank expansion, not ``torch.kron``.
      * anything that doesn't duck-type as a plain ``LoraDelta`` (missing
        attrs, non-tensor ``up``/``down``, a rank mismatch between them, or a
        ``target_slice`` that isn't a dim-0 slice within ``out_features``).

    Any of the above → ``False``, which routes the caller to today's dequant
    fallback instead.
    """
    if not deltas:
        return True
    for d in deltas:
        try:
            if d.kron:
                return False
            down, up, target_slice = d.down, d.up, d.target_slice
        except AttributeError:
            return False
        if not isinstance(down, torch.Tensor) or not isinstance(up, torch.Tensor):
            return False
        if down.dim() != 2 or up.dim() != 2 or down.shape[0] != up.shape[1]:
            return False
        if target_slice is not None:
            dim, start, length = target_slice
            if dim != 0 or up.shape[0] != length or start < 0 or start + length > out_features:
                return False
    return True


# Token-dimension chunk budget for _lora_output_branch:
# picked so the largest per-chunk intermediate -- shape (chunk_rows, out_features)
# at out_dtype -- stays in the tens-of-MB range regardless of how many tokens the
# caller passes (stage-2 video upscales can be 4x+ the token count of stage 1).
_NVFP4_LORA_BRANCH_CHUNK_BYTES = 32 * 1024 * 1024


def _lora_output_branch(
    x2d: torch.Tensor, deltas: "list", out_dtype: torch.dtype, out_features: int,
) -> torch.Tensor:
    """Sum of the low-rank output contributions of ``deltas`` (already proven
    eligible by :func:`_deltas_output_branch_ok`) against the flattened
    activation ``x2d`` ``(M, in_features)``. Shared by the nvfp4 and fp8
    ``_scaled_mm``-family fast paths — both add this term to their raw GEMM
    output, before reshaping back to the caller's original leading dims.

    ``x @ (up @ down).T == (x @ down.T) @ up.T`` — computing it in this order
    never materialises the ``(out, in)`` weight-shaped delta, only ``(M,
    rank)``/``(M, length)`` intermediates, where ``length`` is the delta's
    ``target_slice`` width or ``out_features`` for a full-width delta. A
    sliced delta's term is added into ``total[..., start:start+length]``,
    mirroring the weight-side add's ``out[_slice_of(target_slice)]`` in
    :func:`apply_lora_deltas` exactly.

    Dtype: runs in ``out_dtype`` (the GEMM's compute dtype), not fp32. The
    fp32 that :func:`apply_lora_deltas` uses is applied to the ``(out, in)``
    weight-shaped delta -- bounded by weight size regardless of token count --
    and is cast down to compute dtype BEFORE ``F.linear`` ever runs; the real
    GEMM in that fallback path is always compute-dtype, never fp32. Matching
    that here costs no accuracy (LoRA ranks are small -- a handful to a few
    dozen -- so a compute-dtype reduction over the rank axis is extremely
    well-conditioned) while avoiding an ``(M, in_features)``/``(M,
    out_features)`` fp32 allocation that scales with token count instead of
    weight size, which is what OOM'd a 2x-resolution stage-2 video sample.

    Chunking: ``x2d`` is walked in row chunks of
    :data:`_NVFP4_LORA_BRANCH_CHUNK_BYTES` worth of ``(rows, out_features)``
    output so peak memory for this function is independent of ``M`` — large
    token counts (long/high-res video) no longer allocate an
    activation-sized intermediate in one shot.
    """
    m, _k = x2d.shape
    prepared = [
        (
            d.down.to(device=x2d.device, dtype=out_dtype),
            d.up.to(device=x2d.device, dtype=out_dtype),
            float(d.scale) * float(d.alpha) / d.down.shape[0],
            d.target_slice,
        )
        for d in deltas
    ]
    total = x2d.new_zeros((m, out_features), dtype=out_dtype)
    itemsize = total.element_size()
    chunk_rows = max(1, _NVFP4_LORA_BRANCH_CHUNK_BYTES // max(1, out_features * itemsize))
    for start in range(0, m, chunk_rows):
        end = min(start + chunk_rows, m)
        xc = x2d[start:end].to(out_dtype)
        for down, up, coeff, target_slice in prepared:
            term = (xc @ down.t()) @ up.t()
            term = term * coeff
            if target_slice is not None:
                _dim, s, length = target_slice
                total[start:end, s:s + length] += term
            else:
                total[start:end] += term
    return total


def _nvfp4_fast_path_reject_reason(
    *,
    lora_deltas: "list | None",
    input_dtype: torch.dtype,
    input_is_cuda: bool,
    weight_is_cuda: bool,
    in_features: int,
    out_features: int,
) -> str | None:
    """Which nvfp4 ``_scaled_mm_v2`` fast-path precondition failed, or ``None``
    if they all hold. Single source of truth for :func:`_nvfp4_scaled_mm_fast_path_ok`
    (a plain ``is None`` over this) and for the one-shot observability log at
    the ``forward_comfy_cast_weights`` call site — see that function's comment
    for why this needed a name at all: today nothing distinguishes "took the
    native nvfp4 GEMM" from "silently fell through to LUT dequant", which is
    exactly how a ~30s-per-prompt regression went unnoticed.
    """
    if lora_deltas and not _deltas_output_branch_ok(lora_deltas, out_features):
        return "lora_deltas"
    if input_dtype not in (torch.float16, torch.bfloat16):
        return f"input_dtype={input_dtype}"
    if not input_is_cuda:
        return "input_not_cuda"
    if not weight_is_cuda:
        return "weight_not_cuda"
    if in_features % 32:
        return "in_features_not_multiple_of_32"
    if out_features % 16:
        return "out_features_not_multiple_of_16"
    return None


def _nvfp4_scaled_mm_fast_path_ok(
    *,
    lora_deltas: "list | None",
    input_dtype: torch.dtype,
    input_is_cuda: bool,
    weight_is_cuda: bool,
    in_features: int,
    out_features: int,
) -> bool:
    """Pure predicate for the nvfp4 ``_scaled_mm_v2`` fast-path preconditions.

    Kept separate from the ``$NATIVE_NVFP4_MATMUL`` gate + hardware probe
    (:func:`_nvfp4_matmul_enabled`) so it's testable with plain values instead
    of real CUDA tensors — mirrors :func:`_scaled_mm_fast_path_ok`.

    LoRA: deltas no longer force the dequant fallback outright —
    only deltas :func:`_deltas_output_branch_ok` can't prove expressible as
    an output-side low-rank branch do (LoKr, an out-of-bounds
    ``target_slice``, anything unrecognised).

    Alignment is on the STORED (packed) shapes per
    ``_check_scaled_mm_sizes_v2``: the activation's packed column count and
    the weight's packed row count both need ``% 16 == 0``, i.e. the UNPACKED
    ``in_features`` needs ``% 32 == 0``; the weight's packed column count
    (``out_features``, never packed) needs ``% 16 == 0``.
    """
    return _nvfp4_fast_path_reject_reason(
        lora_deltas=lora_deltas, input_dtype=input_dtype,
        input_is_cuda=input_is_cuda, weight_is_cuda=weight_is_cuda,
        in_features=in_features, out_features=out_features,
    ) is None


# First-occurrence-per-reason log of why a Linear skipped the native nvfp4
# GEMM and fell through to LUT dequant. Deliberately NOT a per-Linear
# per-forward log (forward_comfy_cast_weights runs ~350 times per step) --
# one line per distinct reason for the life of the process is enough to tell
# an operator "every layer is dequanting because activations are fp32"
# without flooding the log.
_nvfp4_fast_path_reject_reasons_logged: set[str] = set()


def _log_nvfp4_fast_path_rejection(reason: str) -> None:
    if reason in _nvfp4_fast_path_reject_reasons_logged:
        return
    _nvfp4_fast_path_reject_reasons_logged.add(reason)
    logger.warning(
        "nvfp4 matmul: fast path unavailable (%s); using LUT dequant instead "
        "(logged once per distinct reason)", reason,
    )


def reset_nvfp4_fast_path_rejection_log() -> None:
    """Drop the one-shot rejection-reason log dedup set (tests only)."""
    _nvfp4_fast_path_reject_reasons_logged.clear()


def _quantize_nvfp4_dynamic(x2d: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """Dynamic per-tensor+per-block nvfp4 activation quantisation for the
    ``_scaled_mm_v2`` fast path.

    ``x2d`` is ``(M, K)`` with ``K % 32 == 0`` (already checked by the
    caller). Returns ``(packed_u8 [M, K // 2], block_scale_swizzled_e4m3,
    global_scale_f32_scalar)`` in the SAME on-disk shape/layout as a
    checkpoint's nvfp4 weight, so :func:`dequantize_nvfp4` (or a
    :func:`_to_blocked` weight scale) can be compared against it directly.

    Algorithm ported from ``comfy/float.py``'s round-to-nearest branch (the
    same authority the weight-format port uses) — its stochastic-
    rounding branch is for SAVING checkpoints and is not used here; a live
    per-forward activation quantiser always rounds to nearest. One
    deliberate addition beyond that reference: an all-zero 16-element block
    (e.g. a padded/masked activation slice — routine at runtime, unlike a
    static trained weight block, which is essentially never exactly zero)
    divides by a zero block scale in the reference formula, i.e. ``0/0 =
    NaN``; the divisor here is floored at a tiny epsilon so that case yields
    an exact ``0`` instead, matching what the (also exactly-zero) stored
    scale would dequantise to anyway.
    """
    m, k = x2d.shape
    xb = x2d.reshape(m, -1, _NVFP4_BLOCK).to(torch.float32)
    amax = xb.abs().amax().clamp(min=1e-12)
    tensor_scale = amax / (_F4_MAX * _E4M3_MAX)
    block_amax = xb.abs().amax(-1)
    block = torch.clamp(block_amax / _F4_MAX / tensor_scale, max=_E4M3_MAX).to(torch.float8_e4m3fn)
    safe_block = block.to(torch.float32).clamp(min=torch.finfo(torch.float32).tiny)
    xn = xb / (tensor_scale * safe_block).unsqueeze(-1)
    sign = torch.signbit(xn).to(torch.uint8)
    ax = xn.abs()
    exp = torch.floor(torch.log2(ax) + 1.1925).clamp(0, 3)
    mant = torch.where(
        exp > 0, (ax / (2.0 ** (exp - 1)) - 1.0) * 2.0, ax * 2.0,
    ).round().clamp(0, 1).to(torch.uint8)
    codes = ((sign << 3) | (exp.to(torch.uint8) << 1) | mant).reshape(m, k)
    packed = (codes[:, 0::2] << 4) | codes[:, 1::2]
    block_sw = _to_blocked(block.to(torch.float32)).to(torch.float8_e4m3fn)
    return packed, block_sw, tensor_scale.reshape(())


class Nvfp4Linear(Fp8ScaledLinear):
    """Linear that also handles nvfp4 (4-bit) layers inside a mixed checkpoint.

    Extends ``Fp8ScaledLinear``: a layer carrying ``weight_scale_2`` is nvfp4 and
    is dequantised on forward; every other layer falls through to the fp8-scaled /
    plain-cast behaviour of the base. This lets a single module hold a mix of fp8
    and nvfp4 linears (e.g. ``qwen_3_8b_fp8mixed``: fp8 layers 0-23, nvfp4 24-35).
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._is_nvfp4 = False
        # non-persistent: never in this module's own state_dict.
        self.register_buffer("nvfp4_packed", None, persistent=False)
        # Precomputed at load: natural block scale * tensor scale,
        # [out_features, in_features // _NVFP4_BLOCK]. The dequant forward
        # path only ever needs this one already-unblocked tensor, never the
        # swizzled on-disk scale or the "to_blocked" inverse permutation.
        self.register_buffer("nvfp4_scale", None, persistent=False)
        # Kept ALONGSIDE nvfp4_scale, not instead of it: the native
        # GEMM fast path needs the RAW swizzled block scale and the separate
        # global scale exactly as stored on disk -- torch._scaled_mm_v2's
        # nvfp4 recipe consumes the cublas-blocked layout directly, unlike
        # the dequant path which needs it unblocked.
        self.register_buffer("nvfp4_weight_block_scale", None, persistent=False)
        self.register_buffer("nvfp4_weight_global_scale", None, persistent=False)

    def _load_from_state_dict(
        self, state_dict, prefix, local_metadata, strict,
        missing_keys, unexpected_keys, error_msgs,
    ) -> None:
        ws2 = state_dict.pop(prefix + "weight_scale_2", None)
        if ws2 is None:
            # fp8-scaled or plain layer: base handles it (and pops comfy_quant).
            super()._load_from_state_dict(
                state_dict, prefix, local_metadata, strict,
                missing_keys, unexpected_keys, error_msgs,
            )
            return

        packed = state_dict.pop(prefix + "weight", None)
        block = state_dict.pop(prefix + "weight_scale", None)
        state_dict.pop(prefix + "input_scale", None)   # unused by the v1 dequant path
        state_dict.pop(prefix + "comfy_quant", None)
        if packed is None or block is None:
            raise ValueError(f"nvfp4 layer '{prefix.rstrip('.')}' missing weight/weight_scale")

        self._is_nvfp4 = True
        self.nvfp4_packed = packed if packed.dtype == torch.uint8 else packed.view(torch.uint8)
        global_scale = ws2.reshape(())
        self.nvfp4_scale = precompute_nvfp4_scale(block, global_scale, self.out_features, self.in_features)
        self.nvfp4_weight_block_scale = block if block.dtype == torch.float8_e4m3fn else block.to(torch.float8_e4m3fn)
        self.nvfp4_weight_global_scale = global_scale.to(torch.float32)
        # Free the empty [out, in] float weight the constructor allocated.
        self.weight = None

        # Let the base load bias etc., then clear the popped keys from missing.
        super()._load_from_state_dict(
            state_dict, prefix, local_metadata, strict,
            missing_keys, unexpected_keys, error_msgs,
        )
        for name in ("weight", "weight_scale", "weight_scale_2", "input_scale", "comfy_quant", "pre_quant_scale"):
            key = prefix + name
            if key in missing_keys:
                missing_keys.remove(key)

    def forward_comfy_cast_weights(self, input: torch.Tensor) -> torch.Tensor:
        if not self._is_nvfp4:
            return super().forward_comfy_cast_weights(input)
        if self.pre_quant_scale is not None:
            # Same AWQ smoothing as the base class's non-nvfp4 path, applied
            # here too since the nvfp4 branch never reaches
            # Fp8ScaledLinear.forward_comfy_cast_weights.
            input = input * cast_to(
                self.pre_quant_scale, input.dtype, input.device, non_blocking=self.stream_non_blocking,
            )
        if _nvfp4_matmul_enabled():
            reject_reason = _nvfp4_fast_path_reject_reason(
                lora_deltas=self.lora_deltas,
                input_dtype=input.dtype,
                input_is_cuda=input.is_cuda,
                weight_is_cuda=self.nvfp4_packed.is_cuda,
                in_features=self.in_features,
                out_features=self.out_features,
            )
            if reject_reason is None:
                fast = self._forward_nvfp4_scaled_mm(input)
                if fast is not None:
                    return fast
                # else: the kernel itself rejected the operands (mixed
                # devices, activation quant failure, runtime rejection) --
                # already logged by _forward_nvfp4_scaled_mm at debug level;
                # fall through to the dequant path below.
            else:
                # The gate is on but a precondition never got the request
                # anywhere near the kernel -- this is the case that used to
                # be completely silent (see _log_nvfp4_fast_path_rejection).
                _log_nvfp4_fast_path_rejection(reject_reason)
        # The scale is already unblocked (precomputed at load); the
        # hot path is just a LUT gather + one broadcasted multiply, never
        # _unblock_scale/_inv_blocked_index. nvfp4_scale is a buffer moved
        # alongside nvfp4_packed by the module's own .to()/_apply machinery
        # (partial-residency streaming included), so the two are always
        # co-located when this runs.
        values = _dequant_values(self.nvfp4_packed, self.out_features, self.in_features)
        num_blocks = self.in_features // _NVFP4_BLOCK
        weight = _scale_values(values, self.nvfp4_scale, self.out_features, self.in_features, num_blocks)
        weight = weight.to(device=input.device, dtype=input.dtype)
        weight = apply_lora_deltas(weight, self.lora_deltas)
        bias = None if self.bias is None else self.bias.to(input.dtype)
        return F.linear(input, weight, bias)

    def _forward_nvfp4_scaled_mm(self, input: torch.Tensor) -> torch.Tensor | None:
        """Native nvfp4 GEMM via ``torch._scaled_mm_v2`` (``F.scaled_mm``) —
        runs the matmul directly on packed fp4 codes on both operands
        instead of dequantising the weight to bf16/fp16 first. Blackwell
        (sm120+) tensor cores only; gated off by default behind
        ``$NATIVE_NVFP4_MATMUL`` (see :func:`_nvfp4_matmul_enabled`) until
        GPU A/B-benchmarked for both speed and output quality. The caller
        (``forward_comfy_cast_weights``) has already checked the feature
        gate, the hardware/kernel probe, and the dtype/device/shape/LoRA
        preconditions.

        Returns ``None`` (signalling the caller to take the dequant
        fallback) rather than crashing whenever an operand doesn't satisfy
        ``_scaled_mm_v2``: mixed devices (a streamed layer's weight may be
        prefetched onto the GPU while its scales sit on pinned CPU RAM), a
        dynamic-quant failure, or a runtime kernel rejection.

        The bias is added AFTER the GEMM on the host side rather than passed
        to the kernel: unlike v1's ``_scaled_mm``, ``_scaled_mm_v2``'s meta
        registration (``_check_scaled_mm_sizes_v2``) has no bias shape/dtype
        check for the nvfp4 recipe, so its exact kernel-level semantics here
        are unverified — a plain elementwise add on the (small) ``[M, N]``
        output is cheap and unambiguous.

        Layout invariant: the first operand must be row-major ``(M, K)`` and
        the second **column-major** ``(K, N)`` — same convention as the fp8
        fast path (see ``_forward_scaled_mm``'s docstring).
        """
        device = input.device
        w_block = self.nvfp4_weight_block_scale
        w_global = self.nvfp4_weight_global_scale
        if w_block is None or w_global is None:
            return None
        if w_block.device != device or self.nvfp4_packed.device != device:
            return None

        orig_shape = input.shape
        x2d = input.reshape(-1, orig_shape[-1])
        if not x2d.is_contiguous():
            x2d = x2d.contiguous()

        try:
            x_packed, x_block, x_global = _quantize_nvfp4_dynamic(x2d)
        except Exception:  # noqa: BLE001 — degrade to dequant, never crash a forward
            logger.debug("nvfp4 matmul: activation quant failed; using dequant path", exc_info=True)
            return None

        weight_packed = self.nvfp4_packed if self.nvfp4_packed.is_contiguous() else self.nvfp4_packed.contiguous()
        mat_a = x_packed.view(torch.float4_e2m1fn_x2)
        mat_b = weight_packed.view(torch.float4_e2m1fn_x2).t()
        bias = self.bias.to(device=device, dtype=input.dtype) if self.bias is not None else None

        try:
            out = F.scaled_mm(
                mat_a, mat_b,
                [x_block.contiguous(), x_global], [F.ScalingType.BlockWise1x16, F.ScalingType.TensorWise],
                [w_block.contiguous(), w_global], [F.ScalingType.BlockWise1x16, F.ScalingType.TensorWise],
                swizzle_a=[F.SwizzleType.SWIZZLE_32_4_4, F.SwizzleType.NO_SWIZZLE],
                swizzle_b=[F.SwizzleType.SWIZZLE_32_4_4, F.SwizzleType.NO_SWIZZLE],
                output_dtype=input.dtype,
            )
        except (RuntimeError, torch.cuda.OutOfMemoryError):
            # Layout/shape/device/capability rejection at the kernel — fall back.
            logger.debug("nvfp4 matmul: _scaled_mm_v2 rejected operands; using dequant path", exc_info=True)
            return None
        if self.lora_deltas:
            # The caller already proved (via _nvfp4_scaled_mm_fast_path_ok
            # -> _deltas_output_branch_ok) that every delta here is a plain
            # output-side low-rank branch, full-width or a dim-0 target_slice.
            # Added to the pre-bias GEMM output — this is the same term a
            # weight-side add (apply_lora_deltas) would contribute to
            # F.linear's matmul, before its own bias add.
            out = out + _lora_output_branch(x2d, self.lora_deltas, out.dtype, self.out_features)
        if bias is not None:
            out = out + bias
        return out.reshape(*orig_shape[:-1], -1)


class fp8_ops(manual_cast):
    """manual_cast with a Linear that handles fp8-scaled AND nvfp4 layers."""

    Linear = Nvfp4Linear


def detect_quant_format(
    metadata: dict[str, str],
    sd: dict[str, torch.Tensor],
) -> str | None:
    """Determine the quantisation format from checkpoint metadata + keys.

    Returns ``QUANT_FP8_SCALED`` when a scaled-quant scheme is present (fp8-scaled
    OR nvfp4 — both are served by ``fp8_ops``/``Nvfp4Linear``, which sorts out the
    per-layer format at load):
      * ``_quantization_metadata`` header (modern Klein-fp8 style) — but ONLY when
        ``sd`` itself carries at least one tensor actually stored in a quantised
        dtype (see below). ``metadata`` is the checkpoint's file-level
        ``__metadata__`` block, unfiltered by ``load_torch_file_prefixed`` even
        when ``sd`` has been sliced to one component's keys (see
        ``NativeEngineLoader._load_vae`` et al. in ``engine.py``). LTX's
        all-in-one file (DiT + video VAE + audio VAE + vocoder in one
        ``.safetensors``) carries a single ``_quantization_metadata`` header
        describing only the ``model.diffusion_model.*`` layers — its bf16
        ``vae.``/``audio_vae.``/``vocoder.`` components inherited the marker
        despite none of their own tensors being quantised. Gating on the sd's own
        dtypes makes the check per-component instead of per-file.
      * a top-level ``scaled_fp8`` marker tensor (legacy ComfyUI style),
      * any ``*.weight_scale`` / ``*.scale_weight`` / ``*.weight_scale_2`` key, or
      * any ``*.comfy_quant`` per-layer descriptor key.

    The descriptor check is what makes recognition independent of a format's
    scale-key spelling: an int8_tensorwise checkpoint would also be caught by its
    ``*.weight_scale`` keys, but only because it happens to share fp8's spelling.
    ``*.comfy_quant`` is the marker ComfyUI writes on every quantised layer
    whatever the format.

    The latter three checks already only see the keys actually present in ``sd``
    (the caller's already-sliced component), so they are inherently
    component-safe and need no extra gating.

    Callers must pass this to ``pick_operations`` — the majority-float dtype is
    unreliable for these checkpoints (the F32 scales usually outnumber the fp8/fp4
    weights), so storage-dtype sniffing alone would miss them.
    """
    if "_quantization_metadata" in metadata:
        if any(t.dtype in _FP8_DTYPES for t in sd.values()):
            return QUANT_FP8_SCALED
    if "scaled_fp8" in sd:
        return QUANT_FP8_SCALED
    for k in sd:
        if (
            k.endswith(".weight_scale")
            or k.endswith(".scale_weight")
            or k.endswith(".weight_scale_2")
            or k.endswith(".comfy_quant")
        ):
            return QUANT_FP8_SCALED
    return None


def pick_operations(
    storage_dtype: torch.dtype,
    compute_dtype: torch.dtype,
    quant_format: str | None = None,
) -> type[disable_weight_init]:
    """Choose the ops namespace for a checkpoint.

    ``quant_format`` takes priority (a fp8-scaled checkpoint always needs
    ``fp8_ops`` regardless of the majority dtype). Otherwise: cast namespace
    when storage != compute, plain namespace when they match.
    """
    if quant_format == QUANT_FP8_SCALED or storage_dtype in _FP8_DTYPES:
        logger.debug("pick_operations -> fp8_ops (quant=%s storage=%s)", quant_format, storage_dtype)
        return fp8_ops
    if storage_dtype != compute_dtype:
        logger.debug("pick_operations -> manual_cast (storage=%s compute=%s)", storage_dtype, compute_dtype)
        return manual_cast
    logger.debug("pick_operations -> disable_weight_init (dtype=%s)", storage_dtype)
    return disable_weight_init
