"""Qwen2.5-VL-7B text encoder for Qwen-Image (text-only, or image-conditioned
for Qwen-Image-Edit when ``vision=True`` was requested at load time).

Vendored from ComfyUI ``comfy/text_encoders/llama.py`` (the ``Qwen25_7BVLI``
config + the m-RoPE position-id construction embedded in its ``forward``) +
``comfy/text_encoders/qwen_image.py`` (template + prefix drop). By default only
the language model is used; the checkpoint's ``visual.*`` vision tower and
``lm_head`` are dropped at load — text-only txt2img, unchanged from before this
module gained vision support. Requesting ``vision=True`` at load time (see
``loader.py``) keeps+loads ``visual.*`` into a :class:`~.qwen_vl_vision.
Qwen2VLVisionTower` and enables ``encode(texts, images=...)`` — see
:meth:`Qwen25VLTextEncoder._encode_with_images` for the splice+m-RoPE mechanics
and ``qwen_vl_vision.py``'s module docstring for the full provenance/cross-check.

Arch vs Qwen3 (Klein/Krea-2, see ``qwen3.py``): qwen2.5 has **q/k/v bias** and
**no q/k RMS norm**; otherwise the Llama-family block (silu MLP, RMS input/post
norms, GQA, 1-D RoPE) is identical, so the shared primitives are reused. RoPE here
is nominally m-RoPE (``rope_dims``): for text-only 1-D position ids it reduces to
the standard rope (unchanged, bit-identical fast path — see ``_rope``); an
image-conditioned encode instead builds genuine 3-axis (T,H,W) position ids (see
``_mrope``) via :func:`~.qwen_vl_vision.qwen25vl_mrope_position_ids`.

Conditioning (ComfyUI ``QwenImageTEModel``): the DiT context is the **last** hidden
state (after the final RMS norm), with the fixed system+user-header template prefix
stripped from the sequence (34 tokens for the text-only template; a different,
also-fixed length for the image template — see ``tokenization.py``). No pooled
vector, image-conditioned or not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import torch
import torch.nn as nn

from ..base import NativeArchModule
from ..errors import NativeEngineUnsupportedError
from ._functional import optimized_attention
from .base import NativeTextEncoder, _module_to, _module_unload
from .qwen3 import _MLP, _RMSNorm, _apply_rope
from .qwen_vl_vision import (
    ROPE_DIMS,
    Qwen2VLVisionTower,
    preprocess_qwen_vl_image,
    qwen25vl_mrope_position_ids,
)
from .qwen_vl_vision import VISION_HIDDEN_SIZE as _VISION_HIDDEN_SIZE
from .qwen_vl_vision import VISION_INTERMEDIATE_SIZE as _VISION_INTERMEDIATE_SIZE
from .qwen_vl_vision import VISION_NUM_HEADS as _VISION_NUM_HEADS
from .qwen_vl_vision import VISION_NUM_LAYERS as _VISION_NUM_LAYERS

logger = logging.getLogger(__name__)


@dataclass
class Qwen25VLConfig:
    hidden_size: int = 3584
    intermediate_size: int = 18944
    num_hidden_layers: int = 28
    vocab_size: int = 152064
    num_attention_heads: int = 28
    num_key_value_heads: int = 4
    head_dim: int = 128
    rms_norm_eps: float = 1e-6
    rope_theta: float = 1000000.0
    # Vision tower — OFF by default (text-only construction is unaffected: no
    # `self.visual` attribute at all, so the state-dict key set and every
    # forward path are exactly as before this field existed).
    vision: bool = False
    vision_hidden_size: int = _VISION_HIDDEN_SIZE
    vision_intermediate_size: int = _VISION_INTERMEDIATE_SIZE
    vision_num_layers: int = _VISION_NUM_LAYERS
    # Not checkpoint-derivable (unlike the LM's GQA head counts, which
    # `_build_config` recovers from projection shapes against the KNOWN
    # head_dim=128): the real 7B tower is always 16 heads. Overridable only so
    # tiny synthetic configs (tests) can shrink `vision_hidden_size` below 16
    # without an invalid (non-integer) head_dim; the loader never overrides it.
    vision_num_heads: int = _VISION_NUM_HEADS

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> "Qwen25VLConfig":
        # Overrides let tests build tiny models; detection supplies
        # hidden/layers/vocab, the loader fills intermediate_size + head counts.
        return cls(
            hidden_size=int(config["hidden_size"]),
            intermediate_size=int(config.get("intermediate_size", 18944)),
            num_hidden_layers=int(config["num_layers"]),
            vocab_size=int(config["vocab_size"]),
            num_attention_heads=int(config.get("num_attention_heads", 28)),
            num_key_value_heads=int(config.get("num_key_value_heads", 4)),
            head_dim=int(config.get("head_dim", 128)),
            vision=bool(config.get("vision", False)),
            vision_hidden_size=int(config.get("vision_hidden_size", _VISION_HIDDEN_SIZE)),
            vision_intermediate_size=int(config.get("vision_intermediate_size", _VISION_INTERMEDIATE_SIZE)),
            vision_num_layers=int(config.get("vision_num_layers", _VISION_NUM_LAYERS)),
            vision_num_heads=int(config.get("vision_num_heads", _VISION_NUM_HEADS)),
        )


class _AttentionVL(nn.Module):
    """Qwen2.5-VL attention: q/k/v have bias, no q/k norm (cf. Qwen3's ``_Attention``)."""

    def __init__(self, cfg: Qwen25VLConfig, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.num_heads = cfg.num_attention_heads
        self.num_kv_heads = cfg.num_key_value_heads
        self.head_dim = cfg.head_dim
        inner = self.num_heads * self.head_dim
        kv = self.num_kv_heads * self.head_dim
        self.q_proj = operations.Linear(cfg.hidden_size, inner, bias=True, device=device, dtype=dtype)
        self.k_proj = operations.Linear(cfg.hidden_size, kv, bias=True, device=device, dtype=dtype)
        self.v_proj = operations.Linear(cfg.hidden_size, kv, bias=True, device=device, dtype=dtype)
        self.o_proj = operations.Linear(inner, cfg.hidden_size, bias=False, device=device, dtype=dtype)

    def forward(self, x, cos, sin, mask):
        b, s, _ = x.shape
        xq = self.q_proj(x).view(b, s, self.num_heads, self.head_dim).transpose(1, 2)
        xk = self.k_proj(x).view(b, s, self.num_kv_heads, self.head_dim).transpose(1, 2)
        xv = self.v_proj(x).view(b, s, self.num_kv_heads, self.head_dim).transpose(1, 2)
        xq, xk = _apply_rope(xq, xk, cos, sin)
        xk = xk.repeat_interleave(self.num_heads // self.num_kv_heads, dim=1)
        xv = xv.repeat_interleave(self.num_heads // self.num_kv_heads, dim=1)
        out = optimized_attention(xq, xk, xv, self.num_heads, mask=mask, skip_reshape=True)
        return self.o_proj(out)


class _BlockVL(nn.Module):
    def __init__(self, cfg: Qwen25VLConfig, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.self_attn = _AttentionVL(cfg, operations, device=device, dtype=dtype)
        self.mlp = _MLP(cfg, operations, device=device, dtype=dtype)
        self.input_layernorm = _RMSNorm(cfg.hidden_size, cfg.rms_norm_eps, device=device, dtype=dtype)
        self.post_attention_layernorm = _RMSNorm(cfg.hidden_size, cfg.rms_norm_eps, device=device, dtype=dtype)

    def forward(self, x, cos, sin, mask):
        x = x + self.self_attn(self.input_layernorm(x), cos, sin, mask)
        x = x + self.mlp(self.post_attention_layernorm(x))
        return x


class _Qwen25VLTransformer(nn.Module):
    """The ``model.*`` submodule; returns the final-normed last hidden state."""

    def __init__(self, cfg: Qwen25VLConfig, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.cfg = cfg
        self.embed_tokens = operations.Embedding(cfg.vocab_size, cfg.hidden_size, device=device, dtype=dtype)
        self.layers = nn.ModuleList([_BlockVL(cfg, operations, device=device, dtype=dtype) for _ in range(cfg.num_hidden_layers)])
        self.norm = _RMSNorm(cfg.hidden_size, cfg.rms_norm_eps, device=device, dtype=dtype)
        self.register_buffer("inv_freq", torch.empty(cfg.head_dim // 2), persistent=False)

    def recompute_inv_freq(self) -> None:
        half = torch.arange(0, self.cfg.head_dim, 2, dtype=torch.float32)
        self.inv_freq = (1.0 / (self.cfg.rope_theta ** (half / self.cfg.head_dim))).to(self.embed_tokens.weight.device)

    def _rope(self, seq_len: int, device, dtype):
        pos = torch.arange(seq_len, device=device, dtype=torch.float32)
        freqs = torch.outer(pos, self.inv_freq.to(device))
        emb = torch.cat((freqs, freqs), dim=-1)
        return emb.cos()[None, None].to(dtype), emb.sin()[None, None].to(dtype)

    def _mrope(self, position_ids: torch.Tensor, device, dtype):
        """3-axis (T,H,W) m-RoPE cos/sin for an image-conditioned sequence.

        ``position_ids``: ``[3, S]``. Standard (non-interleaved) Qwen2.5-VL
        mrope-section split (ComfyUI ``precompute_freqs_cis`` / HF
        ``apply_multimodal_rotary_pos_emb``): the doubled ``[freq,freq]`` cos/sin
        is cut into 6 chunks sized ``ROPE_DIMS*2`` and chunk ``i`` is read from
        axis ``i % 3`` (T,H,W,T,H,W) — for text-only position ids (all 3 axes
        identical) this is a no-op and reduces to :meth:`_rope`.
        """
        inv_freq = self.inv_freq.to(device)
        freqs = position_ids.to(torch.float32).unsqueeze(-1) * inv_freq  # [3, S, head_dim//2]
        emb = torch.cat((freqs, freqs), dim=-1)  # [3, S, head_dim]
        mrope_section = list(ROPE_DIMS) * 2
        cos = torch.cat([m[i % 3] for i, m in enumerate(emb.cos().split(mrope_section, dim=-1))], dim=-1)
        sin = torch.cat([m[i % 3] for i, m in enumerate(emb.sin().split(mrope_section, dim=-1))], dim=-1)
        return cos[None, None].to(dtype), sin[None, None].to(dtype)  # [1, 1, S, head_dim]

    def forward(self, input_ids, attention_mask=None, inputs_embeds=None, position_ids=None):
        # Text-only default path (inputs_embeds=None, position_ids=None) is
        # UNCHANGED from before vision support existed — bit-identical.
        if inputs_embeds is not None:
            x = inputs_embeds.to(torch.float32)
        else:
            x = self.embed_tokens(input_ids).to(torch.float32)
        b, s, _ = x.shape
        cos, sin = self._mrope(position_ids, x.device, x.dtype) if position_ids is not None else self._rope(s, x.device, x.dtype)

        mask = None
        if attention_mask is not None:
            mask = 1.0 - attention_mask.to(x.dtype).reshape((b, 1, -1, s)).expand(b, 1, s, s)
            mask = mask.masked_fill(mask.to(torch.bool), torch.finfo(x.dtype).min / 4)
        if s > 1:
            causal = torch.empty(s, s, dtype=x.dtype, device=x.device).fill_(torch.finfo(x.dtype).min / 4).triu_(1)
            mask = causal if mask is None else mask + causal

        for layer in self.layers:
            x = layer(x, cos, sin, mask)
        # ComfyUI layer="last" -> final hidden AFTER the final norm.
        return self.norm(x)


class Qwen25VLModel(NativeArchModule):
    """Native arch module: ``model.*`` keys map 1:1 (``lm_head`` always dropped).

    ``self.visual`` (top-level, matching the checkpoint's ``visual.*`` prefix)
    exists ONLY when ``cfg.vision`` is True — a text-only construction has no
    such attribute at all, so its state-dict key set is unchanged from before
    vision support existed.
    """

    def __init__(self, cfg: Qwen25VLConfig, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.cfg = cfg
        self.model = _Qwen25VLTransformer(cfg, operations, device=device, dtype=dtype)
        if cfg.vision:
            self.visual = Qwen2VLVisionTower(
                hidden_size=cfg.vision_hidden_size,
                output_hidden_size=cfg.hidden_size,
                intermediate_size=cfg.vision_intermediate_size,
                num_heads=cfg.vision_num_heads,
                num_layers=cfg.vision_num_layers,
                operations=operations,
                device=device,
                dtype=dtype,
            )

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "Qwen25VLModel":
        return cls(Qwen25VLConfig.from_dict(config), operations)

    def post_load(self) -> None:
        self.model.recompute_inv_freq()

    def forward(self, input_ids, attention_mask=None, inputs_embeds=None, position_ids=None):
        return self.model(input_ids, attention_mask, inputs_embeds=inputs_embeds, position_ids=position_ids)


class Qwen25VLTextEncoder(NativeTextEncoder):
    """Qwen-Image text encoder: prompts -> {"context": [B, S', 3584], "attention_mask": [B, S']}.

    ``S'`` is the sequence with the template prefix stripped. No pooled vector.

    ``encode(texts, images=...)`` additionally accepts images (Qwen-Image-Edit)
    when this encoder was built from a ``vision=True`` load — see
    :meth:`_encode_with_images`. Passing ``images`` to an encoder without a
    vision tower raises rather than silently ignoring them.
    """

    role = "qwen25_vl_7b"

    def __init__(self, module: Qwen25VLModel, tokenizer, variant: str = "qwen25_vl_7b",
                 device: str | torch.device = "cpu") -> None:
        self.module = module
        self.tokenizer = tokenizer
        self.role = variant
        self._device = torch.device(device)
        self._has_vision = hasattr(module, "visual")

    def to(self, device: str | torch.device) -> "Qwen25VLTextEncoder":
        self._device = torch.device(device)
        _module_to(self.module, device)
        return self

    def unload(self) -> None:
        _module_unload(self.module)

    @torch.inference_mode()
    def encode(self, texts: list[str], images: list[torch.Tensor] | None = None) -> dict[str, torch.Tensor]:
        if images:
            return self._encode_with_images(texts, images)
        ids, mask, _prefix_len = self.tokenizer(texts, device=self._device)
        return self._encode_ids(ids, mask)

    def _encode_ids(self, ids, mask) -> dict[str, torch.Tensor]:
        pl = self.tokenizer._prefix_len
        # Run on the full sequence (causal attention sees the system prompt), then
        # drop the template prefix from the output — ComfyUI QwenImageTEModel.
        hidden = self.module(ids, attention_mask=mask)          # [B, S, 3584]
        return {"context": hidden[:, pl:], "attention_mask": mask[:, pl:]}

    def _tokenize_weighted(self, prompt: str):
        ids, mask, weights = self.tokenizer.tokenize_with_weights(prompt, device=self._device)
        pl = self.tokenizer._prefix_len
        return ids, mask, weights[:, pl:]  # strip prefix to align with the stripped context

    def _encode_with_images(self, texts: list[str], images: list[torch.Tensor]) -> dict[str, torch.Tensor]:
        """Splice per-image vision-tower embeddings into the token embedding
        sequence in place of each ``<|image_pad|>`` placeholder, build the
        matching 3-axis m-RoPE position ids, and run the LM on the result.

        Restricted to one prompt per call (batch size 1): the position-id
        construction is inherently per-sequence (ComfyUI's own
        ``Qwen25_7BVLI.forward`` has the same restriction — it builds
        ``position_ids`` shaped ``(3, seq_len)`` with no batch axis at all).

        Caching note (embed_cache.py): this method does not itself cache —
        matching every other native text encoder, caching is the CALLER's
        responsibility (e.g. the ``*ClipTextEncoder`` pipe adapters, via
        ``prompt_embed_key``). A future caller that wants to cache an
        image-conditioned encode MUST fold
        ``embed_cache.image_content_fingerprint(img)`` for every image into
        ``prompt_embed_key``'s ``*parts`` — a key built from the prompt text
        alone would silently alias different images to the same cached
        embedding. No caller does this yet (there is no image-conditioned pipe
        yet), so today's absence of any such caller is itself safe.
        """
        if not self._has_vision:
            raise NativeEngineUnsupportedError(
                "this qwen2.5-vl text encoder has no vision tower loaded; "
                "request the vision-enabled variant at load time "
                "(load_text_encoder(..., vision=True))"
            )
        if len(texts) != 1:
            raise ValueError("image-conditioned encode() supports exactly one prompt per call")

        ids, mask, prefix_len = self.tokenizer(texts, device=self._device, has_image=True)
        pad_id = self.tokenizer.IMAGE_PAD_TOKEN
        pad_positions = (ids[0] == pad_id).nonzero(as_tuple=True)[0].tolist()
        if len(pad_positions) != len(images):
            raise ValueError(
                f"template has {len(pad_positions)} <|image_pad|> slot(s) but got {len(images)} image(s)"
            )

        text_embeds = self.module.model.embed_tokens(ids).to(torch.float32)  # [1, S, H]

        embed_pieces: list[torch.Tensor] = []
        mask_pieces: list[torch.Tensor] = []
        image_spans: list[tuple[int, int, torch.Tensor]] = []
        cursor = 0
        for pad_pos, image in zip(pad_positions, images):
            embed_pieces.append(text_embeds[:, cursor:pad_pos])
            mask_pieces.append(mask[:, cursor:pad_pos])

            # Geometry comes from the loaded tower's own config, not this
            # function's (real-model-shaped) defaults — a differently-shaped
            # vision tower (a tiny synthetic one in tests, or a future variant)
            # must patchify to match it exactly, or the tower's patch_embed sees
            # the wrong tensor shape.
            visual = self.module.visual
            patches, grid_thw = preprocess_qwen_vl_image(
                image.to(self._device),
                patch_size=visual.patch_size,
                temporal_patch_size=visual.patch_embed.temporal_patch_size,
                merge_size=visual.spatial_merge_size,
            )
            vision_embeds = self.module.visual(patches.to(self._device, dtype=torch.float32), grid_thw)
            start = sum(p.shape[1] for p in embed_pieces)
            embed_pieces.append(vision_embeds.unsqueeze(0).to(text_embeds.dtype))
            mask_pieces.append(torch.ones((mask.shape[0], vision_embeds.shape[0]), dtype=mask.dtype, device=mask.device))
            image_spans.append((start, vision_embeds.shape[0], grid_thw))

            cursor = pad_pos + 1
        embed_pieces.append(text_embeds[:, cursor:])
        mask_pieces.append(mask[:, cursor:])

        embeds = torch.cat(embed_pieces, dim=1)
        new_mask = torch.cat(mask_pieces, dim=1)
        position_ids = qwen25vl_mrope_position_ids(image_spans, embeds.shape[1], embeds.device)

        hidden = self.module(None, attention_mask=new_mask, inputs_embeds=embeds, position_ids=position_ids)
        # Splicing only ever inserts tokens AFTER the prefix boundary (every
        # <|image_pad|> sits inside the user turn, past the system+"user\n"
        # prefix), so `prefix_len` — computed on the PRE-splice sequence —
        # still points at the correct boundary in the spliced one.
        return {"context": hidden[:, prefix_len:], "attention_mask": new_mask[:, prefix_len:]}
