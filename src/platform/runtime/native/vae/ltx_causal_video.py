"""LTX-2/2.3 causal video VAE, vendored from ComfyUI's
``comfy/ldm/lightricks/vae/causal_video_autoencoder.py`` (+ ``causal_conv3d.py``,
``pixel_norm.py``, ``conv_nd_factory.py``).

**Config comes from the checkpoint itself, not structural sniffing.** Unlike
every other native-engine detector, LTX checkpoints embed a full JSON config
in the safetensors ``__metadata__["config"]`` header (verified against both
local standalone VAE files and the all-in-one DiT checkpoint -- all three
carry the exact same ``config["vae"]`` block). So detection here is "does the
metadata have a `config.vae` block with `_class_name == CausalVideoAutoencoder`",
not shape/key sniffing -- see ``detect_ltx_video_vae_config``.

**Chunking is automatic, not caller-managed.** Unlike the Wan causal VAEs
(``causal_3d.py``/``causal_3d_v2.py``), where the caller drives an explicit
frame-by-frame loop with a ``feat_cache`` list, LTX's ``CausalConv3d`` keeps
its own cache **keyed by thread id** as instance state, and ``Decoder.forward``
recursively self-chunks by a fixed memory budget (``MAX_CHUNK_SIZE`` = 128MB)
using that cache to stitch chunk boundaries seamlessly. The public
``encode``/``decode`` API is therefore a single call over the whole clip --
no manual chunk loop needed. The tradeoff: the cache is **stateful per
module instance** and must be cleared between independent encode/decode
calls (``mark_conv3d_ended`` + a ``finally``-block cache pop, both ported
verbatim) or a later call on the same thread would splice in stale history.

**Frame-count constraint**: `encode()` requires ``1 + 8*k`` frames (verified:
raises otherwise) -- a single image (``T=1``, ``k=0``) satisfies this
trivially, so no separate image-mode wrapper is needed the way Wan's causal
VAEs needed ``encode_image``/``decode_image``.

**Latent normalization lives inside the VAE class itself**, not left to a
registry `latent_format` the way Flux/Wan's per-channel constants are: the
checkpoint's own ``per_channel_statistics.*`` buffers (``mean-of-means``,
``std-of-means``, ...) are loaded weights, and ``encode``/``decode``
normalize/un-normalize through them automatically.

**Scope of this vendor pass**: the non-timestep-conditioned path (verified:
both local standalone checkpoints -- ``LTX2_video_vae_bf16.safetensors``,
``LTX23_video_vae_bf16.safetensors`` -- have ``timestep_conditioning: false``
in their embedded config) is fully implemented and real-file tested.
``timestep_conditioning: true`` configs (decoder noise/timestep conditioning,
via a vendored ``PixArtAlphaCombinedTimestepSizeEmbeddings``) are **not**
implemented -- `from_config` raises `NativeEngineUnsupportedError` naming it
explicitly, rather than silently building a module that would fail integrity
checks or produce wrong output. No local checkpoint needs it; add it when one
does. ``DualConv3d`` (the ``dims=(2,1)`` decomposed-conv path) is likewise not
vendored -- both local checkpoints declare ``dims: 3``, so it's dead code for
every file this engine has access to.
"""

from __future__ import annotations

import logging
import math
import threading
from typing import TYPE_CHECKING, Any

import torch
import torch.nn as nn
import torch.nn.functional as F

from ..base import NativeArchModule
from ..errors import NativeEngineUnsupportedError

if TYPE_CHECKING:
    from .ltx_tiling import LtxTilingConfig

logger = logging.getLogger(__name__)

_MAX_CHUNK_BYTES = 128 * 1024 ** 2


def _cat_nonempty(tensors: list[torch.Tensor | None], dim: int) -> torch.Tensor | None:
    """ComfyUI's ``torch_cat_if_needed``: filters None/empty tensors, then
    cats/passes-through/returns None. The None case is load-bearing here --
    ``_add_exchange_cache`` uses this for a side-effect-only cache value that
    the caller (``ResnetBlock3D.forward``) discards into ``temporal_cache_state``,
    not its actual return value, so an empty result must not raise."""
    xs = [t for t in tensors if t is not None and t.shape[dim] > 0]
    if len(xs) > 1:
        return torch.cat(xs, dim=dim)
    if len(xs) == 1:
        return xs[0]
    return None


