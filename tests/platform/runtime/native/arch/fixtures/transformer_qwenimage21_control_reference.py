# Derived from: VideoX-Fun videox_fun/models/qwenimage21_transformer2d_control.py (Apache-2.0, Copyright (c) Alibaba, Inc. and its affiliates) https://github.com/aigc-apps/VideoX-Fun; local modifications: imports the base classes from the diffusers reference fixture, sequence parallel pinned to one rank.
import math
from typing import Any, Dict, List, Optional, Tuple, Union

import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.utils.checkpoint
from diffusers.configuration_utils import register_to_config
from diffusers.models.modeling_outputs import Transformer2DModelOutput
from diffusers.utils import logging

from diffusers.models.transformers.transformer_qwenimage21 import (QwenImage21KVCache,
                                                                  QwenImage21Transformer2DModel,
                                                                  QwenImage21TransformerBlock,
                                                                  _IMG_TOKENS_PER_SLOT,
                                                                  _qwenimage21_prefix_segments)


def sequence_parallel_all_gather(tensor, dim):
    return tensor

logger = logging.get_logger(__name__)


class QwenImage21ControlTransformerBlock(QwenImage21TransformerBlock):

    def __init__(
        self,
        dim: int,
        num_attention_heads: int,
        attention_head_dim: int,
        mlp_ratio: int = 3,
        eps: float = 1e-6,
        block_id: int = 0,
    ):
        super().__init__(dim, num_attention_heads, attention_head_dim, mlp_ratio, eps)
        self.block_id = block_id
        if block_id == 0:
            self.before_proj = nn.Linear(dim, dim)
            nn.init.zeros_(self.before_proj.weight)
            nn.init.zeros_(self.before_proj.bias)
        self.after_proj = nn.Linear(dim, dim)
        nn.init.zeros_(self.after_proj.weight)
        nn.init.zeros_(self.after_proj.bias)

    def forward(self, c, x, **kwargs):
        if self.block_id == 0:
            c = self.before_proj(c) + x
            all_c = []
        else:
            all_c = list(torch.unbind(c))
            c = all_c.pop(-1)

        c = super().forward(c, **kwargs)
        c_skip = self.after_proj(c)
        all_c += [c_skip, c]
        c = torch.stack(all_c)
        return c


class BaseQwenImage21TransformerBlock(QwenImage21TransformerBlock):

    def __init__(
        self,
        dim: int,
        num_attention_heads: int,
        attention_head_dim: int,
        mlp_ratio: int = 3,
        eps: float = 1e-6,
        block_id: Optional[int] = None,
    ):
        super().__init__(dim, num_attention_heads, attention_head_dim, mlp_ratio, eps)
        self.block_id = block_id

    def forward(self, hidden_states, hints=None, context_scale: float = 1.0, **kwargs):
        hidden_states = super().forward(hidden_states, **kwargs)
        if self.block_id is not None and hints is not None:
            hidden_states = hidden_states + hints[self.block_id] * context_scale
        return hidden_states


