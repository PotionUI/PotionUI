import logging
import math
import re
from typing import List, Tuple, Any, Optional, Dict

import torch
from diffusers.loaders import TextualInversionLoaderMixin
from transformers import CLIPTokenizer

from src.platform.runtime.primitives.clip import ClipTextEncoder, ConditioningModel
from src.pipelines.models import BaseModel


def resize_embeddings_for_sdxl(embedding_tensor: torch.Tensor, target_dim=768) -> torch.Tensor:
    """
    Resize embeddings to match SDXL's expected dimensions:
    - If 2D, mean across dimension 0
    - Truncate or pad to match target_dim
    """
    if len(embedding_tensor.shape) == 2:
        embedding_tensor = embedding_tensor.mean(dim=0)

    if embedding_tensor.shape[0] != target_dim:
        if embedding_tensor.shape[0] > target_dim:
            embedding_tensor = embedding_tensor[:target_dim]
        else:
            padded = torch.zeros(target_dim, device=embedding_tensor.device, dtype=embedding_tensor.dtype)
            padded[:embedding_tensor.shape[0]] = embedding_tensor
            embedding_tensor = padded

    return embedding_tensor


class SDXLClipTextEncoder(ClipTextEncoder):
    """
    SDXL-specific CLIP text encoder implementation with attention weighting support.
    """

    # Store typical BOS/EOS for SDXL
    BASE_MODEL_CONFIG = {
        BaseModel.SDXL: {
            'bos_token_id': 49406,
            'eos_token_id': 49407,
            # dimension for the first text encoder (commonly 768 in SDXL)
            'text_encoder_dim': 768,
            # dimension for the second text encoder (commonly 1280 in SDXL)
            'text_encoder_2_dim': 1280,
        },
    }

    def __init__(
            self,
            pipe: Any,
            base_model: BaseModel,
            text_encoder: Any,
            text_encoder_2: Any,
            tokenizer: CLIPTokenizer,
            tokenizer_2: CLIPTokenizer,
            clip_skip: int,
            device: str = "cuda",
    ):
        # Basic assignments
        self.pipe = pipe
        self.base_model = base_model
        self.text_encoder = text_encoder
        self.text_encoder_2 = text_encoder_2
        self.tokenizer = tokenizer
        self.tokenizer_2 = tokenizer_2
        self.device = device
        self.clip_skip = clip_skip

        # Set BOS/EOS tokens based on base model config
        self.bos_token_id = self.BASE_MODEL_CONFIG[base_model]['bos_token_id']
        self.eos_token_id = self.BASE_MODEL_CONFIG[base_model]['eos_token_id']

        # Pre-cache the expected embedding dims (helpful if we do textual inversion)
        self.text_encoder_dim = self.BASE_MODEL_CONFIG[base_model]['text_encoder_dim']
        self.text_encoder_2_dim = self.BASE_MODEL_CONFIG[base_model]['text_encoder_2_dim']

    @staticmethod
    def parse_prompt_attention(text: str) -> List[Tuple[str, float]]:
        """
        Parse prompt attention weights using the same logic as Automatic1111-like syntax:
        - Weighted parenthesis: (word:1.3)
        - Square bracket attention: [word]
        - Possibly multiple grouping or 'BREAK' tokens, etc.
        """
        re_attention = re.compile(
            r"""
            \\\(|\\\)|\\\[|\\\]|\\\\|\\||\(|\[|:([+-]?[.\d]+)\)|
            \)|]|[^\\()\[\]:]+|:
            """,
            re.X,
        )
        re_break = re.compile(r"\s*\bBREAK\b\s*", re.S)

        round_brackets = []
        square_brackets = []
        round_bracket_multiplier = 1.1
        square_bracket_multiplier = 1 / 1.1

        def multiply_range(results, start_position, multiplier):
            for p in range(start_position, len(results)):
                results[p][1] *= multiplier

        res = []

        for m in re_attention.finditer(text):
            chunk = m.group(0)
            weight = m.group(1)  # the numeric weight inside :(...)

            if chunk.startswith("\\"):
                # Escaped character
                res.append([chunk[1:], 1.0])
            elif chunk == "(":
                round_brackets.append(len(res))
            elif chunk == "[":
                square_brackets.append(len(res))
            elif weight is not None and len(round_brackets) > 0:
                multiply_range(res, round_brackets.pop(), float(weight))
            elif chunk == ")" and len(round_brackets) > 0:
                multiply_range(res, round_brackets.pop(), round_bracket_multiplier)
            elif chunk == "]" and len(square_brackets) > 0:
                multiply_range(res, square_brackets.pop(), square_bracket_multiplier)
            else:
                parts = re.split(re_break, chunk)
                for i, part in enumerate(parts):
                    if i > 0:
                        res.append(["BREAK", -1])
                    res.append([part, 1.0])

        # Unclosed brackets
        for pos in round_brackets:
            multiply_range(res, pos, round_bracket_multiplier)
        for pos in square_brackets:
            multiply_range(res, pos, square_bracket_multiplier)

        # Handle empty prompt
        if not res:
            res = [["", 1.0]]

        # Merge consecutive tokens with identical weights
        i = 0
        while i + 1 < len(res):
            if res[i][1] == res[i + 1][1]:
                res[i][0] += res[i + 1][0]
                res.pop(i + 1)
            else:
                i += 1

        return res

    def get_prompts_tokens_with_weights(
            self,
            tokenizer: CLIPTokenizer,
            prompt: str
    ) -> Tuple[List[int], List[float]]:
        """
        Tokenize the prompt text, applying non-linear weighting for A1111-like bracket syntax.
        """
        texts_and_weights = self.parse_prompt_attention(prompt)
        text_tokens, text_weights = [], []

        for word, weight in texts_and_weights:
            # Non-linear transform of the bracket-based weight
            if weight < 0:
                weight = 1.0

            transformed_weight = math.pow(weight, 1.2) if weight > 1 else math.pow(weight, 0.8)

            # Tokenize
            token_ids = tokenizer(word, truncation=False).input_ids
            # Typically, the first token is BOS and last is EOS in standard CLIP-based tokenizers
            # so we remove them [1:-1]. But be sure your tokenizer actually does that.
            # If not, adjust accordingly.
            if len(token_ids) > 2:
                token_ids = token_ids[1:-1]

            # Extend
            text_tokens.extend(token_ids)
            text_weights.extend([transformed_weight] * len(token_ids))

        return text_tokens, text_weights

    def group_tokens_and_weights(
            self,
            token_ids: List[int],
            weights: List[float],
            pad_last_block: bool = False
    ) -> Tuple[List[List[int]], List[List[float]]]:
        """
        Group tokens into 75-sized chunks, adding BOS/EOS (making 77).
        If `pad_last_block` is True, pad the last chunk to length 75 before adding EOS.
        """
        bos, eos = self.bos_token_id, self.eos_token_id

        new_token_ids = []
        new_weights = []

        while len(token_ids) >= 75:
            head_75_tokens = [token_ids.pop(0) for _ in range(75)]
            head_75_weights = [weights.pop(0) for _ in range(75)]

            temp_77_token_ids = [bos] + head_75_tokens + [eos]
            temp_77_weights = [1.0] + head_75_weights + [1.0]

            new_token_ids.append(temp_77_token_ids)
            new_weights.append(temp_77_weights)

        if len(token_ids) > 0:
            padding_len = 75 - len(token_ids) if pad_last_block else 0
            # Add the leftover tokens plus optional padding
            temp_77_token_ids = [bos] + token_ids + [eos] * padding_len + [eos]
            temp_77_weights = [1.0] + weights + [1.0] * padding_len + [1.0]

            new_token_ids.append(temp_77_token_ids)
            new_weights.append(temp_77_weights)

        return new_token_ids, new_weights

    def _load_textual_inversion_embeddings(
            self,
            embedding_files: Dict[str, str],
    ):
        """
        Load textual inversion embeddings from file(s) and resize for text_encoder_1 & text_encoder_2.
        Then register them in the tokenizers and text encoders.
        """
        from src.platform.observability.logger import logger

        if not embedding_files:
            logger.debug("[CLIP] No textual inversion embeddings to load")
            return  # Nothing to do

        logger.debug(f"[CLIP] Loading {len(embedding_files)} textual inversion embeddings")

        # Because repeated calls to add_tokens + resize can be expensive,
        # collect all new tokens first, then do one resize call at the end for each encoder.
        new_tokens = []
        embeddings_1 = {}
        embeddings_2 = {}

        for token, file_path in embedding_files.items():
            logger.debug(f"[CLIP] Loading embedding '{token}' from: {file_path}")

            # Check if file exists
            import os
            if not os.path.exists(file_path):
                logger.error(f"[CLIP] Embedding file not found: {file_path}")
                continue

            try:
                # 1) Load from safetensors or .pt
                if file_path.endswith('.pt'):
                    embedding_data = torch.load(file_path, map_location=self.device)
                else:
                    from safetensors.torch import load_file
                    embedding_data = load_file(file_path, device=self.device)
            except Exception as e:
                logger.error(f"[CLIP] Failed to load embedding '{token}': {e}")
                continue

            # 2) Extract the actual embedding tensor
            if isinstance(embedding_data, dict):
                if "string_to_param" in embedding_data:
                    embedding_tensor = embedding_data["string_to_param"]["*"]
                elif "emb_params" in embedding_data:
                    embedding_tensor = embedding_data["emb_params"]
                else:
                    # fallback
                    embedding_tensor = None
                    for v in embedding_data.values():
                        if isinstance(v, torch.Tensor):
                            embedding_tensor = v
                            break
            else:
                embedding_tensor = embedding_data

            if embedding_tensor is None:
                raise ValueError(f"Cannot find tensor in embedding file: {file_path}")

            # 3) Resize for the first and second text encoder
            embedding_1 = resize_embeddings_for_sdxl(embedding_tensor, self.text_encoder_dim)
            embedding_2 = resize_embeddings_for_sdxl(embedding_tensor, self.text_encoder_2_dim)

            # 4) Store them for adding after we add tokens to tokenizer
            new_tokens.append(token)
            embeddings_1[token] = embedding_1
            embeddings_2[token] = embedding_2
            logger.debug(f"[CLIP] Successfully loaded embedding '{token}' (dim1={embedding_1.shape[0]}, dim2={embedding_2.shape[0]})")

        # 5) Add all tokens to the tokenizers at once
        logger.debug(f"[CLIP] Adding {len(new_tokens)} new tokens to tokenizers: {new_tokens}")
        self.tokenizer.add_tokens(new_tokens)
        self.tokenizer_2.add_tokens(new_tokens)

        # 6) Resize text encoders
        self.text_encoder.resize_token_embeddings(len(self.tokenizer), mean_resizing=False)
        self.text_encoder_2.resize_token_embeddings(len(self.tokenizer_2), mean_resizing=False)

        # 7) Assign the embeddings
        for token in new_tokens:
            token_id = self.tokenizer.convert_tokens_to_ids(token)
            token_id_2 = self.tokenizer_2.convert_tokens_to_ids(token)

            self.text_encoder.get_input_embeddings().weight.data[token_id] = embeddings_1[token]
            self.text_encoder_2.get_input_embeddings().weight.data[token_id_2] = embeddings_2[token]
            logger.debug(f"[CLIP] Assigned embedding '{token}' to token IDs: {token_id} (encoder1), {token_id_2} (encoder2)")

        logger.debug(f"[CLIP] Successfully loaded and assigned {len(new_tokens)} textual inversion embeddings")

    @torch.no_grad()
    def encode_prompt(
            self,
            prompt: str,
            negative_prompt: str,
            num_images_per_prompt: int = 1,
            do_classifier_free_guidance: bool = True,
            embedding_files: Optional[Dict[str, str]] = None,
    ) -> ConditioningModel:
        """
        Main entry point:
        1) Load textual inversion embeddings (if any).
        2) Tokenize prompts and negative prompts.
        3) Group tokens into 77-length chunks (with BOS/EOS).
        4) Encode them via text_encoder and text_encoder_2 (SDXL).
        5) Return a ConditioningModel with final embeddings.
        """
        # 1) Handle textual inversion embeddings if provided
        if embedding_files:
            self._load_textual_inversion_embeddings(embedding_files)

        # 2) Handle textual inversion conversion if needed
        if isinstance(self.pipe, TextualInversionLoaderMixin):
            prompt = self.pipe.maybe_convert_prompt(prompt, self.tokenizer)
            negative_prompt = self.pipe.maybe_convert_prompt(negative_prompt, self.tokenizer)

        # 4) Tokenize and get weights
        prompt_tokens_1, prompt_weights_1 = self.get_prompts_tokens_with_weights(self.tokenizer, prompt)
        neg_tokens_1, neg_weights_1 = self.get_prompts_tokens_with_weights(self.tokenizer, negative_prompt)

        prompt_tokens_2, prompt_weights_2 = self.get_prompts_tokens_with_weights(self.tokenizer_2, prompt)
        neg_tokens_2, neg_weights_2 = self.get_prompts_tokens_with_weights(self.tokenizer_2, negative_prompt)

        # 5) Group into 77-blocks
        prompt_groups_1, weights_groups_1 = self.group_tokens_and_weights(prompt_tokens_1[:], prompt_weights_1[:], pad_last_block=True)
        neg_groups_1, neg_w_groups_1 = self.group_tokens_and_weights(neg_tokens_1[:], neg_weights_1[:], pad_last_block=True)
        prompt_groups_2, weights_groups_2 = self.group_tokens_and_weights(prompt_tokens_2[:], prompt_weights_2[:], pad_last_block=True)
        neg_groups_2, neg_w_groups_2 = self.group_tokens_and_weights(neg_tokens_2[:], neg_weights_2[:], pad_last_block=True)

        # 5.5) Equalize chunk counts (ComfyUI behavior): pad the shorter prompt
        # with all-pad chunks so both sides encode through the same multi-chunk
        # path. This replaces an earlier workaround that re-encoded the shorter
        # side as a single 77-token pass and tile-repeated it — that produced
        # embeddings with wildly different statistics (cosine ~0.5 vs padded
        # encoding) and lost prompt weighting on the short side.
        def _pad_chunks(groups, wgroups, target_n):
            while len(groups) < target_n:
                groups.append([self.bos_token_id] + [self.eos_token_id] * 76)
                wgroups.append([1.0] * 77)

        n_chunks = max(len(prompt_groups_1), len(neg_groups_1))
        _pad_chunks(prompt_groups_1, weights_groups_1, n_chunks)
        _pad_chunks(prompt_groups_2, weights_groups_2, n_chunks)
        _pad_chunks(neg_groups_1, neg_w_groups_1, n_chunks)
        _pad_chunks(neg_groups_2, neg_w_groups_2, n_chunks)

        # 8) Encode positive and negative chunks independently
        from src.platform.observability.logger import logger

        # Determine CLIP skip layer index
        if self.clip_skip is None or self.clip_skip == 0 or self.clip_skip <= 2:
            layer_idx = -2
        else:
            layer_idx = -(self.clip_skip - 1)
        logger.debug(f"[CLIP] Using clip_skip={self.clip_skip}, layer={layer_idx}")

        def _encode_groups(groups_1, wgroups_1, groups_2):
            """Encode a list of token groups and return (embeds_list, pooled)."""
            embeds = []
            pooled = None
            for i in range(len(groups_1)):
                t1 = torch.tensor([groups_1[i]], device=self.device, dtype=torch.long)
                w1 = torch.tensor(wgroups_1[i], device=self.device, dtype=torch.float16)
                t2 = torch.tensor([groups_2[i]], device=self.device, dtype=torch.long)

                out1 = self.text_encoder(t1, output_hidden_states=True)
                out2 = self.text_encoder_2(t2, output_hidden_states=True)
                # Pooled embedding comes from the FIRST chunk (ComfyUI
                # behavior) — it carries the global style/quality conditioning,
                # which lives at the start of the prompt, not in the overflow.
                if pooled is None:
                    pooled = out2[0]

                he1 = out1.hidden_states[layer_idx]
                he2 = out2.hidden_states[layer_idx]
                emb = torch.cat([he1, he2], dim=-1).squeeze(0)

                # Apply A1111-style weighting
                for j in range(len(w1)):
                    w = w1[j]
                    if w != 1.0 and not (torch.isnan(w) or torch.isinf(w)):
                        emb[j] = emb[-1] + (emb[j] - emb[-1]) * w

                embeds.append(emb.unsqueeze(0))
            return embeds, pooled

        prompt_embeds, pooled_prompt_embeds = _encode_groups(
            prompt_groups_1, weights_groups_1, prompt_groups_2)
        neg_embeds, negative_pooled_prompt_embeds = _encode_groups(
            neg_groups_1, neg_w_groups_1, neg_groups_2)

        # 9) Final concatenation
        prompt_embeds = torch.cat(prompt_embeds, dim=1)  # [1, total_seq, dim]
        negative_prompt_embeds = torch.cat(neg_embeds, dim=1)

        # Log embedding magnitudes for debugging - no normalization needed
        # Illustrious and other anime models naturally produce larger embeddings
        # ComfyUI/Forge don't normalize and work fine.
        # Gated on DEBUG: the reductions force GPU syncs on every encode.
        from src.platform.observability.logger import logger
        if logger.isEnabledFor(logging.DEBUG):
            pos_max = prompt_embeds.abs().max().item()
            neg_max = negative_prompt_embeds.abs().max().item()
            logger.debug(f"[CLIP] Embeddings - pos_max={pos_max:.2f}, neg_max={neg_max:.2f}")

        # Repeat for batch size
        bs_embed, seq_len, hidden_dim = prompt_embeds.shape
        prompt_embeds = prompt_embeds.repeat(1, num_images_per_prompt, 1)  # [1, total_seq * k, dim]
        prompt_embeds = prompt_embeds.view(bs_embed * num_images_per_prompt, seq_len, hidden_dim)

        # Negative
        seq_len_neg = negative_prompt_embeds.shape[1]
        negative_prompt_embeds = negative_prompt_embeds.repeat(1, num_images_per_prompt, 1)
        negative_prompt_embeds = negative_prompt_embeds.view(
            bs_embed * num_images_per_prompt, seq_len_neg, hidden_dim
        )

        # Pooled
        pooled_prompt_embeds = pooled_prompt_embeds.repeat(1, num_images_per_prompt, 1).view(
            bs_embed * num_images_per_prompt, -1
        )
        negative_pooled_prompt_embeds = negative_pooled_prompt_embeds.repeat(1, num_images_per_prompt, 1).view(
            bs_embed * num_images_per_prompt, -1
        )

        # Return results as a ConditioningModel
        return ConditioningModel(
            p_prompt=prompt,
            n_prompt=negative_prompt,
            embeds={"embeds": prompt_embeds, "pooled": pooled_prompt_embeds},
            n_embeds={"embeds": negative_prompt_embeds, "pooled": negative_pooled_prompt_embeds},
        )
