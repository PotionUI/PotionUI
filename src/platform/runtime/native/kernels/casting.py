# Derived from: comfy/ops.py (ComfyUI, GPL-3.0); see vendor/NOTICE.md
from __future__ import annotations

from contextlib import contextmanager

import torch
import torch.nn.functional as F

from ..io.quant_formats import (
    COMFY_QUANT_INT8_TENSORWISE,
    FP8_DTYPES,
    build_convrot_hadamard,
    convrot_unrotate_weight,
    extract_convrot_config,
    parse_comfy_quant,
)
from . import stream_wait as _stream_wait
from .fused_elementwise import ActGate, act_gate, apply_rope, gated_residual, norm_args, norm_modulate, qk_norm_rope
from .lora_math import _note_lora_path, linear_with_lora_deltas


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
    if not tensor.is_cuda and _stream_wait.recorder is not None:
        return _stream_wait.recorder.copy(tensor, dtype, device, non_blocking)
    return tensor.to(device=device, dtype=dtype, non_blocking=non_blocking)


def cast_bias_weight(
    module: "CastWeightBiasOp",
    input: torch.Tensor | None = None,
    dtype: torch.dtype | None = None,
    device: torch.device | None = None,
) -> tuple[torch.Tensor, torch.Tensor | None]:
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
    comfy_cast_weights = False
    weight_function: list = []
    bias_function: list = []
    stream_non_blocking = False
    lora_deltas: list | None = None

    @contextmanager
    def prepared_linear(self, sample: torch.Tensor):
        yield self


class disable_weight_init:
    norm_modulate = staticmethod(norm_modulate)
    gated_residual = staticmethod(gated_residual)
    act_gate = staticmethod(act_gate)
    norm_args = staticmethod(norm_args)
    apply_rope = staticmethod(apply_rope)
    qk_norm_rope = staticmethod(qk_norm_rope)
    ActGate = ActGate

    class Linear(torch.nn.Linear, CastWeightBiasOp):
        def reset_parameters(self) -> None:
            return None

        def forward_comfy_cast_weights(self, input: torch.Tensor) -> torch.Tensor:
            weight, bias = cast_bias_weight(self, input)
            if self.lora_deltas:
                _note_lora_path(self, "dequant (cast)")
            return linear_with_lora_deltas(input, weight, bias, self.lora_deltas, self.out_features, owner=self)

        def forward(self, *args, **kwargs):
            if self.comfy_cast_weights or self.lora_deltas:
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
            layer_conf = parse_comfy_quant(quant_blob)
            if layer_conf is not None:
                fmt = layer_conf.get("format")
                if fmt != COMFY_QUANT_INT8_TENSORWISE:
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
                convrot, groupsize = extract_convrot_config(layer_conf, prefix.rstrip("."))
                if convrot:
                    self._int8_convrot_groupsize = groupsize
                    self._int8_convrot_hadamard = build_convrot_hadamard(groupsize, device="cpu", dtype=torch.float32).clone()
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
                    flat = convrot_unrotate_weight(flat, hadamard, self._int8_convrot_groupsize)
                    x = flat.reshape(orig_shape)
                return x.to(target)
            target = out_dtype
            if target is None:
                target = self.weight.dtype if self.weight.dtype not in FP8_DTYPES else torch.bfloat16
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
