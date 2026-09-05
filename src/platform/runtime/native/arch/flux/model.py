# Derived from: comfy/ldm/flux/model.py (ComfyUI, GPL-3.0) — the block
# composition (double_blocks/single_blocks/pe_embedder/img_in/time_in/
# vector_in/guidance_in/txt_in/final_layer) and forward_orig structure mirror
# ComfyUI's Flux class field-for-field so checkpoint keys map 1:1. Stays in
# src (not vendor/gpl/comfyui/flux/ alongside layers.py/math_ops.py):
# this class extends NativeArchModule (PotionUI's own loader contract) and
# carries PotionUI-only extensions with no ComfyUI equivalent (FirstBlockCache
# step_cache integration, the ref_latents/kontext index/uxo/offset strategies,
# the attn_mask key-padding expansion) — moving it would either drag
# NativeArchModule into vendor (wrong: it's not third-party code) or split an
# inseparable class across two packages.

"""Flux1 / Flux2 (Klein) diffusion transformer.

One ``Flux`` class covers both variants — exactly as ComfyUI does — because they
differ only in config, not structure. The discriminator is ``params.image_model``
(resolved by :class:`FluxParams`):

  * **Flux1** — per-block ``img_mod``/``txt_mod`` modulation, GELU MLP, biases on,
    pooled-CLIP ``vector_in``, distilled ``guidance_in`` (dev checkpoints),
    patch_size 2, 3 RoPE axes.
  * **Flux2 / Klein** — shared ``double_stream_modulation_{img,txt}`` +
    ``single_stream_modulation`` (blocks built with ``modulation=False``), SiLU-gated
    MLP, no biases, no ``vector_in``, no ``guidance_in``, patch_size 1, 4 RoPE axes,
    text tokens positioned on axis 3.

Forward contract (canonical, for the generator / sampling agents)
-----------------------------------------------------------------
``forward(x, timestep, context, y=None, guidance=None, **kwargs)``

  * ``x``        — UNPACKED latent ``(B, C, H, W)``; ``C == params.in_channels``
                   (128 for Klein, 16 for Flux1). H/W are latent (VAE-downscaled)
                   spatial dims. Packing to a token sequence happens *inside*
                   (``process_img``); the generator passes plain latents.
  * ``timestep`` — ``(B,)`` flow-matching t in ``[0, 1]`` (fractional ok).
  * ``context``  — text embeddings ``(B, L, context_in_dim)``.
  * ``y``        — pooled CLIP ``(B, vec_in_dim)``; Flux1 only, ``None`` for Flux2.
  * ``guidance`` — ``(B,)`` distilled guidance; only when ``guidance_embed``.
  * kwargs: ``attention_mask`` (Flux2 512-pad mask), ``ref_latents`` +
            ``ref_latents_method`` (kontext / img2img), ``control`` (ControlNet
            residuals), ``step_cache`` (a :class:`FirstBlockCache` for FBCache
            step skipping — None disables it, the byte-identical default). All
            optional; unused on the txt2img path.

Returns the UNPACKED velocity prediction ``(B, out_channels, H, W)`` (Klein:
128ch; Flux1: 16ch), cropped back to the input H/W.

Only ``x`` and ``timestep`` move between the steps of a run; the text stream, the
pooled/distilled conditioning, the reference latents and the sequence geometry
belong to the guidance branch. Everything derived from the latter alone is
hoisted into :class:`_FluxPrepared` and built once per branch when the engine has
attached a ``run_cache``. Without one, every forward prepares its own —
byte-identical either way.

``pack_latents`` / ``unpack_latents`` / ``prepare_ids`` are exposed as methods so
the sampler never re-derives the patch/token bookkeeping.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

import torch
import torch.nn.functional as F
from einops import rearrange, repeat
from torch import Tensor, nn

from vendor.gpl.comfyui.flux.layers import (
    DoubleStreamBlock,
    EmbedND,
    LastLayer,
    MLPEmbedder,
    RMSNorm,
    SingleStreamBlock,
    Modulation,
    timestep_embedding,
)
from vendor.gpl.comfyui.flux.math_ops import set_attention_backend

from ...attention import attention as _dispatch_attention
from ...base import NativeArchModule
from ...cache_identity import identity_usable, tensor_identity
from .config import FluxParams

if TYPE_CHECKING:
    from ...sampling.step_cache import FirstBlockCache

# vendor/gpl/comfyui/flux/math_ops.py must not import src (layering guard) —
# this is the one module that constructs Flux (and therefore the DoubleStream/
# SingleStreamBlocks that call math_ops.attention()), so wiring the backend
# here guarantees it's set before any forward() runs.
set_attention_backend(_dispatch_attention)


def _pad_to_patch_size(img: Tensor, patch_size: int) -> Tensor:
    """Circular-pad H/W up to a multiple of ``patch_size`` (ComfyUI parity)."""
    pad = ()
    for i in range(img.ndim - 2):
        dim = img.shape[i + 2]
        pad = (0, (patch_size - dim % patch_size) % patch_size) + pad
    if all(p == 0 for p in pad):
        return img
    return F.pad(img, pad, mode="circular")


@dataclass
class _FluxPrepared:
    """One guidance branch's step-invariant forward inputs, plus their sources.

    The RoPE table, the joint key-padding mask, the packed clean reference tokens
    and the ``txt_norm``/``txt_in`` projection are properties of the branch and
    the weights, rebuilt identically at every step of a run.

    ``guidance_vec`` and ``pooled_vec`` are filled on first use rather than with
    the rest: both are cast to the dtype ``img_in`` produces, which the branch
    preparation runs before. ``vec_dtype`` records the dtype they were built under
    so a differing one rebuilds them instead of answering with the wrong cast.

    ``context``, ``token_mask``, ``y``, ``guidance`` and ``refs`` are the tensors
    the cache key names. Holding them closes the one hole ``tensor_identity``
    cannot see: a freed tensor's address can be handed to a new allocation whose
    version counter starts over, and while an entry lives its sources cannot be
    freed.
    """

    pe: Tensor
    attn_mask: Tensor | None
    ref_tokens: Tensor | None
    txt: Tensor
    context: Tensor
    token_mask: Tensor | None
    y: Tensor | None
    guidance: Tensor | None
    refs: tuple
    guidance_vec: Tensor | None = None
    pooled_vec: Tensor | None = None
    vec_dtype: Any = None


class Flux(NativeArchModule):
    """Transformer model for flow matching on sequences (Flux1 + Flux2)."""

    def __init__(self, params: FluxParams, operations, dtype=None, device=None):
        super().__init__()
        self.dtype = dtype
        self.params = params
        self.patch_size = params.patch_size
        self.in_channels = params.in_channels * params.patch_size * params.patch_size
        self.out_channels = params.out_channels * params.patch_size * params.patch_size
        pe_dim = params.hidden_size // params.num_heads
        self.hidden_size = params.hidden_size
        self.num_heads = params.num_heads
        bias = params.ops_bias

        self.pe_embedder = EmbedND(dim=pe_dim, theta=params.theta, axes_dim=params.axes_dim)
        self.img_in = operations.Linear(self.in_channels, self.hidden_size, bias=bias, dtype=dtype, device=device)
        self.time_in = MLPEmbedder(in_dim=256, hidden_dim=self.hidden_size, bias=bias, dtype=dtype, device=device, operations=operations)

        if params.vec_in_dim is not None:
            self.vector_in = MLPEmbedder(params.vec_in_dim, self.hidden_size, dtype=dtype, device=device, operations=operations)
        else:
            self.vector_in = None

        self.guidance_in = (
            MLPEmbedder(in_dim=256, hidden_dim=self.hidden_size, bias=bias, dtype=dtype, device=device, operations=operations)
            if params.guidance_embed else nn.Identity()
        )
        self.txt_in = operations.Linear(params.context_in_dim, self.hidden_size, bias=bias, dtype=dtype, device=device)

        if params.txt_norm:
            self.txt_norm = RMSNorm(params.context_in_dim, dtype=dtype, device=device, operations=operations)
        else:
            self.txt_norm = None

        block_modulation = params.global_modulation is False
        self.double_blocks = nn.ModuleList([
            DoubleStreamBlock(
                self.hidden_size, self.num_heads, mlp_ratio=params.mlp_ratio, qkv_bias=params.qkv_bias,
                modulation=block_modulation, mlp_silu_act=params.mlp_silu_act, proj_bias=bias,
                dtype=dtype, device=device, operations=operations,
            )
            for _ in range(params.depth)
        ])
        self.single_blocks = nn.ModuleList([
            SingleStreamBlock(
                self.hidden_size, self.num_heads, mlp_ratio=params.mlp_ratio,
                modulation=block_modulation, mlp_silu_act=params.mlp_silu_act, bias=bias,
                dtype=dtype, device=device, operations=operations,
            )
            for _ in range(params.depth_single_blocks)
        ])

        self.final_layer = LastLayer(self.hidden_size, 1, self.out_channels, bias=bias, dtype=dtype, device=device, operations=operations)

        if params.global_modulation:
            self.double_stream_modulation_img = Modulation(self.hidden_size, double=True, bias=False, dtype=dtype, device=device, operations=operations)
            self.double_stream_modulation_txt = Modulation(self.hidden_size, double=True, bias=False, dtype=dtype, device=device, operations=operations)
            self.single_stream_modulation = Modulation(self.hidden_size, double=False, bias=False, dtype=dtype, device=device, operations=operations)

    # -- foundation contract ------------------------------------------------

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "Flux":
        """Build empty-weight from a detected DiT config dict.

        Wrap the call in ``with torch.device("meta"):`` to construct without
        allocating the (multi-GB) weights, then ``load_into_module`` assigns them.
        """
        params = FluxParams.from_detect_config(config)
        return cls(params, operations=operations)

    def post_load(self) -> None:
        """No-op: Flux computes RoPE per-forward from ids, so it has no derived
        buffers to recompute after an assign-load. Kept explicit to satisfy the
        mandatory hook and document that the absence is deliberate (verified: the
        module registers no buffers; ``EmbedND``/``timestep_embedding`` build
        their frequencies inline each forward)."""
        return None

    # -- latent packing helpers (exposed for the generator) -----------------

    def pack_latents(self, x: Tensor, index: int = 0, h_offset: int = 0, w_offset: int = 0) -> tuple[Tensor, Tensor]:
        """Patchify ``(B, C, H, W)`` latent to token seq + positional ids.

        Returns ``(img_tokens, img_ids)`` where ``img_tokens`` is
        ``(B, h*w, C*patch*patch)`` and ``img_ids`` is ``(B, h*w, len(axes_dim))``.
        """
        return (self._pack_tokens(x),
                self._pack_ids(x.shape, x.device, index=index, h_offset=h_offset, w_offset=w_offset))

    def _pack_tokens(self, x: Tensor) -> Tensor:
        """The value half of :meth:`pack_latents`.

        Split from the ids because the noisy target is repacked every step, while
        its ids follow from the latent's shape alone and ride the cached RoPE table.
        """
        patch_size = self.patch_size
        x = _pad_to_patch_size(x, patch_size)
        return rearrange(x, "b c (h ph) (w pw) -> b (h w) (c ph pw)", ph=patch_size, pw=patch_size)

    def _pack_ids(self, shape, device, index: float = 0, h_offset: int = 0, w_offset: int = 0) -> Tensor:
        """The position half of :meth:`pack_latents`, from the UNPADDED shape."""
        bs, _, h, w = shape
        patch_size = self.patch_size
        h_len = (h + (patch_size // 2)) // patch_size
        w_len = (w + (patch_size // 2)) // patch_size
        h_off = (h_offset + (patch_size // 2)) // patch_size
        w_off = (w_offset + (patch_size // 2)) // patch_size

        n_axes = len(self.params.axes_dim)
        img_ids = torch.zeros((h_len, w_len, n_axes), device=device, dtype=torch.float32)
        img_ids[:, :, 0] = img_ids[:, :, 0] + index
        img_ids[:, :, 1] = img_ids[:, :, 1] + torch.linspace(h_off, h_len - 1 + h_off, steps=h_len, device=device, dtype=torch.float32).unsqueeze(1)
        img_ids[:, :, 2] = img_ids[:, :, 2] + torch.linspace(w_off, w_len - 1 + w_off, steps=w_len, device=device, dtype=torch.float32).unsqueeze(0)
        return repeat(img_ids, "h w c -> b (h w) c", b=bs)

    def unpack_latents(self, out: Tensor, h_len: int, w_len: int, h_orig: int, w_orig: int) -> Tensor:
        """Inverse of :meth:`pack_latents`: token seq -> ``(B, C, H, W)`` latent."""
        ph = pw = self.patch_size
        return rearrange(out, "b (h w) (c ph pw) -> b c (h ph) (w pw)", h=h_len, w=w_len, ph=ph, pw=pw)[:, :, :h_orig, :w_orig]

    def prepare_ids(self, context_len: int, batch: int, device, dtype=torch.float32) -> Tensor:
        """Text-token positional ids ``(B, L, len(axes_dim))``.

        Flux1 leaves them all-zero (``txt_ids_dims == []``); Flux2 puts a
        linspace on RoPE axis 3 (``txt_ids_dims == [3]``).
        """
        n_axes = len(self.params.axes_dim)
        txt_ids = torch.zeros((batch, context_len, n_axes), device=device, dtype=dtype)
        for i in self.params.txt_ids_dims:
            txt_ids[:, :, i] = torch.linspace(0, context_len - 1, steps=context_len, device=device, dtype=dtype)
        return txt_ids

    # -- step-invariant preparation -----------------------------------------

    def _branch_inputs(self, x: Tensor, context: Tensor, y: Tensor | None,
                       guidance: Tensor | None, token_mask: Tensor | None,
                       refs: tuple, ref_method: str) -> _FluxPrepared:
        """:meth:`_prepare_branch`, once per guidance branch when a run cache is up.

        When the engine has attached a ``run_cache`` (``NativeGenerator.sample``)
        the prepared bundle is reused for the rest of the run; without one every
        forward prepares its own, exactly as before.

        ``cache.revision`` is read here, per lookup, and never hoisted across
        steps: a step-windowed LoRA applies and restores at step boundaries WITHIN
        a run, so a projection computed at step 1 must not answer a lookup at
        step 3 under different weights.
        """
        cache = getattr(self, "run_cache", None)
        key = None
        if cache is not None:
            ids = (tensor_identity(context), tensor_identity(token_mask),
                   tensor_identity(y), tensor_identity(guidance),
                   *(tensor_identity(ref) for ref in refs))
            if identity_usable(*ids):
                key = ("flux.branch", cache.revision, tuple(x.shape), x.dtype, x.device,
                       ref_method if refs else None, *ids)
                hit = cache.get(key)
                if hit is not None:
                    return hit

        prepared = self._prepare_branch(x, context, y, guidance, token_mask, refs, ref_method)
        if key is not None:
            cache.put(key, prepared)
        return prepared

    def _prepare_branch(self, x: Tensor, context: Tensor, y: Tensor | None,
                        guidance: Tensor | None, token_mask: Tensor | None,
                        refs: tuple, ref_method: str) -> _FluxPrepared:
        """Reference packing, positional ids, RoPE, the joint mask and ``txt_in``."""
        img_ids = self._pack_ids(x.shape, x.device)
        ref_tokens = None
        if refs:
            patch_size = self.patch_size
            h_len = (x.shape[-2] + (patch_size // 2)) // patch_size
            w_len = (x.shape[-1] + (patch_size // 2)) // patch_size
            packed: list[Tensor] = []
            ref_ids: list[Tensor] = []
            h = w = index = 0
            for ref in refs:
                if ref_method == "index":
                    index += self.params.ref_index_scale
                    h_offset = w_offset = 0
                elif ref_method == "uxo":
                    index = 0
                    h_offset = h_len * patch_size + h
                    w_offset = w_len * patch_size + w
                    h += ref.shape[-2]
                    w += ref.shape[-1]
                else:  # "offset"
                    index = 1
                    h_offset = w_offset = 0
                    if ref.shape[-2] + h > ref.shape[-1] + w:
                        w_offset = w
                    else:
                        h_offset = h
                    h = max(h, ref.shape[-2] + h_offset)
                    w = max(w, ref.shape[-1] + w_offset)
                packed.append(self._pack_tokens(ref))
                ref_ids.append(self._pack_ids(ref.shape, ref.device, index=index,
                                              h_offset=h_offset, w_offset=w_offset))
            # Reference latents are clean VAE encodings, fixed for the whole run,
            # so tokens and ids are joined in one concatenation rather than one
            # per reference per step.
            ref_tokens = torch.cat(packed, dim=1)
            img_ids = torch.cat([img_ids, *ref_ids], dim=1)

        txt_ids = self.prepare_ids(context.shape[1], x.shape[0], x.device)
        txt = self.txt_norm(context) if self.txt_norm is not None else context
        return _FluxPrepared(
            pe=self.pe_embedder(torch.cat((txt_ids, img_ids), dim=1)),
            attn_mask=self._expand_attention_mask(token_mask, img_ids.shape[1]),
            ref_tokens=ref_tokens,
            txt=self.txt_in(txt),
            context=context, token_mask=token_mask, y=y, guidance=guidance, refs=refs,
        )

    def _fill_vec_terms(self, prepared: _FluxPrepared, img: Tensor) -> None:
        """The two timestep-INDEPENDENT ``vec`` contributions, in ``img``'s dtype.

        Held out of :meth:`_prepare_branch` because both are cast to the dtype
        ``img_in`` produces, which the branch preparation runs before.
        """
        if prepared.vec_dtype == img.dtype:
            return
        guidance_vec = None
        if self.params.guidance_embed and prepared.guidance is not None:
            guidance_vec = self.guidance_in(timestep_embedding(prepared.guidance, 256).to(img.dtype))
        pooled_vec = None
        if self.vector_in is not None:
            y = prepared.y
            if y is None:
                y = torch.zeros((img.shape[0], self.params.vec_in_dim), device=img.device, dtype=img.dtype)
            pooled_vec = self.vector_in(y[:, : self.params.vec_in_dim])
        prepared.guidance_vec, prepared.pooled_vec = guidance_vec, pooled_vec
        prepared.vec_dtype = img.dtype

    # -- forward ------------------------------------------------------------

    def forward_orig(
        self,
        img: Tensor,
        prepared: _FluxPrepared,
        timesteps: Tensor,
        control: dict | None = None,
        step_cache: "FirstBlockCache | None" = None,
    ) -> Tensor:
        # FBCache's probe is captured at block 0 only; ControlNet residuals
        # applied at LATER blocks (control["input"][1:], control["output"]) are
        # invisible to it, so a residual payload that changes between calls
        # while block-0 stays stable would go undetected and reuse a stale
        # cached output. Bypass the cache entirely whenever a control payload
        # is present rather than try to make the probe control-aware — cheap
        # (ControlNet-conditioned generations are already the minority path)
        # and unconditionally safe.
        if control is not None:
            step_cache = None

        if prepared.ref_tokens is not None:
            img = torch.cat([img, prepared.ref_tokens], dim=1)
        img = self.img_in(img)
        vec = self.time_in(timestep_embedding(timesteps, 256).to(img.dtype))
        self._fill_vec_terms(prepared, img)
        if prepared.guidance_vec is not None:
            vec = vec + prepared.guidance_vec
        if prepared.pooled_vec is not None:
            vec = vec + prepared.pooled_vec

        txt, pe, attn_mask = prepared.txt, prepared.pe, prepared.attn_mask
        vec_orig = vec
        if self.params.global_modulation:
            vec = (self.double_stream_modulation_img(vec_orig), self.double_stream_modulation_txt(vec_orig))

        # FBCache probe: block 0's img-stream output is the cheap change proxy.
        # ``should_skip`` gates reusing the last computed output and skipping the
        # remaining double + single blocks and the final projection entirely.
        probe: Tensor | None = None
        for i, block in enumerate(self.double_blocks):
            img, txt = block(img=img, txt=txt, vec=vec, pe=pe, attn_mask=attn_mask)
            if control is not None:
                control_i = control.get("input")
                if control_i is not None and i < len(control_i) and control_i[i] is not None:
                    img[:, : control_i[i].shape[1]] += control_i[i]
            if i == 0 and step_cache is not None:
                probe = img
                if step_cache.should_skip(probe):
                    return step_cache.record_skip()

        if img.dtype == torch.float16:
            img = torch.nan_to_num(img, nan=0.0, posinf=65504, neginf=-65504)

        img = torch.cat((txt, img), 1)
        if self.params.global_modulation:
            vec, _ = self.single_stream_modulation(vec_orig)

        for i, block in enumerate(self.single_blocks):
            img = block(img, vec=vec, pe=pe, attn_mask=attn_mask)
            if control is not None:
                control_o = control.get("output")
                if control_o is not None and i < len(control_o) and control_o[i] is not None:
                    img[:, txt.shape[1] : txt.shape[1] + control_o[i].shape[1], ...] += control_o[i]

        img = img[:, txt.shape[1] :, ...]
        out = self.final_layer(img, vec_orig)
        if step_cache is not None and probe is not None:
            step_cache.record_compute(probe, out)
        return out

    @staticmethod
    def _expand_attention_mask(token_mask: Tensor | None, img_tokens: int) -> Tensor | None:
        """Expand a (B, S_txt) text-token mask to the joint txt+img key sequence.

        Attention runs over cat(txt, img) (txt first, see DoubleStreamBlock and
        forward_orig), so the key-padding mask must cover S_txt + img_tokens; image
        positions are always valid. Returns a bool (B, 1, 1, L) mask (sdpa rejects
        the tokenizer's int64 dtype), or None when nothing is masked so the
        accelerated no-mask attention path stays usable.

        ``token_mask`` MUST be a key-padding mask (1 = real token, 0 = pad) — the
        contract the Qwen3/Klein TE emits. A float *additive* mask (0 / -inf) would
        invert under the bool cast; if such an input ever appears, convert it first.
        """
        if token_mask is None:
            return None
        token_mask = token_mask.to(torch.bool)
        if token_mask.all():
            return None
        joint = torch.ones(
            (token_mask.shape[0], img_tokens), dtype=torch.bool, device=token_mask.device
        )
        return torch.cat((token_mask, joint), dim=1)[:, None, None, :]

    def forward(self, x: Tensor, timestep: Tensor, context: Tensor, y: Tensor | None = None,
                guidance: Tensor | None = None, **kwargs) -> Tensor:
        h_orig, w_orig = x.shape[-2], x.shape[-1]
        patch_size = self.patch_size
        h_len = (h_orig + (patch_size // 2)) // patch_size
        w_len = (w_orig + (patch_size // 2)) // patch_size

        img = self._pack_tokens(x)
        if img.ndim != 3 or context.ndim != 3:
            raise ValueError("Input img and txt tensors must have 3 dimensions.")
        img_tokens = img.shape[1]

        ref_latents = kwargs.get("ref_latents", None)
        prepared = self._branch_inputs(
            x, context, y, guidance, kwargs.get("attention_mask"),
            tuple(ref_latents) if ref_latents is not None else (),
            kwargs.get("ref_latents_method", self.params.default_ref_method),
        )
        out = self.forward_orig(
            img, prepared, timestep,
            control=kwargs.get("control"), step_cache=kwargs.get("step_cache"),
        )
        out = out[:, :img_tokens]
        return self.unpack_latents(out, h_len, w_len, h_orig, w_orig)
