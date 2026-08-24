"""End-to-end dtype-boundary test: a bf16 AR core's ``frame_hiddens`` handed
to a fp16 flow-matching DiT -- the real checkpoint combination (pruned/full
text-encoder repacks ship all-bf16; the DiT checkpoint ships fp16).

Confirms the seam the generator pipe relies on (``flow.py``'s
``model.encode_condition(...).to(dtype)``, and inside ``encode_condition``
itself, ``_cast_to`` re-aligning the activation to the condition-encoder
Conv1d's own weight dtype) tolerates the AR stage's output arriving in a
DIFFERENT float dtype than the DiT's own compute dtype -- the two stages are
never resident together (module docstring, ``audio_minimax_music3/main.py``),
so nothing upstream of this seam guarantees they match.
"""

from __future__ import annotations

import torch

from src.platform.runtime.native.arch.minimax_music3 import ar_loop, flow
from src.platform.runtime.native.arch.minimax_music3.config import MiniMaxMusic3TextEncoderConfig
from src.platform.runtime.native.arch.minimax_music3.lm import MiniMaxMusic3AudioLM
from src.platform.runtime.native.arch.minimax_music3.model import MINIMAX_MUSIC3_DIT, MiniMaxMusic3Model
from vendor.gpl.comfyui.ops import disable_weight_init, pick_operations

_HIDDEN_SIZE = 16  # must match the DiT's condition_hidden_dim -- see the docstring below.


def _tiny_lm_config(pruned: bool) -> MiniMaxMusic3TextEncoderConfig:
    return MiniMaxMusic3TextEncoderConfig(
        hidden_size=_HIDDEN_SIZE, intermediate_size=24, num_layers=2, head_dim=8,
        num_attention_heads=2, num_key_value_heads=1, rope_theta=10000.0,
        rms_norm_eps=1e-6, max_position_embeddings=64,
        decoder_intermediate_size=20, decoder_num_layers=2, decoder_num_heads=2, decoder_head_dim=8,
        audio_vocab_size=6, num_codebooks=8,
        merged_qkv=pruned, merged_mlp=pruned, decoder_merged_qkv=pruned, decoder_merged_mlp=pruned,
        pruned_embeddings=pruned, pruned_lm_head=pruned,
    )


def _build_bf16_lm(pruned: bool, seed: int) -> MiniMaxMusic3AudioLM:
    lm = MiniMaxMusic3AudioLM(_tiny_lm_config(pruned), disable_weight_init, dtype=torch.bfloat16)
    g = torch.Generator().manual_seed(seed)
    with torch.no_grad():
        for p in lm.parameters():
            p.copy_((torch.randn(p.shape, generator=g) * 0.5).to(p.dtype))
    lm.post_load()
    lm.eval()
    return lm


def _tiny_dit_config() -> dict:
    return dict(
        image_model=MINIMAX_MUSIC3_DIT,
        # condition_hidden_dim MUST equal the LM's hidden_size: frame_hiddens'
        # last dim is `num_condition_layers(8) * hidden_size` (1 LLM + 7 depth
        # slots, ar_loop.FRAME_HIDDEN_SIZE's own contract), and
        # `encode_condition` reshapes it as `(num_condition_layers,
        # condition_hidden_dim)`.
        in_channels=4, condition_dim=6, condition_hidden_dim=_HIDDEN_SIZE, num_condition_layers=8,
        num_layers=2, num_attention_heads=2, attention_head_dim=4, ffn_inner_dim=6,
        rotary_dim=2, fourier_dim=8,
    )


def _build_fp16_dit(seed: int) -> MiniMaxMusic3Model:
    """Mirrors the real loader's contract: ``from_config`` builds the module
    shape (no real dtype yet -- ``dtype=None`` construction defaults to
    fp32), and the checkpoint's actual dtype only lands via
    ``load_state_dict(..., assign=True)`` (``assign=True`` REPLACES each
    parameter with the loaded tensor, dtype included -- a plain ``.copy_()``
    would instead keep the placeholder fp32 the parameter was constructed
    with, silently defeating this test's whole point)."""
    ops = pick_operations(torch.float16, torch.float16)
    m = MiniMaxMusic3Model.from_config(_tiny_dit_config(), ops)
    g = torch.Generator().manual_seed(seed)
    sd = {
        k: (torch.randn(v.shape, generator=g) * 0.02).to(torch.float16)
        for k, v in m.state_dict().items()
    }
    m.load_state_dict(sd, strict=True, assign=True)
    m.requires_grad_(False)
    m.post_load()
    m.eval()
    return m


def test_bf16_ar_output_feeds_the_fp16_dit_without_a_dtype_crash():
    lm = _build_bf16_lm(pruned=True, seed=31)
    ids = torch.randint(0, 500, (2, 4), generator=torch.Generator().manual_seed(7))
    frame_hiddens = ar_loop.generate(lm, ids, torch.Generator().manual_seed(1), max_frames=6)
    assert frame_hiddens.dtype == torch.bfloat16
    assert frame_hiddens.shape[1] > 0, "need at least one kept frame to exercise the DiT handoff"

    dit = _build_fp16_dit(seed=32)
    latent_chunks = flow.denoise_windowed(
        dit, frame_hiddens, steps=2, cfg_scale=1.7,
        generator=torch.Generator().manual_seed(4), device=torch.device("cpu"), dtype=torch.float16,
    )
    assert len(latent_chunks) == 1
    assert latent_chunks[0].dtype == torch.float16
    assert torch.isfinite(latent_chunks[0]).all()