class CausalConv3d(nn.Module):
    """Causal (left-padded) 3D conv with a thread-keyed streaming cache.

    Composes ``operations.Conv3d`` (see ``vae/causal_3d.py`` for why:
    the ops class is picked per-load, not known at class-definition time).
    Unlike ``causal_3d.py``'s composition wrapper, HERE the checkpoint's own
    keys genuinely nest under a ``.conv.`` submodule (verified: real keys are
    e.g. ``encoder.conv_in.conv.weight``) -- ComfyUI's own ``CausalConv3d``
    also wraps an inner ``self.conv``, so no rename trick is needed here.
    """

    def __init__(
        self, in_channels: int, out_channels: int, kernel_size: int = 3,
        *, stride: int | tuple[int, int, int] = 1, dilation: int = 1, groups: int = 1,
        spatial_padding_mode: str = "zeros", operations: Any,
    ) -> None:
        super().__init__()
        self.in_channels = in_channels
        self.out_channels = out_channels
        k = (kernel_size, kernel_size, kernel_size)
        self.time_kernel_size = k[0]
        dilation3 = (dilation, 1, 1)
        padding = (0, k[1] // 2, k[2] // 2)
        self.conv = operations.Conv3d(
            in_channels, out_channels, k, stride=stride, dilation=dilation3,
            padding=padding, padding_mode=spatial_padding_mode, groups=groups,
        )
        self.temporal_cache_state: dict[int, tuple[torch.Tensor | None, bool]] = {}

    def forward(self, x: torch.Tensor, causal: bool = True) -> torch.Tensor:
        tid = threading.get_ident()
        cached, is_end = self.temporal_cache_state.get(tid, (None, False))
        if cached is None:
            padding_length = self.time_kernel_size - 1
            if not causal:
                padding_length = padding_length // 2
            if x.shape[2] == 0:
                return x
            cached = x[:, :, :1, :, :].repeat((1, 1, padding_length, 1, 1))
        pieces = [cached, x]
        if is_end and not causal:
            pieces.append(x[:, :, -1:, :, :].repeat((1, 1, (self.time_kernel_size - 1) // 2, 1, 1)))

        needs_caching = not is_end
        if needs_caching and x.shape[2] >= self.time_kernel_size - 1:
            needs_caching = False
            self.temporal_cache_state[tid] = (x[:, :, -(self.time_kernel_size - 1):, :, :], False)

        x = torch.cat(pieces, dim=2)
        if needs_caching:
            self.temporal_cache_state[tid] = (x[:, :, -(self.time_kernel_size - 1):, :, :], False)

        return self.conv(x) if x.shape[2] >= self.time_kernel_size else x[:, :, :0, :, :]

    @property
    def weight(self) -> torch.Tensor:
        return self.conv.weight


def _mark_conv3d_ended(module: nn.Module) -> None:
    tid = threading.get_ident()
    for m in module.modules():
        if isinstance(m, CausalConv3d):
            current = m.temporal_cache_state.get(tid, (None, False))
            m.temporal_cache_state[tid] = (current[0], True)


def _clear_thread_cache(module: nn.Module) -> None:
    tid = threading.get_ident()
    for m in module.modules():
        if hasattr(m, "temporal_cache_state"):
            m.temporal_cache_state.pop(tid, None)


def _split2(t: torch.Tensor, split_point: int, dim: int = 2):
    return torch.split(t, [split_point, t.shape[dim] - split_point], dim=dim)


def _add_exchange_cache(dest, cache_in, new_input, dim: int = 2):
    if dest is not None:
        if cache_in is not None:
            cache_to_dest = min(dest.shape[dim], cache_in.shape[dim])
            lead_in_dest, dest = _split2(dest, cache_to_dest, dim=dim)
            lead_in_source, cache_in = _split2(cache_in, cache_to_dest, dim=dim)
            lead_in_dest.add_(lead_in_source)
        body, new_input = _split2(new_input, dest.shape[dim], dim)
        dest.add_(body)
    return _cat_nonempty([cache_in, new_input], dim=dim)


class PixelNorm(nn.Module):
    def __init__(self, dim: int = 1, eps: float = 1e-8) -> None:
        super().__init__()
        self.dim = dim
        self.eps = eps

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x / torch.sqrt(torch.mean(x ** 2, dim=self.dim, keepdim=True) + self.eps)


def _make_conv(in_ch: int, out_ch: int, kernel_size: int, *, stride=1, padding=0,
                causal: bool = True, spatial_padding_mode: str = "zeros", operations: Any) -> nn.Module:
    if causal:
        return CausalConv3d(
            in_ch, out_ch, kernel_size, stride=stride,
            spatial_padding_mode=spatial_padding_mode, operations=operations,
        )
    return operations.Conv3d(
        in_ch, out_ch, kernel_size, stride=stride, padding=padding, padding_mode=spatial_padding_mode,
    )


def _make_linear(in_ch: int, out_ch: int, *, operations: Any) -> nn.Module:
    return operations.Conv3d(in_ch, out_ch, kernel_size=1, bias=True)


class ResnetBlock3D(nn.Module):
    def __init__(
        self, in_channels: int, out_channels: int | None = None, *, eps: float = 1e-6,
        norm_layer: str = "group_norm", groups: int = 32, spatial_padding_mode: str = "zeros",
        operations: Any,
    ) -> None:
        super().__init__()
        out_channels = in_channels if out_channels is None else out_channels
        self.in_channels = in_channels
        self.out_channels = out_channels

        self.norm1 = PixelNorm() if norm_layer == "pixel_norm" else nn.GroupNorm(groups, in_channels, eps=eps, affine=True)
        self.non_linearity = nn.SiLU()
        self.conv1 = _make_conv(in_channels, out_channels, 3, padding=1, causal=True,
                                 spatial_padding_mode=spatial_padding_mode, operations=operations)
        self.norm2 = PixelNorm() if norm_layer == "pixel_norm" else nn.GroupNorm(groups, out_channels, eps=eps, affine=True)
        self.dropout = nn.Dropout(0.0)
        self.conv2 = _make_conv(out_channels, out_channels, 3, padding=1, causal=True,
                                 spatial_padding_mode=spatial_padding_mode, operations=operations)
        self.conv_shortcut = (
            _make_linear(in_channels, out_channels, operations=operations)
            if in_channels != out_channels else nn.Identity()
        )
        # LayerNorm applied over the channel axis when the shortcut needs projecting --
        # matches ComfyUI's ResnetBlock3D.norm3 (always LayerNorm, never Pixel/GroupNorm).
        self.norm3 = (
            nn.LayerNorm(in_channels, eps=eps, elementwise_affine=True)
            if in_channels != out_channels else nn.Identity()
        )
        self.temporal_cache_state: dict[int, torch.Tensor | None] = {}

    def _norm3(self, x: torch.Tensor) -> torch.Tensor:
        if isinstance(self.norm3, nn.Identity):
            return x
        x = x.permute(0, 2, 3, 4, 1)
        x = self.norm3(x)
        return x.permute(0, 4, 1, 2, 3)

    def forward(self, x: torch.Tensor, causal: bool = True) -> torch.Tensor:
        h = self.norm1(x)
        h = self.non_linearity(h)
        h = self.conv1(h, causal=causal)
        h = self.norm2(h)
        h = self.non_linearity(h)
        h = self.dropout(h)
        h = self.conv2(h, causal=causal)

        shortcut = self._norm3(x)
        shortcut = self.conv_shortcut(shortcut)

        tid = threading.get_ident()
        cached = self.temporal_cache_state.get(tid, None)
        cached = _add_exchange_cache(h, cached, shortcut, dim=2)
        self.temporal_cache_state[tid] = cached
        return h


class UNetMidBlock3D(nn.Module):
    def __init__(self, in_channels: int, num_layers: int, *, norm_layer: str = "group_norm",
                 resnet_groups: int = 32, spatial_padding_mode: str = "zeros", operations: Any) -> None:
        super().__init__()
        self.res_blocks = nn.ModuleList([
            ResnetBlock3D(in_channels, in_channels, norm_layer=norm_layer, groups=resnet_groups,
                          spatial_padding_mode=spatial_padding_mode, operations=operations)
            for _ in range(num_layers)
        ])

    def forward(self, x: torch.Tensor, causal: bool = True) -> torch.Tensor:
        for block in self.res_blocks:
            x = block(x, causal=causal)
        return x


class SpaceToDepthDownsample(nn.Module):
    def __init__(self, in_channels: int, out_channels: int, stride: tuple[int, int, int],
                 *, spatial_padding_mode: str = "zeros", operations: Any) -> None:
        super().__init__()
        self.stride = stride
        self.group_size = in_channels * math.prod(stride) // out_channels
        self.conv = _make_conv(in_channels, out_channels // math.prod(stride), 3, stride=1,
                                causal=True, spatial_padding_mode=spatial_padding_mode, operations=operations)

    def forward(self, x: torch.Tensor, causal: bool = True) -> torch.Tensor:
        p1, p2, p3 = self.stride
        if self.stride[0] == 2:
            x = torch.cat([x[:, :, :1, :, :], x], dim=2)

        b, c, d, h, w = x.shape
        x_in = x.view(b, c, d // p1, p1, h // p2, p2, w // p3, p3)
        x_in = x_in.permute(0, 1, 3, 5, 7, 2, 4, 6).reshape(b, c * p1 * p2 * p3, d // p1, h // p2, w // p3)
        x_in = x_in.view(b, x_in.shape[1] // self.group_size, self.group_size, *x_in.shape[2:])
        x_in = x_in.mean(dim=2)

        y = self.conv(x, causal=causal)
        b2, c2, d2, h2, w2 = y.shape
        y = y.view(b2, c2, d2 // p1, p1, h2 // p2, p2, w2 // p3, p3)
        y = y.permute(0, 1, 3, 5, 7, 2, 4, 6).reshape(b2, c2 * p1 * p2 * p3, d2 // p1, h2 // p2, w2 // p3)

        return y + x_in


class DepthToSpaceUpsample(nn.Module):
    def __init__(self, in_channels: int, stride: tuple[int, int, int], *, residual: bool = False,
                 out_channels_reduction_factor: int = 1, spatial_padding_mode: str = "zeros", operations: Any) -> None:
        super().__init__()
        self.stride = stride
        self.out_channels = math.prod(stride) * in_channels // out_channels_reduction_factor
        self.conv = _make_conv(in_channels, self.out_channels, 3, stride=1, causal=True,
                                spatial_padding_mode=spatial_padding_mode, operations=operations)
        self.residual = residual
        self.out_channels_reduction_factor = out_channels_reduction_factor
        self.temporal_cache_state: dict[int, tuple] = {}

    def _depth_to_space(self, y: torch.Tensor) -> torch.Tensor:
        p1, p2, p3 = self.stride
        b, c, d, h, w = y.shape
        cg = c // (p1 * p2 * p3)
        y = y.view(b, cg, p1, p2, p3, d, h, w)
        y = y.permute(0, 1, 5, 2, 6, 3, 7, 4).reshape(b, cg, d * p1, h * p2, w * p3)
        return y

    def forward(self, x: torch.Tensor, causal: bool = True) -> torch.Tensor:
        tid = threading.get_ident()
        cached, drop_first_conv, drop_first_res = self.temporal_cache_state.get(tid, (None, True, True))

        y = self.conv(x, causal=causal)
        y = self._depth_to_space(y)
        if self.stride[0] == 2 and y.shape[2] > 0 and drop_first_conv:
            y = y[:, :, 1:, :, :]
            drop_first_conv = False

        if self.residual:
            p1, p2, p3 = self.stride
            num_repeat = math.prod(self.stride) // self.out_channels_reduction_factor
            x_in = x.repeat(1, num_repeat, 1, 1, 1)
            x_in = self._depth_to_space_repeat(x_in)
            if self.stride[0] == 2 and x_in.shape[2] > 0 and drop_first_res:
                x_in = x_in[:, :, 1:, :, :]
                drop_first_res = False

            if y.shape[2] == 0:
                y = None

            cached = _add_exchange_cache(y, cached, x_in, dim=2)
            self.temporal_cache_state[tid] = (cached, drop_first_conv, drop_first_res)
        else:
            self.temporal_cache_state[tid] = (None, drop_first_conv, False)

        return y

    def _depth_to_space_repeat(self, x_in: torch.Tensor) -> torch.Tensor:
        p1, p2, p3 = self.stride
        b, c, d, h, w = x_in.shape
        cg = c // (p1 * p2 * p3)
        x_in = x_in.view(b, cg, p1, p2, p3, d, h, w)
        x_in = x_in.permute(0, 1, 5, 2, 6, 3, 7, 4).reshape(b, cg, d * p1, h * p2, w * p3)
        return x_in


def _patchify(x: torch.Tensor, patch_size_hw: int, patch_size_t: int = 1) -> torch.Tensor:
    if patch_size_hw == 1 and patch_size_t == 1:
        return x
    b, c, f, h, w = x.shape
    p, q, r = patch_size_t, patch_size_hw, patch_size_hw
    # Channel packing order: (c, p, r, q) -- p=temporal, r=width-patch,
    # q=height-patch.  Must match the ltx-core / diffusers convention so that
    # decode(encode(x)) round-trips correctly with Lightricks checkpoints.
    x = x.view(b, c, f // p, p, h // q, q, w // r, r)
    x = x.permute(0, 1, 3, 7, 5, 2, 4, 6).reshape(b, c * p * q * r, f // p, h // q, w // r)
    return x


def _unpatchify(x: torch.Tensor, patch_size_hw: int, patch_size_t: int = 1) -> torch.Tensor:
    if patch_size_hw == 1 and patch_size_t == 1:
        return x
    b, c_pqr, f, h, w = x.shape
    p, q, r = patch_size_t, patch_size_hw, patch_size_hw
    c = c_pqr // (p * q * r)
    # Channel unpacking order: (c, p, r, q) -- must match _patchify and the
    # ltx-core / diffusers convention.  See _patchify comment.
    x = x.view(b, c, p, r, q, f, h, w)
    x = x.permute(0, 1, 5, 2, 6, 4, 7, 3).reshape(b, c, f * p, h * q, w * r)
    return x


def _build_block(name: str, params: dict, in_channels: int, out_channels: int, *,
                  encoder: bool, norm_layer: str, spatial_padding_mode: str, operations: Any) -> nn.Module:
    if name == "res_x":
        return UNetMidBlock3D(in_channels, params["num_layers"], norm_layer=norm_layer,
                               spatial_padding_mode=spatial_padding_mode, operations=operations)
    if encoder:
        if name == "compress_time":
            return _make_conv(in_channels, out_channels, 3, stride=(2, 1, 1), causal=True,
                               spatial_padding_mode=spatial_padding_mode, operations=operations)
        if name == "compress_space":
            return _make_conv(in_channels, out_channels, 3, stride=(1, 2, 2), causal=True,
                               spatial_padding_mode=spatial_padding_mode, operations=operations)
        if name == "compress_all":
            return _make_conv(in_channels, out_channels, 3, stride=(2, 2, 2), causal=True,
                               spatial_padding_mode=spatial_padding_mode, operations=operations)
        if name in ("compress_all_res", "compress_space_res", "compress_time_res"):
            stride = {"compress_all_res": (2, 2, 2), "compress_space_res": (1, 2, 2), "compress_time_res": (2, 1, 1)}[name]
            return SpaceToDepthDownsample(in_channels, out_channels, stride,
                                           spatial_padding_mode=spatial_padding_mode, operations=operations)
    else:
        # NOTE: this local ComfyUI checkout's causal_video_autoencoder.py builds
        # compress_time/compress_space *without* a reduction factor (implying
        # channel count is preserved), but the real LTX23_video_vae_bf16.safetensors
        # checkpoint's shapes prove the deployed model DOES divide channels by
        # `multiplier` for these two block types too -- verified by reading the
        # real header's channel progression (decoder.up_blocks.5: 512->512 raw
        # conv width for a stride=(2,1,1)/multiplier=2 compress_time, which only
        # matches prod(stride)*in/reduction with reduction=multiplier=2, not the
        # checked-out source's implied default of 1). Treating compress_time/
        # compress_space like compress_all (reduction_factor=multiplier) is what
        # actually loads the real checkpoint with exact shape parity -- likely a
        # version drift between this checkout and whatever generated the file.
        if name == "compress_time":
            return DepthToSpaceUpsample(
                in_channels, (2, 1, 1), out_channels_reduction_factor=params.get("multiplier", 1),
                spatial_padding_mode=spatial_padding_mode, operations=operations,
            )
        if name == "compress_space":
            return DepthToSpaceUpsample(
                in_channels, (1, 2, 2), out_channels_reduction_factor=params.get("multiplier", 1),
                spatial_padding_mode=spatial_padding_mode, operations=operations,
            )
        if name == "compress_all":
            return DepthToSpaceUpsample(
                in_channels, (2, 2, 2), residual=params.get("residual", False),
                out_channels_reduction_factor=params.get("multiplier", 1),
                spatial_padding_mode=spatial_padding_mode, operations=operations,
            )
    raise NativeEngineUnsupportedError(f"LTX VAE: unknown block type {name!r} ({'encoder' if encoder else 'decoder'})")


class Encoder(nn.Module):
    def __init__(self, *, in_channels: int, latent_channels: int, blocks: list, base_channels: int,
                 patch_size: int, norm_layer: str, latent_log_var: str, spatial_padding_mode: str, operations: Any) -> None:
        super().__init__()
        self.patch_size = patch_size
        self.latent_log_var = latent_log_var
        patched_in = in_channels * patch_size ** 2
        out_ch = base_channels

        self.conv_in = _make_conv(patched_in, out_ch, 3, padding=1, causal=True,
                                   spatial_padding_mode=spatial_padding_mode, operations=operations)

        self.down_blocks = nn.ModuleList()
        for name, params in blocks:
            if isinstance(params, int):
                params = {"num_layers": params}
            in_ch = out_ch
            if name != "res_x":
                out_ch = params.get("multiplier", 2) * out_ch if "res" in name else out_ch
            self.down_blocks.append(_build_block(
                name, params, in_ch, out_ch, encoder=True, norm_layer=norm_layer,
                spatial_padding_mode=spatial_padding_mode, operations=operations,
            ))

        self.conv_norm_out = PixelNorm() if norm_layer == "pixel_norm" else nn.GroupNorm(32, out_ch, eps=1e-6, affine=True)
        self.conv_act = nn.SiLU()

        conv_out_channels = latent_channels
        if latent_log_var == "per_channel":
            conv_out_channels *= 2
        elif latent_log_var in ("uniform", "constant"):
            conv_out_channels += 1
        self.conv_out = _make_conv(out_ch, conv_out_channels, 3, padding=1, causal=True,
                                    spatial_padding_mode=spatial_padding_mode, operations=operations)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = _patchify(x, self.patch_size, 1)
        x = self.conv_in(x)
        for block in self.down_blocks:
            # Every down_blocks entry (UNetMidBlock3D, a plain CausalConv3d
            # compress_*, or SpaceToDepthDownsample) accepts causal= uniformly.
            x = block(x, causal=True)
        x = self.conv_norm_out(x)
        x = self.conv_act(x)
        x = self.conv_out(x)

        if self.latent_log_var == "uniform":
            last = x[:, -1:]
            repeated = last.repeat(1, x.shape[1] - 2, 1, 1, 1)
            x = torch.cat([x, repeated], dim=1)
        elif self.latent_log_var == "constant":
            x = x[:, :-1]
            x = torch.cat([x, torch.full_like(x, -30.0)], dim=1)
        return x


class Decoder(nn.Module):
    def __init__(self, *, latent_channels: int, out_channels: int, blocks: list, base_channels: int,
                 patch_size: int, norm_layer: str, causal: bool, spatial_padding_mode: str, operations: Any) -> None:
        super().__init__()
        self.patch_size = patch_size
        self.causal = causal
        patched_out = out_channels * patch_size ** 2

        # See _build_block's "compress_time"/"compress_space" note: all three
        # compress_* variants divide/multiply channels by `multiplier` in the
        # real checkpoints, so they're tracked identically here.
        _compress_names = ("compress_all", "compress_time", "compress_space")

        out_ch = base_channels
        for name, params in reversed(blocks):
            params = params if isinstance(params, dict) else {}
            if name in _compress_names:
                out_ch = out_ch * params.get("multiplier", 1)

        self.conv_in = _make_conv(latent_channels, out_ch, 3, padding=1, causal=True,
                                   spatial_padding_mode=spatial_padding_mode, operations=operations)

        self.up_blocks = nn.ModuleList()
        for name, params in reversed(blocks):
            if isinstance(params, int):
                params = {"num_layers": params}
            in_ch = out_ch
            if name in _compress_names:
                out_ch = out_ch // params.get("multiplier", 1)
            self.up_blocks.append(_build_block(
                name, params, in_ch, out_ch, encoder=False, norm_layer=norm_layer,
                spatial_padding_mode=spatial_padding_mode, operations=operations,
            ))

        self.conv_norm_out = PixelNorm() if norm_layer == "pixel_norm" else nn.GroupNorm(32, out_ch, eps=1e-6, affine=True)
        self.conv_act = nn.SiLU()
        self.conv_out = _make_conv(out_ch, patched_out, 3, padding=1, causal=True,
                                    spatial_padding_mode=spatial_padding_mode, operations=operations)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        _mark_conv3d_ended(self.conv_in)
        x = self.conv_in(x, causal=self.causal)

        output: list[torch.Tensor] = []

        def run_up(idx: int, sample: torch.Tensor, ended: bool) -> None:
            if idx >= len(self.up_blocks):
                sample = self.conv_norm_out(sample)
                sample = self.conv_act(sample)
                if ended:
                    _mark_conv3d_ended(self.conv_out)
                sample = self.conv_out(sample, causal=self.causal)
                if sample is not None and sample.shape[2] > 0:
                    output.append(sample)
                return

            up_block = self.up_blocks[idx]
            if ended:
                _mark_conv3d_ended(up_block)
            sample = up_block(sample, causal=self.causal)

            if sample is None or sample.shape[2] == 0:
                return

            total_bytes = sample.numel() * sample.element_size()
            num_chunks = (total_bytes + _MAX_CHUNK_BYTES - 1) // _MAX_CHUNK_BYTES
            chunks = torch.chunk(sample, chunks=num_chunks, dim=2)
            for i, chunk in enumerate(chunks):
                run_up(idx + 1, chunk, ended and i == len(chunks) - 1)

        run_up(0, x, True)
        out = torch.cat(output, dim=2)
        return _unpatchify(out, self.patch_size, 1)


class _PerChannelStatistics(nn.Module):
    """Checkpoint-provided per-channel latent normalization (dash-named
    buffers -- valid for ``register_buffer``, which only forbids dots)."""

    def __init__(self, channels: int = 128) -> None:
        super().__init__()
        self.register_buffer("std-of-means", torch.empty(channels))
        self.register_buffer("mean-of-means", torch.empty(channels))
        self.register_buffer("mean-of-stds", torch.empty(channels))
        self.register_buffer("mean-of-stds_over_std-of-means", torch.empty(channels))
        self.register_buffer("channel", torch.empty(channels))

    def normalize(self, x: torch.Tensor) -> torch.Tensor:
        mean = self.get_buffer("mean-of-means").view(1, -1, 1, 1, 1).to(x)
        std = self.get_buffer("std-of-means").view(1, -1, 1, 1, 1).to(x)
        return (x - mean) / std

    def un_normalize(self, x: torch.Tensor) -> torch.Tensor:
        mean = self.get_buffer("mean-of-means").view(1, -1, 1, 1, 1).to(x)
        std = self.get_buffer("std-of-means").view(1, -1, 1, 1, 1).to(x)
        return x * std + mean


class LTXCausalVideoVAE(NativeArchModule):
    """LTX-2/2.3 ``CausalVideoAutoencoder``. ``encode``/``decode`` operate on
    ``(B, 3, T, H, W)`` pixels in [-1, 1] / ``(B, latent_channels, T', H', W')``
    latents; ``T`` must be ``1 + 8*k`` (a still image, ``T=1``, is the ``k=0``
    case -- no separate image-mode API needed). Chunking across long clips is
    automatic (see module docstring); call :meth:`reset_cache` between
    independent encode/decode invocations on the same thread.
    """

    def __init__(self, *, config: dict[str, Any], operations: Any) -> None:
        super().__init__()
        if config.get("timestep_conditioning", False):
            raise NativeEngineUnsupportedError(
                "LTX VAE: timestep_conditioning=True checkpoints are not supported yet "
                "(no local checkpoint needs it -- see ltx_causal_video.py module docstring)."
            )
        latent_channels = config["latent_channels"]
        self.latent_channels = latent_channels
        norm_layer = config.get("norm_layer", "group_norm")
        patch_size = config.get("patch_size", 1)
        spatial_padding_mode = config.get("spatial_padding_mode", "zeros")

        self.encoder = Encoder(
            in_channels=config.get("in_channels", 3), latent_channels=latent_channels,
            blocks=config["encoder_blocks"], base_channels=config.get("encoder_base_channels", 128),
            patch_size=patch_size, norm_layer=norm_layer,
            latent_log_var=config.get("latent_log_var", "per_channel"),
            spatial_padding_mode=spatial_padding_mode, operations=operations,
        )
        self.decoder = Decoder(
            latent_channels=latent_channels, out_channels=config.get("out_channels", 3),
            blocks=config["decoder_blocks"], base_channels=config.get("decoder_base_channels", 128),
            patch_size=patch_size, norm_layer=norm_layer, causal=config.get("causal_decoder", False),
            spatial_padding_mode=spatial_padding_mode, operations=operations,
        )
        self.per_channel_statistics = _PerChannelStatistics(latent_channels)

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "LTXCausalVideoVAE":
        return cls(config=config, operations=operations)

    def post_load(self) -> None:
        # No computed buffers: per_channel_statistics are loaded weights (not
        # derived), and the CausalConv3d temporal cache is per-forward-call
        # transient state, not a persisted buffer.
        return None

    def reset_cache(self) -> None:
        """Clear the thread-local streaming cache. Call between independent
        encode/decode invocations on the same thread (mirrors ComfyUI's
        ``finally``-block cleanup after each top-level forward)."""
        _clear_thread_cache(self)

    def encode(self, pixels: torch.Tensor) -> torch.Tensor:
        """``pixels``: (B, 3, T, H, W) in [-1, 1], T = 1 + 8*k. Returns the
        normalized latent (B, latent_channels, T', H', W')."""
        t = pixels.shape[2]
        if (t - 1) % 8 != 0:
            raise ValueError(
                f"LTX VAE encode: T={t} invalid -- must be 1 + 8*k (e.g. 1, 9, 17, ...)."
            )
        try:
            means, _logvar = torch.chunk(self.encoder(pixels), 2, dim=1)
            return self.per_channel_statistics.normalize(means)
        finally:
            self.reset_cache()

    def decode(self, latent: torch.Tensor) -> torch.Tensor:
        """``latent``: (B, latent_channels, T, H, W). Returns pixels (B, 3, T', H', W') in [-1, 1]."""
        try:
            x = self.per_channel_statistics.un_normalize(latent)
            return self.decoder(x)
        finally:
            self.reset_cache()

    def tiled_encode(self, pixels: torch.Tensor, tiling_config: "LtxTilingConfig | None" = None) -> torch.Tensor:
        """Tiled twin of :meth:`encode` -- ported faithfully from Lightricks'
        first-party ``ltx-core`` (``video_vae.py``'s ``VideoEncoder.tiled_encode``).
        See ``vae/ltx_tiling.py``'s module docstring for the exact algorithm
        (spatial AND temporal tiling with LTX-specific hard-discard/concat
        masks -- NOT the blend-weighted overlap ``vae/tiling.py`` uses for the
        Wan-shaped causal VAEs) and its documented deviation from whole-clip
        encode near tile seams.

        Only needed when the whole clip doesn't fit in VRAM (see
        ``latent_upscaler/ltx/main.py``'s OOM ladder) -- passing
        ``tiling_config=None`` degenerates to a single whole-clip tile,
        bit-exact with :meth:`encode`.
        """
        from .ltx_tiling import tiled_encode as _tiled_encode
        return _tiled_encode(self, pixels, tiling_config)
