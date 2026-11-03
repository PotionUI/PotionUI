from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import torch
import torch.nn as nn
from torch import Tensor

from vendor.gpl.comfyui.flux.layers import timestep_embedding

from ...base import NativeArchModule
from ...cancellation import raise_if_cancelled
from ...io.quant_formats import FP8_DTYPES
from .config import Flux3Config
from .layers import Block, LastLayer, MLPEmbedder, Modulation, ModeModulation, rope_tables

IMAGE_ROPE_SCALE = 0.5
_SCALE_SUFFIX = ".weight_scale"
_DROPPED_SUFFIX = ".comfy_attention.config"


def convert_flux3_state_dict(sd: dict[str, Tensor]) -> dict[str, Tensor]:
    out = {key: value for key, value in sd.items() if not key.endswith(_DROPPED_SUFFIX)}
    for key, scale in out.items():
        if not key.endswith(_SCALE_SUFFIX) or scale.ndim != 1:
            continue
        weight = out.get(key[: -len("_scale")])
        if weight is not None and weight.dtype in FP8_DTYPES and weight.shape[0] == scale.shape[0]:
            out[key] = scale.reshape(-1, 1)
    return out


class Flux3(NativeArchModule):
    def __init__(self, cfg: Flux3Config, operations, dtype=None, device=None) -> None:
        super().__init__()
        self.cfg = cfg
        self.patch_size = 1
        self.params = SimpleNamespace(in_channels=cfg.in_channels)
        hidden = cfg.hidden
        kw = {"dtype": dtype, "device": device}

        image_modes = ("image", "image_cond") if cfg.has_image_cond else ("image",)
        self.emb_in = nn.ModuleDict({
            m: operations.Linear(cfg.in_channels, hidden, bias=False, **kw) for m in image_modes
        })
        self.txt_in = operations.Linear(cfg.context_in_dim, hidden, bias=False, **kw)
        self.time_in = MLPEmbedder(256, hidden, operations, **kw)
        if cfg.flow_map:
            self.ref_time_in = MLPEmbedder(256, hidden, operations, **kw)
        mod_keys = (*image_modes, "txt")
        self.early_stream_modulations = nn.ModuleDict({k: ModeModulation(hidden, operations, **kw) for k in mod_keys})
        self.single_stream_modulations = nn.ModuleDict({k: ModeModulation(hidden, operations, **kw) for k in mod_keys})

        def blocks(n: int) -> nn.ModuleList:
            return nn.ModuleList(
                [Block(hidden, cfg.heads, cfg.mlp_hidden, operations, fused_qkv=cfg.fused_qkv, **kw) for _ in range(n)]
            )

        self.content_mode_blocks = nn.ModuleDict({m: blocks(cfg.depth) for m in image_modes})
        self.txt_mode_blocks = blocks(cfg.depth)
        self.single_blocks = blocks(cfg.depth_single)
        self.final_layer = nn.ModuleDict({"image": LastLayer(hidden, cfg.in_channels, operations, **kw)})

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "Flux3":
        return cls(Flux3Config.from_detect_config(config), operations=operations)

    def post_load(self) -> None:
        return None

    @staticmethod
    def _grid_ids(h: int, w: int, layer: float, device) -> Tensor:
        rows = torch.arange(h, dtype=torch.float32, device=device)[:, None].expand(h, w)
        cols = torch.arange(w, dtype=torch.float32, device=device)[None, :].expand(h, w)
        zeros = torch.zeros_like(rows)
        ids = torch.stack((zeros, rows * IMAGE_ROPE_SCALE, cols * IMAGE_ROPE_SCALE, zeros + layer), dim=-1)
        return ids.reshape(h * w, 4)

    @staticmethod
    def _ref_list(ref_latents: Tensor | list[Tensor] | tuple[Tensor, ...] | None) -> list[Tensor]:
        if ref_latents is None:
            return []
        if isinstance(ref_latents, (list, tuple)):
            return list(ref_latents)
        return [ref_latents]

    def forward(
        self,
        x: Tensor,
        timestep: Tensor,
        context: Tensor,
        y: Tensor | None = None,
        guidance: Tensor | None = None,
        attention_mask: Tensor | None = None,
        ref_latents: Tensor | list[Tensor] | None = None,
        flow_map_target: Tensor | None = None,
        step_cache: Any = None,
        **_: Any,
    ) -> Tensor:
        cfg = self.cfg
        if (flow_map_target is not None) != cfg.flow_map:
            raise ValueError("flow_map_target is required by, and only accepted from, a flow-map FLUX 3 checkpoint")
        bsz, _, h, w = x.shape
        device, dtype = x.device, x.dtype

        img = self.emb_in["image"](x.flatten(2).transpose(1, 2))
        n_img = img.shape[1]
        img_ids = self._grid_ids(h, w, 0.0, device)

        refs = self._ref_list(ref_latents)
        if refs and not cfg.has_image_cond:
            raise ValueError("this FLUX 3 checkpoint has no reference-image weights")
        cond = None
        cond_ids = None
        if refs:
            cond = torch.cat(
                [r.to(device=device, dtype=dtype).flatten(2).transpose(1, 2).expand(bsz, -1, -1) for r in refs], dim=1
            )
            cond = self.emb_in["image_cond"](cond)
            cond_ids = torch.cat(
                [self._grid_ids(r.shape[-2], r.shape[-1], -(i + 1.0), device) for i, r in enumerate(refs)]
            )

        context = context.to(device=device, dtype=dtype)
        txt = self.txt_in(context)
        n_txt = txt.shape[1]
        txt_ids = torch.zeros(n_txt, 4, dtype=torch.float32, device=device)
        txt_ids[:, 3] = torch.arange(n_txt, dtype=torch.float32, device=device)

        t = timestep.reshape(-1).to(dtype)
        if t.shape[0] != bsz:
            t = t.expand(bsz)
        vec_img = self.time_in(timestep_embedding(t, 256))
        vec_zero = self.time_in(timestep_embedding(torch.zeros(1, dtype=dtype, device=device), 256))
        vec_cond = vec_txt = vec_zero
        if flow_map_target is not None:
            target = flow_map_target.reshape(-1).to(dtype)
            if target.shape[0] != bsz:
                target = target.expand(bsz)
            jump = self.ref_time_in(timestep_embedding(target, 256))
            vec_img = vec_img + jump
            vec_cond = vec_cond + jump
            vec_txt = vec_txt + jump

        early_img = self.early_stream_modulations["image"](vec_img)
        early_txt = self.early_stream_modulations["txt"](vec_txt)
        single_img = self.single_stream_modulations["image"](vec_img)
        single_txt = self.single_stream_modulations["txt"](vec_txt)

        img_rope = rope_tables(img_ids, cfg.axes_dim, cfg.theta)
        txt_rope = rope_tables(txt_ids, cfg.axes_dim, cfg.theta)
        cond_rope = rope_tables(cond_ids, cfg.axes_dim, cfg.theta) if refs else None
        if refs:
            early_cond = self.early_stream_modulations["image_cond"](vec_cond)
            single_cond = self.single_stream_modulations["image_cond"](vec_cond)

        seg_img: list[Modulation] = [(n_img, *early_img)]
        seg_txt: list[Modulation] = [(n_txt, *early_txt)]
        for d in range(cfg.depth):
            raise_if_cancelled(d)
            img = self.content_mode_blocks["image"][d](img, img_rope, seg_img)
            if refs:
                cond = self.content_mode_blocks["image_cond"][d](cond, cond_rope, [(cond.shape[1], *early_cond)])
            txt = self.txt_mode_blocks[d](txt, txt_rope, seg_txt)

        parts = [img]
        id_parts = [img_ids]
        segments: list[Modulation] = [(n_img, *single_img)]
        if refs:
            parts.append(cond)
            id_parts.append(cond_ids)
            segments.append((cond.shape[1], *single_cond))
        parts.append(txt)
        id_parts.append(txt_ids)
        segments.append((n_txt, *single_txt))

        stream = torch.cat(parts, dim=1)
        rope = rope_tables(torch.cat(id_parts), cfg.axes_dim, cfg.theta)
        for i, block in enumerate(self.single_blocks):
            raise_if_cancelled(cfg.depth + i)
            stream = block(stream, rope, segments)

        out = self.final_layer["image"](stream[:, :n_img], vec_img)
        return out.transpose(1, 2).reshape(bsz, cfg.in_channels, h, w)
