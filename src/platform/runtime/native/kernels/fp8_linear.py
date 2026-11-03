# Derived from: comfy/ops.py (ComfyUI, GPL-3.0); see vendor/NOTICE.md
from __future__ import annotations

import logging
from contextlib import contextmanager

import torch
import torch.nn.functional as F

from src.platform.runtime.cuda_allocator import is_driver_oom
from src.platform.settings.runtime_flags import runtime_flag
from ..errors import is_cuda_oom, retry_after_cuda_oom
from ..io.quant_formats import (
    COMFY_QUANT_INT8_TENSORWISE,
    E4M3_MAX,
    FP8_DTYPES,
    build_convrot_hadamard,
    check_quant_format_supported,
    convrot_unrotate_weight,
    extract_convrot_config,
    parse_comfy_quant,
    reject_ambiguous_int8,
)
from .casting import cast_bias_weight, cast_to, manual_cast
from .gemm import gemm_support
from .row_chunks import ChunkedGemmFailed, gemm_in_row_chunks
from .lora_math import (
    _add_lora_output_branch,
    _deltas_output_branch_ok,
    _lora_dequant_path,
    _lora_fused_enabled,
    _lora_output_branch,
    _note_lora_path,
    _with_lokr,
    apply_lora_deltas,
    linear_with_lora_deltas,
    partition_output_branch_deltas,
)

logger = logging.getLogger(__name__)

FP8_MATMUL_FLAG = "native_fp8_matmul"


def _fp8_matmul_enabled() -> bool:
    if not runtime_flag(FP8_MATMUL_FLAG):
        return False
    return gemm_support().fp8_scaled_mm


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
    allow_weight_staging: bool = False,
) -> str | None:
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
    if not weight_is_cuda and not allow_weight_staging:
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
    allow_weight_staging: bool = False,
) -> bool:
    return _scaled_mm_fast_path_reject_reason(
        weight_dtype=weight_dtype,
        has_weight_scale=has_weight_scale,
        lora_deltas=lora_deltas,
        input_dtype=input_dtype,
        input_is_cuda=input_is_cuda,
        weight_is_cuda=weight_is_cuda,
        in_features=in_features,
        out_features=out_features,
        allow_weight_staging=allow_weight_staging,
    ) is None


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
    _scaled_mm_fast_path_reject_reasons_logged.clear()


def fp8_activation_scale(x2d: torch.Tensor) -> torch.Tensor:
    amax = torch.linalg.vector_norm(x2d.detach(), ord=float("inf")).clamp(min=1e-8).to(torch.float32)
    return amax / E4M3_MAX


