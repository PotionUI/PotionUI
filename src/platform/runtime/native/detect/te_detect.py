"""Text-encoder architecture detection from a raw state dict.

v1 distinguishes CLIP-L, T5-XXL and Qwen3 (4B / 8B). Detection keys and shapes
were read from the real local checkpoints:

    clip_l.safetensors        text_model.*   token_embedding [49408, 768]   12 layers
    t5xxl_*_scaled            shared.weight [32128, 4096] + encoder.block.*  24 blocks
    qwen_3_4b                 model.* , q_norm present, embed_tokens [151936, 2560]
    qwen_3_8b                 model.* , q_norm present, embed_tokens [151936, 4096]

Config dict schema:
    te_type:     "clip_l" | "t5xxl" | "qwen3"
    variant:     "clip_l" | "t5xxl" | "qwen3_4b" | "qwen3_8b"
    hidden_size: int
    num_layers:  int
    vocab_size:  int
    scaled_fp8:  bool     -- ComfyUI top-level `scaled_fp8` marker tensor present
"""

from __future__ import annotations

import logging

import torch

from ..io.state_dict_utils import count_blocks

logger = logging.getLogger(__name__)


def _base(sd: dict[str, torch.Tensor]) -> dict:
    return {"scaled_fp8": "scaled_fp8" in sd}


