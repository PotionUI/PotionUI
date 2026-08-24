"""Wan 2.1 / 2.2 diffusion transformer (base t2v / i2v backbone).

Vendored from ComfyUI ``comfy/ldm/wan/model.py`` and trimmed to the base
``WanModel`` only — the ``vace`` / ``camera`` / ``s2v`` / ``humo`` / ``animate`` /
``multitalk`` extension modules are intentionally NOT ported (detection rejects
those checkpoints with a clear unsupported error).

Adaptations from upstream:
  * the ``operation_settings`` dict collapses to a plain ``operations`` namespace
    (matching the Flux vendoring), the fp8/manual_cast seam;
  * ``optimized_attention`` is routed through the native attention dispatcher
    (:func:`src.platform.runtime.native.attention.attention`);
  * ``EmbedND`` and ``apply_rope1`` are reused from the Flux arch (upstream Wan
    imports the very same RoPE utilities from ``comfy.ldm.flux``);
  * ComfyUI's ``transformer_options`` / patcher-extension / block-replace hooks
    are dropped (no ComfyUI runtime here);
  * ``comfy.model_management.cast_to`` -> plain ``.to(dtype, device)``.

Forward contract (canonical, for the generator / dual-expert router)
--------------------------------------------------------------------
``forward(x, timestep, context, clip_fea=None, **kwargs)``

  * ``x``        — 5D video latent ``(B, C_in, T, H, W)`` in VAE-latent space.
                   ``C_in == params.in_dim``. For **i2v** the image conditioning
                   (concat of the reference-frame latent + a temporal mask) is
                   already folded into the channel dim by the generator — the
                   14B i2v checkpoints carry a larger ``in_dim`` than t2v (36 vs
                   16). The DiT does NOT build the concat itself.
  * ``timestep`` — ``(B,)`` flow-matching t (the sampler passes sigma; Wan's
                   timestep == sigma*1000 handled by ``sinusoidal_embedding_1d``).
  * ``context``  — UMT5 text embeddings ``(B, L, text_dim)`` (text_dim 4096).
  * ``clip_fea`` — **i2v only**: CLIP-vision features ``(B, 257, 1280)`` from the
                   reference frame; projected by ``img_emb`` and prepended to the
                   cross-attention context. ``None`` for t2v. Optional kwarg.
  * kwargs: ``reference_latent`` (ref_conv variants only). All optional.

Returns the velocity prediction ``(B, out_dim, T, H, W)`` cropped to input dims.
"""

from __future__ import annotations

import math
from typing import Any

import torch
import torch.nn as nn

from einops import rearrange

from ...attention import attention as _dispatch_attention
from ...base import NativeArchModule
from ...nag import apply_nag
from vendor.gpl.comfyui.flux.layers import EmbedND
from vendor.gpl.comfyui.flux.math_ops import apply_rope1, rope
from .config import WanParams

# Wan 2.1/2.2's official max training length is 81 pixel frames (5s @ 16fps);
# the causal VAE's 1+4k temporal chunking maps that to (81-1)//4+1 = 21 latent
# frames. Used as the RIFLEx "trained length" (N in the paper) when the caller
# doesn't supply one explicitly.
WAN_DEFAULT_TRAINED_LATENT_FRAMES = 21


def sinusoidal_embedding_1d(dim: int, position: torch.Tensor) -> torch.Tensor:
    assert dim % 2 == 0
    half = dim // 2
    position = position.type(torch.float32)
    sinusoid = torch.outer(position, torch.pow(10000, -torch.arange(half).to(position).div(half)))
    return torch.cat([torch.cos(sinusoid), torch.sin(sinusoid)], dim=1)


def _pad_to_patch_size(img: torch.Tensor, patch_size, padding_mode: str = "circular") -> torch.Tensor:
    """Pad the trailing (T,H,W) dims up to a multiple of ``patch_size``."""
    pad = ()
    for i in range(img.ndim - 2):
        pad = (0, (patch_size[i] - img.shape[i + 2] % patch_size[i]) % patch_size[i]) + pad
    if all(p == 0 for p in pad):
        return img
    return torch.nn.functional.pad(img, pad, mode=padding_mode)