def _quantize_fp8_dynamic(x2d: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    scale = fp8_activation_scale(x2d)
    return _quantize_fp8(x2d, scale), scale


def _quantize_fp8(x2d: torch.Tensor, scale: torch.Tensor) -> torch.Tensor:
    x = x2d.to(torch.float32, copy=True)
    return x.div_(scale).clamp_(-E4M3_MAX, E4M3_MAX).to(torch.float8_e4m3fn)


class _PreparedLinearOperand:
    __slots__ = ("_run",)

    def __init__(self, run) -> None:
        self._run = run

    def __call__(self, x_chunk: torch.Tensor) -> torch.Tensor:
        return self._run(x_chunk)


class _Unset:
    pass


_UNSET = _Unset()


def _read_finite_positive_scalar(t: torch.Tensor) -> float | None:
    if not bool(torch.isfinite(t)):
        return None
    value = float(t)
    return value if value > 0.0 else None


def _scale_version_marker(scale: torch.Tensor) -> "int | object":
    if scale.is_inference():
        return object()
    try:
        return int(scale._version)
    except RuntimeError:
        return object()


def _scale_identity_key(scale: torch.Tensor, device: "torch.device | str") -> tuple:
    return (id(scale), _scale_version_marker(scale), scale.device, scale.dtype, scale.numel(), device)


class Fp8ScaledLinear(manual_cast.Linear):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.register_buffer("weight_scale", None, persistent=False)
        self.register_buffer("input_scale", None, persistent=False)
        self.register_buffer("convrot_hadamard", None, persistent=False)
        self.convrot_groupsize: int | None = None
        self.register_buffer("pre_quant_scale", None, persistent=False)
        self._scale_preflight: dict[str, tuple] = {}

    def _cached_scaled_mm_scale(
        self, slot: str, scale: torch.Tensor, device: "torch.device | str",
    ) -> torch.Tensor | None:
        key = _scale_identity_key(scale, device)
        cached = self._scale_preflight.get(slot)
        if cached is not None and cached[0] == key:
            return cached[1]
        if scale.numel() != 1:
            result = None
        else:
            moved = scale.to(device=device, dtype=torch.float32).reshape(())
            result = moved if _read_finite_positive_scalar(moved) is not None else None
        self._scale_preflight[slot] = (key, result)
        return result

    def _load_from_state_dict(
        self, state_dict, prefix, local_metadata, strict,
        missing_keys, unexpected_keys, error_msgs,
    ) -> None:
        ws = state_dict.pop(prefix + "weight_scale", None)
        if ws is None:
            ws = state_dict.pop(prefix + "scale_weight", None)
        is_ = state_dict.pop(prefix + "input_scale", None)
        if is_ is None:
            is_ = state_dict.pop(prefix + "scale_input", None)
        pqs = state_dict.pop(prefix + "pre_quant_scale", None)
        quant_blob = state_dict.pop(prefix + "comfy_quant", None)
        layer_conf = parse_comfy_quant(quant_blob)
        if layer_conf is not None:
            check_quant_format_supported(layer_conf, prefix.rstrip("."))
            if layer_conf.get("format") == COMFY_QUANT_INT8_TENSORWISE:
                convrot, groupsize = extract_convrot_config(layer_conf, prefix.rstrip("."))
                if convrot:
                    self.convrot_groupsize = groupsize
                    self.convrot_hadamard = build_convrot_hadamard(groupsize, device="cpu", dtype=torch.float32).clone()
        else:
            reject_ambiguous_int8(state_dict.get(prefix + "weight"), ws, prefix.rstrip("."))
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
            input = input * cast_to(
                self.pre_quant_scale, input.dtype, input.device, non_blocking=self.stream_non_blocking,
            )
        if self.weight_scale is not None:
            dequant_reason = "fp8 matmul off"
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
                    allow_weight_staging=True,
                )
                if reject_reason is None:
                    fast = self._forward_scaled_mm(input)
                    if fast is not None:
                        return fast
                    dequant_reason = "kernel rejected"
                else:
                    _log_scaled_mm_fast_path_rejection(reject_reason)
                    dequant_reason = reject_reason
            output_side, weight_side = partition_output_branch_deltas(
                self.lora_deltas, self.out_features)
            if self.lora_deltas:
                _note_lora_path(self, _lora_dequant_path(weight_side, dequant_reason, output_side))
            weight, bias, dt = self._prepare_dequant_operand(
                input.dtype, input.device, deltas=weight_side)
            if input.dtype is not dt:
                input = input.to(dt)
            out = F.linear(input, weight, bias)
            return _add_lora_output_branch(out, input, output_side, self.out_features, owner=self)
        weight, bias = cast_bias_weight(self, input)
        if self.lora_deltas:
            _note_lora_path(self, "dequant (cast)")
        return linear_with_lora_deltas(input, weight, bias, self.lora_deltas, self.out_features, owner=self)

    def _prepare_dequant_operand(
        self, dtype: torch.dtype, device: "torch.device | str",
        deltas: "list | None | _Unset" = _UNSET,
    ) -> tuple[torch.Tensor, "torch.Tensor | None", torch.dtype]:
        if isinstance(deltas, _Unset):
            deltas = self.lora_deltas
        nb = self.stream_non_blocking
        dt = dtype if dtype not in FP8_DTYPES else torch.bfloat16
        weight = cast_to(self.weight, dt, device, non_blocking=nb)
        weight = weight * cast_to(self.weight_scale, dt, device, non_blocking=nb)
        if self.convrot_hadamard is not None:
            hadamard = cast_to(self.convrot_hadamard, dt, device, non_blocking=nb)
            weight = convrot_unrotate_weight(weight, hadamard, self.convrot_groupsize)
        bias = cast_to(self.bias, dt, device, non_blocking=nb) if self.bias is not None else None
        weight = apply_lora_deltas(weight, deltas)
        return weight, bias, dt

    @contextmanager
    def prepared_linear(self, sample: torch.Tensor):
        if self.weight_scale is None or getattr(self, "_is_nvfp4", False) \
                or self._forward_pre_hooks or self._forward_hooks:
            yield self
            return
        dtype, device = sample.dtype, sample.device
        nb = self.stream_non_blocking
        state: dict = {
            "pre_quant_scale": None,
            "fast_weight": None, "w_scale": None,
            "dequant_weight": None, "dequant_bias": None, "dequant_dt": None,
            "downgraded": False,
        }
        if self.pre_quant_scale is not None:
            state["pre_quant_scale"] = cast_to(self.pre_quant_scale, dtype, device, non_blocking=nb)

        use_fast = False
        if _fp8_matmul_enabled():
            reject_reason = _scaled_mm_fast_path_reject_reason(
                weight_dtype=self.weight.dtype,
                has_weight_scale=True,
                lora_deltas=self.lora_deltas,
                input_dtype=dtype,
                input_is_cuda=sample.is_cuda,
                weight_is_cuda=self.weight.is_cuda,
                in_features=self.in_features,
                out_features=self.out_features,
                allow_weight_staging=True,
            )
            if reject_reason is None:
                fw = self._stage_scaled_mm_weight(device)
                if fw is not None:
                    ws = self._cached_scaled_mm_scale("weight", self.weight_scale, device)
                    if ws is not None:
                        state["fast_weight"] = fw
                        state["w_scale"] = ws
                        use_fast = True
                    ws = None
                fw = None
            else:
                _log_scaled_mm_fast_path_rejection(reject_reason)

        def _apply_pre_quant_scale(x_chunk: torch.Tensor) -> torch.Tensor:
            pqs = state["pre_quant_scale"]
            return x_chunk * pqs if pqs is not None else x_chunk

        output_side, weight_side = partition_output_branch_deltas(self.lora_deltas, self.out_features)

        def _prepare_dequant_once() -> tuple[torch.Tensor, "torch.Tensor | None", torch.dtype]:
            if state["dequant_weight"] is None:
                if self.lora_deltas:
                    _note_lora_path(self, _lora_dequant_path(weight_side, "chunked", output_side))
                weight, bias, dt = self._prepare_dequant_operand(dtype, device, deltas=weight_side)
                state["dequant_weight"] = weight
                state["dequant_bias"] = bias
                state["dequant_dt"] = dt
            return state["dequant_weight"], state["dequant_bias"], state["dequant_dt"]

        def _run_dequant(x_chunk: torch.Tensor) -> torch.Tensor:
            weight, bias, dt = _prepare_dequant_once()
            scaled = _apply_pre_quant_scale(x_chunk)
            if scaled.dtype is not dt:
                scaled = scaled.to(dt)
            out = F.linear(scaled, weight, bias)
            return _add_lora_output_branch(out, scaled, output_side, self.out_features, owner=self)

        def _run_fast(x_chunk: torch.Tensor) -> torch.Tensor:
            if state["downgraded"]:
                return _run_dequant(x_chunk)
            scaled = _apply_pre_quant_scale(x_chunk)
            out = self._forward_scaled_mm(scaled, weight=state["fast_weight"])
            if out is not None:
                return out
            state["fast_weight"] = None
            state["w_scale"] = None
            state["downgraded"] = True
            return _run_dequant(x_chunk)

        try:
            yield _PreparedLinearOperand(_run_fast if use_fast else _run_dequant)
        finally:
            state.clear()

    def _stage_scaled_mm_weight(self, device: "torch.device | str") -> "torch.Tensor | None":
        weight = self.weight
        if weight.is_cuda:
            return weight
        try:
            return cast_to(weight, weight.dtype, device, non_blocking=self.stream_non_blocking)
        except (RuntimeError, torch.cuda.OutOfMemoryError) as exc:
            if is_driver_oom(exc) and not retry_after_cuda_oom(exc):
                raise
            logger.debug(
                "fp8 matmul: on-demand weight staging failed; using dequant path", exc_info=True,
            )
            return None

    def _forward_scaled_mm(
        self, input: torch.Tensor, *, weight: "torch.Tensor | None" = None,
    ) -> torch.Tensor | None:
        device = input.device
        w_scale = self._cached_scaled_mm_scale("weight", self.weight_scale, device)
        if w_scale is None:
            return None

        if weight is None:
            weight = self._stage_scaled_mm_weight(device)
            if weight is None:
                return None

        orig_shape = input.shape
        x2d = input.reshape(-1, orig_shape[-1]).contiguous()
        static_scale = None
        if self.input_scale is not None:
            static_scale = self._cached_scaled_mm_scale("input", self.input_scale, device)
            if static_scale is None:
                return None
        weight = weight if weight.is_contiguous() else weight.contiguous()
        bias = self.bias.to(device=device, dtype=input.dtype) if self.bias is not None else None

        def run_rows(rows: torch.Tensor, scale: "torch.Tensor | None") -> torch.Tensor:
            try:
                x_scale = scale if scale is not None else static_scale
                if x_scale is None:
                    x_scale = fp8_activation_scale(rows)
                x_fp8 = _quantize_fp8(rows, x_scale)
            except RuntimeError as exc:
                if not retry_after_cuda_oom(exc):
                    raise
                raise ChunkedGemmFailed(f"activation quant failed ({type(exc).__name__})", exc) from exc
            try:
                return torch._scaled_mm(
                    x_fp8, weight.t(), scale_a=x_scale, scale_b=w_scale, out_dtype=input.dtype, bias=bias,
                )
            except (RuntimeError, torch.cuda.OutOfMemoryError) as exc:
                if is_driver_oom(exc) and not retry_after_cuda_oom(exc):
                    raise
                raise ChunkedGemmFailed(f"scaled_mm rejected operands ({type(exc).__name__})", exc) from exc

        try:
            out = gemm_in_row_chunks(
                x2d, self.out_features, run_rows,
                (lambda _rows: static_scale) if static_scale is not None else fp8_activation_scale,
            )
        except ChunkedGemmFailed as failed:
            logger.debug("fp8 matmul: %s; using dequant path", failed.reason, exc_info=failed.cause)
            if is_cuda_oom(failed.cause):
                _log_scaled_mm_fast_path_rejection(failed.reason)
            return None
        if self.lora_deltas:
            if _lora_fused_enabled():
                _note_lora_path(self, _with_lokr("fused-fast", self.lora_deltas))
                out = _add_lora_output_branch(out, x2d, self.lora_deltas, self.out_features, owner=self)
            else:
                _note_lora_path(self, "unfused-fast")
                out = out + _lora_output_branch(x2d, self.lora_deltas, out.dtype, self.out_features)
        return out.reshape(*orig_shape[:-1], -1)