class QwenImage21ControlTransformer2DModel(QwenImage21Transformer2DModel):

    _supports_gradient_checkpointing = True
    _no_split_modules = ["BaseQwenImage21TransformerBlock", "QwenImage21ControlTransformerBlock"]
    _skip_layerwise_casting_patterns = ["pos_embed", "norm"]
    _repeated_blocks = ["BaseQwenImage21TransformerBlock", "QwenImage21ControlTransformerBlock"]
    sp_world_size = 1
    sp_world_rank = 0

    @register_to_config
    def __init__(
        self,
        control_layers=None,
        control_in_dim=None,
        patch_size: int = 1,
        in_channels: int = 64,
        out_channels: Optional[int] = 64,
        num_layers: int = 32,
        attention_head_dim: int = 128,
        num_attention_heads: int = 32,
        context_in_dim: int = 4096,
        mlp_ratio: int = 3,
        axes_dims_rope: Tuple[int, int, int] = (16, 56, 56),
        eps: float = 1e-6,
        causal_condition: bool = True,
    ):
        super().__init__(
            patch_size, in_channels, out_channels, num_layers, attention_head_dim, num_attention_heads,
            context_in_dim, mlp_ratio, axes_dims_rope, eps, causal_condition,
        )

        self.control_layers = [i for i in range(0, num_layers, 2)] if control_layers is None else list(control_layers)
        self.control_in_dim = in_channels if control_in_dim is None else control_in_dim

        assert 0 in self.control_layers, "control_layers must contain 0 (the first control block merges the conditioning)."
        self.control_layers_mapping = {i: n for n, i in enumerate(self.control_layers)}

        self.transformer_blocks = nn.ModuleList(
            [
                BaseQwenImage21TransformerBlock(
                    dim=self.inner_dim,
                    num_attention_heads=num_attention_heads,
                    attention_head_dim=attention_head_dim,
                    mlp_ratio=mlp_ratio,
                    eps=eps,
                    block_id=self.control_layers_mapping[i] if i in self.control_layers else None,
                )
                for i in range(num_layers)
            ]
        )

        self.control_blocks = nn.ModuleList(
            [
                QwenImage21ControlTransformerBlock(
                    dim=self.inner_dim,
                    num_attention_heads=num_attention_heads,
                    attention_head_dim=attention_head_dim,
                    mlp_ratio=mlp_ratio,
                    eps=eps,
                    block_id=i,
                )
                for i in self.control_layers
            ]
        )

        self.control_img_in = nn.Linear(self.control_in_dim, self.inner_dim)

    @classmethod
    def from_pretrained(
        cls, pretrained_model_path, subfolder=None, transformer_additional_kwargs=None,
        low_cpu_mem_usage=False, torch_dtype=torch.bfloat16,
    ):
        model = super().from_pretrained(
            pretrained_model_path, subfolder=subfolder, transformer_additional_kwargs=transformer_additional_kwargs,
            low_cpu_mem_usage=low_cpu_mem_usage, torch_dtype=torch_dtype,
        )
        with torch.no_grad():
            for block in model.control_blocks:
                if hasattr(block, "before_proj"):
                    nn.init.zeros_(block.before_proj.weight)
                    nn.init.zeros_(block.before_proj.bias)
                nn.init.zeros_(block.after_proj.weight)
                nn.init.zeros_(block.after_proj.bias)
            model.control_img_in.reset_parameters()
        return model

    def forward_control(self, control_joint, x_joint, kwargs):
        c = control_joint
        for block in self.control_blocks:
            if torch.is_grad_enabled() and self.gradient_checkpointing:
                def create_custom_forward(module, **static_kwargs):
                    def custom_forward(*inputs):
                        return module(*inputs, **static_kwargs)

                    return custom_forward

                c = torch.utils.checkpoint.checkpoint(
                    create_custom_forward(block, x=x_joint, **kwargs),
                    c,
                    use_reentrant=False,
                )
            else:
                c = block(c, x_joint, **kwargs)

        hints = torch.unbind(c)[:-1]
        return hints

    def forward(
        self,
        hidden_states: torch.Tensor,
        encoder_hidden_states: torch.Tensor,
        timestep: torch.Tensor,
        img_shapes: List[List[Tuple[int, int, int]]],
        img_mask: torch.Tensor,
        encoder_hidden_states_mask: Optional[torch.Tensor] = None,
        attention_kwargs: Optional[Dict[str, Any]] = None,
        kv_cache: Optional[QwenImage21KVCache] = None,
        kv_cache_mode: Optional[str] = None,
        control_context: Optional[torch.Tensor] = None,
        control_context_scale: float = 1.0,
        return_dict: bool = True,
    ) -> Union[torch.Tensor, Transformer2DModelOutput]:
        batch_size = hidden_states.shape[0]
        if kv_cache is not None and not self.config.causal_condition:
            raise ValueError(
                "kv_cache requires `causal_condition=True`. The cache is only valid because text and condition-image "
                "tokens modulate from t=0, which makes their activations independent of the denoising step."
            )
        if kv_cache is not None and kv_cache_mode not in ("extract", "cached"):
            raise ValueError(
                f"kv_cache_mode must be 'extract' or 'cached' when kv_cache is provided, got {kv_cache_mode!r}."
            )
        if kv_cache is None and kv_cache_mode is not None:
            raise ValueError(f"kv_cache_mode is {kv_cache_mode!r} but no kv_cache was passed to hold the prefix.")

        hidden_states = self.img_in(hidden_states)
        encoder_hidden_states = self.txt_in(encoder_hidden_states)

        repeats = torch.where(img_mask, _IMG_TOKENS_PER_SLOT, 1)[0]
        image_pad_mask = torch.repeat_interleave(img_mask[0], repeats)

        target_tokens = math.prod(img_shapes[0][-1])
        joint_hidden_states = torch.cat(
            [
                encoder_hidden_states,
                encoder_hidden_states.new_zeros(batch_size, target_tokens // 4, encoder_hidden_states.shape[2]),
            ],
            dim=1,
        )
        joint_hidden_states = joint_hidden_states.repeat_interleave(repeats, dim=1)
        joint_hidden_states[:, image_pad_mask] = hidden_states

        control_joint = None
        if control_context is not None:
            control_features = self.control_img_in(control_context)
            control_joint = torch.zeros_like(joint_hidden_states)
            control_joint[:, image_pad_mask] = control_features

        rotary_emb = self.pos_embed(img_shapes[0], image_pad_mask, device=hidden_states.device)
        image_ids, target_token_mask = self.build_token_metadata(image_pad_mask, img_shapes[0])

        timestep = timestep.to(hidden_states.dtype)
        if self.config.causal_condition:
            timestep = torch.cat([timestep, timestep.new_zeros(1)], dim=0)
            modulation_mask = target_token_mask
        else:
            modulation_mask = None
        temb = self.time_text_embed(timestep, hidden_states)
        modulation = self.modulation(temb)

        joint_key_valid = None
        if encoder_hidden_states_mask is not None:
            joint_key_valid = torch.ones(
                batch_size, image_pad_mask.shape[0], dtype=torch.bool, device=hidden_states.device
            )
            text_positions = (~image_pad_mask).nonzero(as_tuple=True)[0]
            vlm_text_positions = ~img_mask[0][: encoder_hidden_states_mask.shape[1]]
            joint_key_valid[:, text_positions] = encoder_hidden_states_mask.bool()[:, vlm_text_positions]

        prefix_len = int((~target_token_mask).sum())

        if kv_cache_mode == "cached":
            joint_hidden_states = joint_hidden_states[:, prefix_len:]
            if control_joint is not None:
                control_joint = control_joint[:, prefix_len:]
            rotary_emb = rotary_emb[prefix_len:]
            modulation_mask = modulation_mask[prefix_len:]
            attention_mask = None if joint_key_valid is None else joint_key_valid[:, None, None, :]
            cache_write_slice = None
            block_segments, block_key_valid = None, None
        else:
            attention_mask = None
            block_segments = _qwenimage21_prefix_segments(image_ids, prefix_len)
            cache_write_slice = slice(0, prefix_len) if kv_cache_mode == "extract" else None
            block_key_valid = joint_key_valid

        sp_size = self.sp_world_size
        sp_pad_len = 0
        sp_active_len = joint_hidden_states.shape[1]
        if sp_size > 1:
            sp_padded_len = math.ceil(sp_active_len / sp_size) * sp_size
            sp_pad_len = sp_padded_len - sp_active_len
            if sp_pad_len > 0:
                joint_hidden_states = F.pad(joint_hidden_states, (0, 0, 0, sp_pad_len))
                if control_joint is not None:
                    control_joint = F.pad(control_joint, (0, 0, 0, sp_pad_len))
                rotary_emb = torch.cat(
                    [rotary_emb, rotary_emb.new_zeros(sp_pad_len, rotary_emb.shape[-1])], dim=0
                )
                if modulation_mask is not None:
                    modulation_mask = torch.cat(
                        [modulation_mask, modulation_mask.new_zeros(sp_pad_len, dtype=torch.bool)], dim=0
                    )
                if kv_cache_mode == "cached":
                    if joint_key_valid is None:
                        joint_key_valid = torch.ones(
                            batch_size, prefix_len + sp_active_len, dtype=torch.bool,
                            device=joint_hidden_states.device,
                        )
                    joint_key_valid = torch.cat(
                        [joint_key_valid, joint_key_valid.new_zeros(batch_size, sp_pad_len, dtype=torch.bool)],
                        dim=1,
                    )
                    attention_mask = joint_key_valid[:, None, None, :]
                else:
                    if block_key_valid is None:
                        block_key_valid = torch.ones(
                            batch_size, sp_active_len, dtype=torch.bool, device=joint_hidden_states.device
                        )
                    block_key_valid = torch.cat(
                        [block_key_valid, block_key_valid.new_zeros(batch_size, sp_pad_len, dtype=torch.bool)],
                        dim=1,
                    )
            sp_local = sp_padded_len // sp_size
            sp_lo = self.sp_world_rank * sp_local
            joint_hidden_states = joint_hidden_states[:, sp_lo:sp_lo + sp_local]
            if control_joint is not None:
                control_joint = control_joint[:, sp_lo:sp_lo + sp_local]
            rotary_emb = rotary_emb[sp_lo:sp_lo + sp_local]
            if modulation_mask is not None:
                modulation_mask = modulation_mask[sp_lo:sp_lo + sp_local]

        hints = None
        if control_joint is not None:
            control_kwargs = dict(
                modulation=modulation,
                rotary_emb=rotary_emb,
                attention_mask=attention_mask,
                target_token_mask=modulation_mask,
                layer_cache=None,
                kv_cache_mode=None,
                cache_write_slice=None,
                segments=block_segments,
                key_valid=block_key_valid,
            )
            hints = self.forward_control(control_joint, joint_hidden_states, control_kwargs)

        for index_block, block in enumerate(self.transformer_blocks):
            layer_cache = kv_cache.get_layer(index_block) if kv_cache is not None else None
            kwargs = dict(
                modulation=modulation,
                rotary_emb=rotary_emb,
                attention_mask=attention_mask,
                target_token_mask=modulation_mask,
                layer_cache=layer_cache,
                kv_cache_mode=kv_cache_mode,
                cache_write_slice=cache_write_slice,
                segments=block_segments,
                key_valid=block_key_valid,
                hints=hints,
                context_scale=control_context_scale,
            )
            if torch.is_grad_enabled() and self.gradient_checkpointing:
                def create_custom_forward(module, **static_kwargs):
                    def custom_forward(*inputs):
                        return module(*inputs, **static_kwargs)

                    return custom_forward

                joint_hidden_states = torch.utils.checkpoint.checkpoint(
                    create_custom_forward(block, **kwargs),
                    joint_hidden_states,
                    use_reentrant=False,
                )
            else:
                joint_hidden_states = block(joint_hidden_states, **kwargs)

        joint_hidden_states = self.norm_out(joint_hidden_states, temb, modulation_mask)
        output = self.proj_out(joint_hidden_states)
        if sp_size > 1:
            output = sequence_parallel_all_gather(output, dim=1)
            if sp_pad_len > 0:
                output = output[:, :sp_active_len]

        if not return_dict:
            return (output,)

        return Transformer2DModelOutput(sample=output)