def detect_te_config(sd: dict[str, torch.Tensor]) -> dict | None:
    """Return a text-encoder config dict, else ``None`` if unrecognised."""
    config = _base(sd)

    # --- CLIP-L: HuggingFace CLIPTextModel layout ---------------------------
    if "text_model.embeddings.token_embedding.weight" in sd:
        emb = sd["text_model.embeddings.token_embedding.weight"]
        config.update(
            te_type="clip_l",
            variant="clip_l",
            vocab_size=int(emb.shape[0]),
            hidden_size=int(emb.shape[1]),
            num_layers=count_blocks(sd, "text_model.encoder.layers.{}."),
        )
        logger.debug("detected CLIP-L: hidden=%d layers=%d", config["hidden_size"], config["num_layers"])
        return config

    # --- UMT5-XXL (Wan-native layout): token_embedding + blocks.N.attn.* -----
    # The original Wan/mmgp UMT5 dump uses its own key names; the loader remaps
    # them to the ComfyUI T5 layout before build. Distinguished from T5 by the
    # `blocks.N.attn.q` naming; per-block `pos_embedding` == per-layer bias.
    if "token_embedding.weight" in sd and "blocks.0.attn.q.weight" in sd:
        emb = sd["token_embedding.weight"]
        config.update(
            te_type="umt5",
            variant="umt5_xxl",
            format="wan_native",
            vocab_size=int(emb.shape[0]),
            hidden_size=int(emb.shape[1]),
            num_layers=count_blocks(sd, "blocks.{}."),
        )
        logger.debug("detected UMT5-XXL (wan_native): hidden=%d blocks=%d", config["hidden_size"], config["num_layers"])
        return config

    # --- T5-XXL / UMT5-XXL (ComfyUI layout): `shared` + encoder.block.* ------
    if "shared.weight" in sd and "encoder.block.0.layer.0.SelfAttention.q.weight" in sd:
        emb = sd["shared.weight"]
        vocab = int(emb.shape[0])
        # UMT5 has the multilingual 256k vocab and a per-layer relative bias;
        # T5-XXL has the 32k vocab and shares block-0's bias.
        is_umt5 = vocab >= 200000
        config.update(
            te_type="umt5" if is_umt5 else "t5xxl",
            variant="umt5_xxl" if is_umt5 else "t5xxl",
            vocab_size=vocab,
            hidden_size=int(emb.shape[1]),
            num_layers=count_blocks(sd, "encoder.block.{}."),
        )
        if is_umt5:
            config["format"] = "comfy_t5"
        logger.debug("detected %s: hidden=%d blocks=%d", config["te_type"], config["hidden_size"], config["num_layers"])
        return config

    # --- Gemma4-Unified-12B (LTX-2.5 TE): model.* with the SAME 4-norm block -
    # A flat Gemma4 file also carries `model.embed_tokens.weight` +
    # `model.layers.0.pre_feedforward_layernorm.weight` (Comfy's own flattening
    # convention reuses gemma3's key names for the LM), so this branch MUST run
    # BEFORE the gemma3 branch below or a gemma4 file misdetects as gemma3.
    # `layer_scalar` is the discriminator: transformers' `Gemma4TextDecoderLayer`
    # unconditionally registers it as a *persistent* buffer (`torch.ones(1)`) on
    # every layer, in both the dense and unified variants, regardless of whether
    # the checkpoint also carries vision/audio towers — gemma3 has no such key.
    # Also tolerates the checkpoint's optional vision/audio towers, at either the
    # Comfy names (`vision_model.*` / `multi_modal_projector.*` / `audio_projector.*`
    # — same prefixes gemma3's own SigLIP tower uses) or the legacy HF names
    # (`model.vision_embedder.*` / `model.embed_vision.*` / `model.embed_audio.*`);
    # detection itself never inspects them.
    if (
        "model.embed_tokens.weight" in sd
        and "model.layers.0.pre_feedforward_layernorm.weight" in sd
        and "model.layers.0.layer_scalar" in sd
    ):
        emb = sd["model.embed_tokens.weight"]
        config.update(
            te_type="gemma4",
            variant="gemma4_12b",
            vocab_size=int(emb.shape[0]),
            hidden_size=int(emb.shape[1]),
            num_layers=count_blocks(sd, "model.layers.{}."),
        )
        logger.debug("detected Gemma4-Unified-12B: hidden=%d layers=%d", config["hidden_size"], config["num_layers"])
        return config

    # --- Gemma3-12B (LTX-2 TE): model.* with the gemma3 4-norm block --------
    # gemma3 also has q_norm, so it must be checked BEFORE the qwen3 branch; the
    # `pre_feedforward_layernorm` (a gemma3-only 4th norm) is the discriminator.
    if (
        "model.embed_tokens.weight" in sd
        and "model.layers.0.pre_feedforward_layernorm.weight" in sd
    ):
        emb = sd["model.embed_tokens.weight"]
        config.update(
            te_type="gemma3",
            variant="gemma3_12b",
            vocab_size=int(emb.shape[0]),
            hidden_size=int(emb.shape[1]),
            num_layers=count_blocks(sd, "model.layers.{}."),
        )
        logger.debug("detected Gemma3-12B: hidden=%d layers=%d", config["hidden_size"], config["num_layers"])
        return config

    # --- MiniMax-Music3 (fused Qwen3-8B global LLM + RVQ depth decoder + ------
    # --- embedded tokenizer): model.audio_decoder.* + tokenizer_json --------
    # MUST run before the plain qwen3 branch below: the checkpoint's *full*
    # (un-pruned) layout has `model.embed_tokens.weight` and per-head
    # `self_attn.q_norm.weight` too -- structurally indistinguishable from a
    # bare Qwen3-8B checkpoint by that branch's own condition alone. The
    # `audio_decoder.norm.weight` key is unique to Music3 among every native
    # family (H3's audio path has no `audio_decoder` namespace at all).
    if "model.audio_decoder.norm.weight" in sd and "tokenizer_json" in sd:
        hidden = int(sd["model.audio_decoder.norm.weight"].shape[0])
        merged_qkv = "model.layers.0.self_attn.qkv_proj.weight" in sd
        merged_mlp = "model.layers.0.mlp.gate_up_proj.weight" in sd
        decoder_merged_qkv = "model.audio_decoder.layers.0.self_attn.qkv_proj.weight" in sd
        decoder_merged_mlp = "model.audio_decoder.layers.0.mlp.gate_up_proj.weight" in sd
        pruned_embeddings = "model.embed_tokens_prefill.weight" in sd
        pruned_lm_head = "model.lm_head_pruned.weight" in sd
        intermediate_size = (
            int(sd["model.layers.0.mlp.gate_up_proj.weight"].shape[0]) // 2
            if merged_mlp
            else int(sd["model.layers.0.mlp.gate_proj.weight"].shape[0])
        )
        decoder_intermediate_size = int(sd["model.audio_decoder.layers.0.mlp.down_proj.weight"].shape[1])
        # `audio_extra_embedding` is an nn.Embedding table: [7 residual
        # codebooks * audio_vocab_size, hidden_size] -- its SECOND dim is
        # hidden_size, not audio_vocab_size. The classifier head's OUT width
        # is the actual audio_vocab_size; the embedding table's row count then
        # gives the residual codebook count (+1 for the semantic codebook the
        # LLM's own lm_head samples).
        audio_vocab_size = int(sd["model.audio_decoder.audio_heads.0.weight"].shape[0])
        num_extra_rows = int(sd["model.audio_extra_embedding.weight"].shape[0])
        config.update(
            te_type="minimax_music3",
            variant="minimax_music3",
            hidden_size=hidden,
            intermediate_size=intermediate_size,
            num_layers=count_blocks(sd, "model.layers.{}."),
            head_dim=int(sd["model.layers.0.self_attn.q_norm.weight"].shape[0]),
            decoder_intermediate_size=decoder_intermediate_size,
            decoder_num_layers=count_blocks(sd, "model.audio_decoder.layers.{}."),
            audio_vocab_size=audio_vocab_size,
            num_codebooks=num_extra_rows // audio_vocab_size + 1,
            merged_qkv=merged_qkv,
            merged_mlp=merged_mlp,
            decoder_merged_qkv=decoder_merged_qkv,
            decoder_merged_mlp=decoder_merged_mlp,
            pruned_embeddings=pruned_embeddings,
            pruned_lm_head=pruned_lm_head,
        )
        logger.debug(
            "detected minimax_music3 TE: hidden=%d layers=%d merged_qkv=%s merged_mlp=%s "
            "decoder_merged_qkv=%s decoder_merged_mlp=%s pruned_embeddings=%s pruned_lm_head=%s",
            hidden, config["num_layers"], merged_qkv, merged_mlp,
            decoder_merged_qkv, decoder_merged_mlp, pruned_embeddings, pruned_lm_head,
        )
        return config

    # --- Qwen3 (LLM text encoder): model.* with per-head QK-norm ------------
    if "model.embed_tokens.weight" in sd and "model.layers.0.self_attn.q_norm.weight" in sd:
        emb = sd["model.embed_tokens.weight"]
        hidden = int(emb.shape[1])
        # A vision tower marks the vision-language variant. Its language model
        # is an ordinary Qwen3, so it loads through the same arch — only the
        # extraction contract differs. Two checkpoints carry the tower at TWO
        # DIFFERENT prefixes: Qwen3-VL-4B (Krea-2) nests it under `model.visual.*`;
        # the Comfy-Org MiniMax-H3 repack of Qwen3-VL-32B carries it top-level as
        # `visual.*` (sibling of `model.*`, like Qwen2.5-VL's tower) — verified
        # against the real repack's safetensors header (ai/minimax_h3/te_bf16_header.json).
        # Both must be checked or the 32B tower is silently invisible to detection.
        has_nested_vision = any(k.startswith("model.visual.") for k in sd)
        has_toplevel_vision = any(k.startswith("visual.") for k in sd)
        has_vision = has_nested_vision or has_toplevel_vision
        if has_vision:
            te_type = "qwen3vl"
            # Width branch: hidden 2560 -> Krea-2's Qwen3-VL-4B; hidden 5120 ->
            # MiniMax-H3's Qwen3-VL-32B TE. Widths don't overlap between the two
            # known checkpoints, so the threshold is safe.
            variant = "qwen3vl_32b" if hidden >= 5120 else "qwen3vl_4b"
        else:
            te_type = "qwen3"
            # 0.6B: hidden EXACTLY 1024, 28 layers (Anima's TE — its own head/MLP
            # sizes, recovered from shapes in loader._build_config). Matched on the
            # exact width so it never claims an unrelated small Qwen3. 4B: hidden
            # 2560, 8B: hidden 4096 (both 36 layers, same vocab).
            if hidden == 1024:
                variant = "qwen3_06b"
            else:
                variant = "qwen3_4b" if hidden <= 2560 else "qwen3_8b"
        config.update(
            te_type=te_type,
            variant=variant,
            vocab_size=int(emb.shape[0]),
            hidden_size=hidden,
            num_layers=count_blocks(sd, "model.layers.{}."),
            # Structural truth, not a variant guess: MiniMax-H3's real
            # Comfy-Org trimmed repack has no `model.norm.weight` key at all
            # (verified against both its bf16 and nvfp4_awq headers) — every
            # other known Qwen3-family checkpoint (Klein/Krea-2/Z-Image/Anima)
            # has it, so this stays True for them by construction, not by
            # special-casing this variant.
            has_final_norm="model.norm.weight" in sd,
        )
        if has_vision:
            # Where the loader must attach/strip the tower and read its shapes from.
            config["vision_top_level"] = has_toplevel_vision
        logger.debug("detected %s %s: hidden=%d layers=%d", te_type, variant, hidden, config["num_layers"])
        return config

    # --- Qwen2.5-VL (Qwen-Image LM): model.* with q/k/v bias, NO q/k norm -----
    # Distinguished from Qwen3/Qwen3-VL by the absence of q_norm and the presence
    # of attention biases. Its vision tower is top-level ``visual.*`` (Qwen3-VL's
    # is ``model.visual.*``) and is dropped at load.
    if (
        "model.embed_tokens.weight" in sd
        and "model.layers.0.self_attn.q_proj.bias" in sd
        and "model.layers.0.self_attn.q_norm.weight" not in sd
    ):
        emb = sd["model.embed_tokens.weight"]
        config.update(
            te_type="qwen25_vl",
            variant="qwen25_vl_7b",
            vocab_size=int(emb.shape[0]),
            hidden_size=int(emb.shape[1]),
            num_layers=count_blocks(sd, "model.layers.{}."),
        )
        logger.debug("detected qwen25_vl: hidden=%d layers=%d", config["hidden_size"], config["num_layers"])
        return config

    return None