def _cast(t: torch.Tensor, ref: torch.Tensor) -> torch.Tensor:
    return t.to(dtype=ref.dtype, device=ref.device)


def wan_attention(q: torch.Tensor, k: torch.Tensor, v: torch.Tensor, heads: int) -> torch.Tensor:
    """Attention on ``(B, L, heads*dim)`` tensors via the native dispatcher.

    Mirrors ComfyUI's ``optimized_attention(..., heads=)`` contract: q/k/v come in
    flattened ``(B, L, H*D)`` and the result is ``(B, Lq, H*D)``. The dispatcher
    works on head-split ``(B, H, L, D)``, so we reshape in and out.
    """
    b = q.shape[0]

    def split(t: torch.Tensor) -> torch.Tensor:
        return t.view(b, t.shape[1], heads, t.shape[-1] // heads).transpose(1, 2)

    out = _dispatch_attention(split(q), split(k), split(v), heads=heads)
    return out.transpose(1, 2).reshape(b, q.shape[1], -1)


class WanSelfAttention(nn.Module):
    def __init__(self, dim, num_heads, window_size=(-1, -1), qk_norm=True, eps=1e-6,
                 kv_dim=None, *, operations, device=None, dtype=None):
        assert dim % num_heads == 0
        super().__init__()
        self.dim = dim
        self.num_heads = num_heads
        self.head_dim = dim // num_heads
        self.window_size = window_size
        self.qk_norm = qk_norm
        self.eps = eps
        if kv_dim is None:
            kv_dim = dim

        self.q = operations.Linear(dim, dim, device=device, dtype=dtype)
        self.k = operations.Linear(kv_dim, dim, device=device, dtype=dtype)
        self.v = operations.Linear(kv_dim, dim, device=device, dtype=dtype)
        self.o = operations.Linear(dim, dim, device=device, dtype=dtype)
        self.norm_q = operations.RMSNorm(dim, eps=eps, elementwise_affine=True, device=device, dtype=dtype) if qk_norm else nn.Identity()
        self.norm_k = operations.RMSNorm(dim, eps=eps, elementwise_affine=True, device=device, dtype=dtype) if qk_norm else nn.Identity()

    def forward(self, x, freqs):
        b, s, n, d = *x.shape[:2], self.num_heads, self.head_dim
        q = apply_rope1(self.norm_q(self.q(x)).view(b, s, n, d), freqs).reshape(b, s, n * d)
        k = apply_rope1(self.norm_k(self.k(x)).view(b, s, n, d), freqs).reshape(b, s, n * d)
        v = self.v(x)
        x = wan_attention(q, k, v, heads=self.num_heads)
        return self.o(x)


def _nag_active(nag: dict | None) -> bool:
    return bool(nag) and float(nag.get("scale", 1.0)) > 1.0


class WanT2VCrossAttention(WanSelfAttention):
    def forward(self, x, context, context_img_len=None, nag_context=None, nag=None):
        q = self.norm_q(self.q(x))
        k = self.norm_k(self.k(context))
        v = self.v(context)
        x = wan_attention(q, k, v, heads=self.num_heads)
        if nag_context is not None and _nag_active(nag):
            k_neg = self.norm_k(self.k(nag_context))
            v_neg = self.v(nag_context)
            neg = wan_attention(q, k_neg, v_neg, heads=self.num_heads)
            x = apply_nag(x, neg, nag["scale"], nag.get("tau", 3.5), nag.get("alpha", 0.5))
        return self.o(x)


class WanI2VCrossAttention(WanSelfAttention):
    def __init__(self, dim, num_heads, window_size=(-1, -1), qk_norm=True, eps=1e-6,
                 *, operations, device=None, dtype=None):
        super().__init__(dim, num_heads, window_size, qk_norm, eps,
                         operations=operations, device=device, dtype=dtype)
        self.k_img = operations.Linear(dim, dim, device=device, dtype=dtype)
        self.v_img = operations.Linear(dim, dim, device=device, dtype=dtype)
        self.norm_k_img = operations.RMSNorm(dim, eps=eps, elementwise_affine=True, device=device, dtype=dtype) if qk_norm else nn.Identity()

    def forward(self, x, context, context_img_len, nag_context=None, nag=None):
        context_img = context[:, :context_img_len]
        context = context[:, context_img_len:]
        q = self.norm_q(self.q(x))
        k = self.norm_k(self.k(context))
        v = self.v(context)
        k_img = self.norm_k_img(self.k_img(context_img))
        v_img = self.v_img(context_img)
        img_x = wan_attention(q, k_img, v_img, heads=self.num_heads)
        text_x = wan_attention(q, k, v, heads=self.num_heads)
        # NAG only guides the text-token attention component — the image (CLIP)
        # cross-attention is added back untouched.
        if nag_context is not None and _nag_active(nag):
            k_neg = self.norm_k(self.k(nag_context))
            v_neg = self.v(nag_context)
            neg_x = wan_attention(q, k_neg, v_neg, heads=self.num_heads)
            text_x = apply_nag(text_x, neg_x, nag["scale"], nag.get("tau", 3.5), nag.get("alpha", 0.5))
        x = text_x + img_x
        return self.o(x)


_WAN_CROSSATTENTION_CLASSES = {
    "t2v_cross_attn": WanT2VCrossAttention,
    "i2v_cross_attn": WanI2VCrossAttention,
}


def repeat_e(e, x):
    """Broadcast a per-(few)-token modulation ``e`` to x's sequence length."""
    repeats = 1
    if e.size(1) > 1:
        repeats = x.size(1) // e.size(1)
    if repeats == 1:
        return e
    if repeats * e.size(1) == x.size(1):
        return torch.repeat_interleave(e, repeats, dim=1)
    return torch.repeat_interleave(e, repeats + 1, dim=1)[:, :x.size(1)]


class WanAttentionBlock(nn.Module):
    def __init__(self, cross_attn_type, dim, ffn_dim, num_heads, window_size=(-1, -1),
                 qk_norm=True, cross_attn_norm=False, eps=1e-6, *, operations, device=None, dtype=None):
        super().__init__()
        self.dim = dim
        self.ffn_dim = ffn_dim
        self.num_heads = num_heads
        self.window_size = window_size
        self.qk_norm = qk_norm
        self.cross_attn_norm = cross_attn_norm
        self.eps = eps

        self.norm1 = operations.LayerNorm(dim, eps, elementwise_affine=False, device=device, dtype=dtype)
        self.self_attn = WanSelfAttention(dim, num_heads, window_size, qk_norm, eps,
                                          operations=operations, device=device, dtype=dtype)
        self.norm3 = operations.LayerNorm(dim, eps, elementwise_affine=True, device=device, dtype=dtype) if cross_attn_norm else nn.Identity()
        self.cross_attn = _WAN_CROSSATTENTION_CLASSES[cross_attn_type](
            dim, num_heads, (-1, -1), qk_norm, eps, operations=operations, device=device, dtype=dtype)
        self.norm2 = operations.LayerNorm(dim, eps, elementwise_affine=False, device=device, dtype=dtype)
        self.ffn = nn.Sequential(
            operations.Linear(dim, ffn_dim, device=device, dtype=dtype),
            nn.GELU(approximate="tanh"),
            operations.Linear(ffn_dim, dim, device=device, dtype=dtype),
        )
        self.modulation = nn.Parameter(torch.empty(1, 6, dim, device=device, dtype=dtype))

    def forward(self, x, e, freqs, context, context_img_len=257, nag_context=None, nag=None):
        if e.ndim < 4:
            e = (_cast(self.modulation, x) + e).chunk(6, dim=1)
        else:
            e = (_cast(self.modulation, x).unsqueeze(0) + e).unbind(2)

        x = x.contiguous()
        y = self.self_attn(torch.addcmul(repeat_e(e[0], x), self.norm1(x), 1 + repeat_e(e[1], x)), freqs)
        x = torch.addcmul(x, y, repeat_e(e[2], x))
        del y

        x = x + self.cross_attn(self.norm3(x), context, context_img_len=context_img_len,
                                 nag_context=nag_context, nag=nag)

        y = self.ffn(torch.addcmul(repeat_e(e[3], x), self.norm2(x), 1 + repeat_e(e[4], x)))
        x = torch.addcmul(x, y, repeat_e(e[5], x))
        return x


class Head(nn.Module):
    def __init__(self, dim, out_dim, patch_size, eps=1e-6, *, operations, device=None, dtype=None):
        super().__init__()
        self.dim = dim
        self.out_dim = out_dim
        self.patch_size = patch_size
        self.eps = eps
        out_dim = math.prod(patch_size) * out_dim
        self.norm = operations.LayerNorm(dim, eps, elementwise_affine=False, device=device, dtype=dtype)
        self.head = operations.Linear(dim, out_dim, device=device, dtype=dtype)
        self.modulation = nn.Parameter(torch.empty(1, 2, dim, device=device, dtype=dtype))

    def forward(self, x, e):
        if e.ndim < 3:
            e = (_cast(self.modulation, x) + e.unsqueeze(1)).chunk(2, dim=1)
        else:
            e = (_cast(self.modulation, x).unsqueeze(0) + e.unsqueeze(2)).unbind(2)
        return self.head(torch.addcmul(repeat_e(e[0], x), self.norm(x), 1 + repeat_e(e[1], x)))


class MLPProj(nn.Module):
    """Projects i2v CLIP-vision features into the DiT hidden dim (img cross-attn)."""

    def __init__(self, in_dim, out_dim, flf_pos_embed_token_number=None, *, operations, device=None, dtype=None):
        super().__init__()
        self.proj = nn.Sequential(
            operations.LayerNorm(in_dim, device=device, dtype=dtype),
            operations.Linear(in_dim, in_dim, device=device, dtype=dtype),
            nn.GELU(),
            operations.Linear(in_dim, out_dim, device=device, dtype=dtype),
            operations.LayerNorm(out_dim, device=device, dtype=dtype),
        )
        if flf_pos_embed_token_number is not None:
            self.emb_pos = nn.Parameter(torch.empty((1, flf_pos_embed_token_number, in_dim), device=device, dtype=dtype))
        else:
            self.emb_pos = None

    def forward(self, image_embeds):
        if self.emb_pos is not None:
            image_embeds = image_embeds[:, :self.emb_pos.shape[1]] + _cast(self.emb_pos[:, :image_embeds.shape[1]], image_embeds)
        return self.proj(image_embeds)


def _riflex_intrinsic_k(dim: int, theta: float, latent_frames_trained: int) -> int:
    """Locate the "intrinsic" temporal RoPE frequency component (RIFLEx,
    arXiv:2502.15894, eq. 7): the index whose period is closest to the trained
    video length. Video DiTs repeat/loop past the trained length because this
    one low-frequency component completes an extra cycle; every other
    component's period is short enough that extrapolation doesn't touch it.

    Mirrors ``flux.math_ops.rope``'s frequency construction (paper eq. 4,
    1-indexed j=1..dim//2): ``theta_j = theta ** (-2*(j-1)/dim)``,
    ``N_j = round(2*pi / theta_j)``. Returns a 0-based index into the
    ``dim//2``-length omega array that ``rope()`` builds.
    """
    half = dim // 2
    best_idx, best_diff = 0, float("inf")
    for j in range(1, half + 1):
        theta_j = theta ** (-2.0 * (j - 1) / dim)
        period = round(2.0 * math.pi / theta_j)
        diff = abs(period - latent_frames_trained)
        if diff < best_diff:
            best_diff = diff
            best_idx = j - 1
    return best_idx


def _rope_temporal_riflex(pos: torch.Tensor, dim: int, theta: float, k: int, theta_k: float) -> torch.Tensor:
    """Temporal-axis RoPE tensor, identical to ``flux.math_ops.rope`` except
    frequency component ``k`` is CLAMPED DOWNWARD to ``theta_k`` (RIFLEx eq. 8)
    when it's currently higher. Duplicated (rather than adding a hook to the
    shared ``rope()``) because the override is Wan-temporal-axis-specific; kept
    a line-for-line mirror so it stays bit-identical to ``rope()`` everywhere
    except the one component.

    Clamp, not overwrite: ``theta_k`` is the *maximum* frequency component k
    may have without wrapping within the extrapolated length (eq. 8's
    non-repetition bound). If a forced/auto-detected ``k`` already has a lower
    natural frequency than that bound (period already longer than needed),
    unconditionally overwriting it would instead ACCELERATE that component —
    e.g. dim=16/theta=10000/k=7's natural frequency (~3.16e-4) is far below a
    typical ``theta_k`` (~0.26 at t_len=22), and an overwrite would multiply it
    ~800x instead of leaving the already-safe component alone.
    """
    assert dim % 2 == 0
    device = pos.device
    scale = torch.linspace(0, (dim - 2) / dim, steps=dim // 2, dtype=torch.float64, device=device)
    omega = 1.0 / (theta**scale)
    omega[k] = min(float(omega[k]), theta_k)
    out = torch.einsum("...n,d->...nd", pos.to(dtype=torch.float32, device=device), omega)
    out = torch.stack([torch.cos(out), -torch.sin(out), torch.sin(out), torch.cos(out)], dim=-1)
    out = rearrange(out, "b n d (i j) -> b n d i j", i=2, j=2)
    return out.to(dtype=torch.float32, device=pos.device)


class WanModel(NativeArchModule):
    """Wan 2.1 / 2.2 diffusion backbone (text-to-video + image-to-video)."""

    def __init__(self, params: WanParams, operations, dtype=None, device=None):
        super().__init__()
        self.dtype = dtype
        self.params = params
        self.model_type = params.model_type
        self.patch_size = params.patch_size
        self.text_len = params.text_len
        self.in_dim = params.in_dim
        self.dim = params.dim
        self.ffn_dim = params.ffn_dim
        self.freq_dim = params.freq_dim
        self.text_dim = params.text_dim
        self.out_dim = params.out_dim
        self.num_heads = params.num_heads
        self.num_layers = params.num_layers
        self.eps = params.eps

        self.patch_embedding = operations.Conv3d(
            params.in_dim, params.dim, kernel_size=params.patch_size, stride=params.patch_size,
            device=device, dtype=torch.float32)
        self.text_embedding = nn.Sequential(
            operations.Linear(params.text_dim, params.dim, device=device, dtype=dtype),
            nn.GELU(approximate="tanh"),
            operations.Linear(params.dim, params.dim, device=device, dtype=dtype))
        self.time_embedding = nn.Sequential(
            operations.Linear(params.freq_dim, params.dim, device=device, dtype=dtype),
            nn.SiLU(),
            operations.Linear(params.dim, params.dim, device=device, dtype=dtype))
        self.time_projection = nn.Sequential(
            nn.SiLU(), operations.Linear(params.dim, params.dim * 6, device=device, dtype=dtype))

        cross_attn_type = "t2v_cross_attn" if params.model_type == "t2v" else "i2v_cross_attn"
        self.blocks = nn.ModuleList([
            WanAttentionBlock(cross_attn_type, params.dim, params.ffn_dim, params.num_heads,
                              params.window_size, params.qk_norm, params.cross_attn_norm, params.eps,
                              operations=operations, device=device, dtype=dtype)
            for _ in range(params.num_layers)
        ])
        self.head = Head(params.dim, params.out_dim, params.patch_size, params.eps,
                         operations=operations, device=device, dtype=dtype)

        d = params.dim // params.num_heads
        self.rope_embedder = EmbedND(dim=d, theta=10000.0, axes_dim=[d - 4 * (d // 6), 2 * (d // 6), 2 * (d // 6)])

        if params.model_type == "i2v":
            self.img_emb = MLPProj(1280, params.dim, flf_pos_embed_token_number=params.flf_pos_embed_token_number,
                                   operations=operations, device=device, dtype=dtype)
        else:
            self.img_emb = None

        if params.in_dim_ref_conv is not None:
            self.ref_conv = operations.Conv2d(params.in_dim_ref_conv, params.dim,
                                              kernel_size=params.patch_size[1:], stride=params.patch_size[1:],
                                              device=device, dtype=dtype)
        else:
            self.ref_conv = None

    # -- foundation contract ------------------------------------------------

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "WanModel":
        return cls(WanParams.from_detect_config(config), operations=operations)

    def post_load(self) -> None:
        """No-op: Wan has no computed buffers. RoPE freqs are built inline each
        forward by ``EmbedND``/``rope_encode`` (verified: the module registers no
        buffers — ``modulation``/``emb_pos`` are loaded parameters, not derived).
        Kept explicit to satisfy the mandatory hook."""
        return None

    # -- rope ---------------------------------------------------------------

    def rope_encode(self, t, h, w, device=None, dtype=None, riflex: dict | None = None):
        """Build the joint (t, h, w) RoPE tensor.

        ``riflex`` (roadmap 3.8, RIFLEx arXiv:2502.15894): opt-in video-length
        extrapolation. ``None``/``{"enabled": False}`` (the default) is a
        byte-identical no-op — same code path as before RIFLEx existed. When
        enabled with ``{"enabled": True, "latent_frames_trained": int|None,
        "k": int|None}`` and the requested latent frame count exceeds
        ``latent_frames_trained`` (default :data:`WAN_DEFAULT_TRAINED_LATENT_FRAMES`),
        the intrinsic temporal frequency component is clamped (see
        ``_riflex_intrinsic_k`` / ``_rope_temporal_riflex``) so it completes at
        most ~0.9 of a cycle over the extrapolated length instead of wrapping.
        ``k`` can be forced explicitly (skips auto-detection); everything else
        (h/w axes, and the temporal axis when not extrapolating) is untouched.
        """
        pt, ph, pw = self.patch_size
        t_len = (t + (pt // 2)) // pt
        h_len = (h + (ph // 2)) // ph
        w_len = (w + (pw // 2)) // pw
        img_ids = torch.zeros((t_len, h_len, w_len, 3), device=device, dtype=dtype)
        img_ids[:, :, :, 0] += torch.linspace(0, t_len - 1, steps=t_len, device=device, dtype=dtype).reshape(-1, 1, 1)
        img_ids[:, :, :, 1] += torch.linspace(0, h_len - 1, steps=h_len, device=device, dtype=dtype).reshape(1, -1, 1)
        img_ids[:, :, :, 2] += torch.linspace(0, w_len - 1, steps=w_len, device=device, dtype=dtype).reshape(1, 1, -1)
        img_ids = img_ids.reshape(1, -1, 3)

        if not riflex or not riflex.get("enabled"):
            return self.rope_embedder(img_ids).movedim(1, 2)

        latent_frames_trained = int(riflex.get("latent_frames_trained") or WAN_DEFAULT_TRAINED_LATENT_FRAMES)
        if t_len <= latent_frames_trained:
            # Not actually extrapolating past the trained length: nothing to fix.
            return self.rope_embedder(img_ids).movedim(1, 2)

        axes_dim = self.rope_embedder.axes_dim
        theta = self.rope_embedder.theta
        k = riflex.get("k")
        if k is None:
            k = _riflex_intrinsic_k(axes_dim[0], theta, latent_frames_trained)
        # Minimal non-repetition solution is theta_k' = 2*pi/L_test (eq. 8); the
        # reference implementation (thu-ml/DiT-Extrapolation) applies a
        # conservative 0.9 factor so the tail stays inside 90% of a period
        # rather than exactly at the wrap boundary.
        theta_k = 0.9 * 2.0 * math.pi / t_len

        temporal = _rope_temporal_riflex(img_ids[..., 0], axes_dim[0], theta, k, theta_k)
        h_freqs = rope(img_ids[..., 1], axes_dim[1], theta)
        w_freqs = rope(img_ids[..., 2], axes_dim[2], theta)
        emb = torch.cat([temporal, h_freqs, w_freqs], dim=-3).unsqueeze(1)
        return emb.movedim(1, 2)

    # -- forward ------------------------------------------------------------

    def forward(self, x, timestep, context, clip_fea=None, time_dim_concat=None, riflex: dict | None = None,
                skip_layers: set[int] | None = None, **kwargs):
        # ``riflex``: see rope_encode() docstring. Threaded here (rather than
        # via **kwargs) so it's consumed before reaching _forward_orig, which
        # has no use for it. The generator pipe is expected to set it from a
        # config knob, e.g. riflex={"enabled": cfg.get("riflex", False),
        # "latent_frames_trained": cfg.get("riflex_trained_frames")} — not
        # wired up here (out of scope: src/pipelines/pipes/generator/*_wan22).
        #
        # ``skip_layers`` (roadmap 3.5, Skip-Layer Guidance — no paper; SD3.5/
        # ComfyUI SkipLayerGuidanceDiT concept, re-derived, NOT ported — see
        # sampling/cfg.py::SkipLayerGuidance): block indices in ``self.blocks``
        # to bypass entirely (identity passthrough) for one degraded forward
        # pass. ``None`` (the default) is a byte-identical no-op — same code
        # path as before SLG existed. Threaded like ``riflex``: consumed here,
        # not forwarded via ``**kwargs`` past ``_forward_orig``.
        bs, c, t, h, w = x.shape
        x = _pad_to_patch_size(x, self.patch_size)

        t_len = t
        if time_dim_concat is not None:
            time_dim_concat = _pad_to_patch_size(time_dim_concat, self.patch_size)
            x = torch.cat([x, time_dim_concat], dim=2)
            t_len = x.shape[2]
        if self.ref_conv is not None and "reference_latent" in kwargs:
            t_len += 1

        freqs = self.rope_encode(t_len, h, w, device=x.device, dtype=x.dtype, riflex=riflex)
        # FBCache: a degraded (skip_layers) pass must never touch the cache — the
        # denoise wrapper already withholds step_cache from it, but guard here too
        # so the arch is correct regardless of how the cache is threaded.
        step_cache = kwargs.pop("step_cache", None)
        if skip_layers:
            step_cache = None
        out = self._forward_orig(x, timestep, context, clip_fea=clip_fea, freqs=freqs,
                                  skip_layers=skip_layers, step_cache=step_cache, **kwargs)
        return out[:, :, :t, :h, :w]

    def _forward_orig(self, x, t, context, clip_fea=None, freqs=None,
                       nag_context=None, nag=None, skip_layers: set[int] | None = None,
                       step_cache=None, **kwargs):
        x = self.patch_embedding(x.float()).to(self.dtype if self.dtype is not None else x.dtype)
        grid_sizes = x.shape[2:]
        x = x.flatten(2).transpose(1, 2)

        e = self.time_embedding(sinusoidal_embedding_1d(self.freq_dim, t.flatten()).to(dtype=x.dtype))
        e = e.reshape(t.shape[0], -1, e.shape[-1])
        e0 = self.time_projection(e).unflatten(2, (6, self.dim))

        full_ref = None
        if self.ref_conv is not None:
            full_ref = kwargs.get("reference_latent", None)
            if full_ref is not None:
                full_ref = self.ref_conv(full_ref).flatten(2).transpose(1, 2)
                x = torch.concat((full_ref, x), dim=1)

        context = self.text_embedding(context)
        # NAG's negative context is text-only (no CLIP-vision tokens): project it
        # through the same text_embedding as the positive context, but never
        # concat it with clip_fea — the i2v cross-attn keeps image attention
        # untouched and only guides the text-attention branch (see
        # WanI2VCrossAttention.forward).
        nag_context = self.text_embedding(nag_context) if nag_context is not None else None

        context_img_len = None
        if clip_fea is not None:
            if self.img_emb is not None:
                context_clip = self.img_emb(clip_fea)
                context = torch.concat([context_clip, context], dim=1)
            context_img_len = clip_fea.shape[-2]

        # FBCache: block-0's output is the change proxy; a skip reuses the last
        # computed output (pre-crop; forward re-applies the crop) and bypasses
        # blocks 1..N + head. step_cache is None on skip_layers passes (guarded
        # in forward), so it never interacts with SLG.
        probe = None
        for i, block in enumerate(self.blocks):
            if skip_layers and i in skip_layers:
                continue
            x = block(x, e=e0, freqs=freqs, context=context, context_img_len=context_img_len,
                      nag_context=nag_context, nag=nag)
            if i == 0 and step_cache is not None:
                probe = x
                if step_cache.should_skip(probe):
                    return step_cache.record_skip()

        x = self.head(x, e)
        if full_ref is not None:
            x = x[:, full_ref.shape[1]:]
        out = self._unpatchify(x, grid_sizes)
        if step_cache is not None and probe is not None:
            step_cache.record_compute(probe, out)
        return out

    def _unpatchify(self, x, grid_sizes):
        c = self.out_dim
        b = x.shape[0]
        u = x[:, :math.prod(grid_sizes)].view(b, *grid_sizes, *self.patch_size, c)
        u = torch.einsum("bfhwpqrc->bcfphqwr", u)
        return u.reshape(b, c, *[i * j for i, j in zip(grid_sizes, self.patch_size)])
