"""Offline tokenization for the native text encoders.

Loads the bundled tokenizer assets (``assets/<name>_tokenizer/``) through
``transformers`` with ``local_files_only=True`` so no HuggingFace-hub request is
ever made — the failure mode that killed the previous Qwen attempt. The bundled
files are the exact ones ComfyUI ships, and the same tokenizer classes are used
(``Qwen2Tokenizer`` / ``T5TokenizerFast`` / ``CLIPTokenizer``), so token ids match
ComfyUI bit-for-bit.

Padding / masking here reproduces ComfyUI's ``SDTokenizer`` +
``SDClipModel.process_tokens`` for each encoder:

  * Qwen3 (Klein):  chat template applied, pad id 151643, min length 512, right
                    pad, attention mask 1 up to the first pad then 0.
  * T5-XXL (Flux1): trailing EOS (1) from the fast tokenizer kept, pad id 0, min
                    length 256, right pad.
  * CLIP-L (Flux1): BOS(49406)+text+EOS(49407), pad id 49407, length 77, right pad.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import torch

logger = logging.getLogger(__name__)

# Force offline for any transformers/hub code path touched here. setdefault so we
# never override an operator's explicit choice.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from .prompt_weights import weighted_token_ids  # noqa: E402  (after os.environ setdefault)

_ASSETS = Path(__file__).resolve().parent / "assets"

# Klein chat template (ComfyUI KleinTokenizer.llama_template).
QWEN3_TEMPLATE = "<|im_start|>user\n{}<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"

# Z-Image chat template (ComfyUI ZImageTokenizer.llama_template) — same Qwen2
# vocab as Klein but NO trailing `<think>` block and no forced 512 padding
# (ComfyUI min_length=1). The full templated sequence is kept (no prefix strip).
ZIMAGE_TEMPLATE = "<|im_start|>user\n{}<|im_end|>\n<|im_start|>assistant\n"

QWEN3_PAD = 151643
QWEN3_MIN_LEN = 512

# Krea-2 Qwen3-VL template, byte-identical to diffusers' Krea2Pipeline
# `prompt_template_encode_prefix`/`_suffix` (Apache-2.0). The system prompt prefix
# is stripped from the output sequence after encoding.
#
# This same system prompt is what comfyui-krea2edit's `Krea2EditGroundedEncode`
# node (Apache-2.0, lbouaraba) uses as its DEFAULT for the vision-grounded
# instruction encode -- Krea-2's training used one system prompt for
# both the text-only and image-conditioned cases, so the constant is shared
# rather than duplicated (see `KREA2VL_DEFAULT_SYSTEM_PROMPT` /
# `krea2vl_image_prefix` below); only the grounded path allows overriding it.
KREA2VL_DEFAULT_SYSTEM_PROMPT = (
    "Describe the image by detailing the color, shape, size, "
    "texture, quantity, text, spatial relationships of the objects and background:"
)
QWEN3VL_PREFIX = (
    f"<|im_start|>system\n{KREA2VL_DEFAULT_SYSTEM_PROMPT}<|im_end|>\n<|im_start|>user\n"
)
QWEN3VL_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n"
QWEN3VL_MIN_LEN = 512
# One occurrence per source image; the vision-conditioned encoder
# locates these tokens in the tokenized ids and splices the vision tower's
# merged embeddings + DeepStack taps in their place.
QWEN3VL_VISION_MARKUP = "<|vision_start|><|image_pad|><|vision_end|>"


def krea2vl_image_prefix(system_prompt: str, num_images: int) -> str:
    """Build the system+user prefix for a vision-grounded Krea-2 encode: the
    system prompt, then one ``QWEN3VL_VISION_MARKUP`` block per image (scene
    first, subject second — matching the training/edit-mode reference order),
    then the user turn opens for the instruction text.
    """
    vision_blocks = "\n".join([QWEN3VL_VISION_MARKUP] * num_images)
    return f"<|im_start|>system\n{system_prompt}<|im_end|>\n<|im_start|>user\n{vision_blocks}\n"

T5_PAD = 0
T5_MIN_LEN = 256

# Wan UMT5-XXL uses a SentencePiece model (256k vocab), not the T5 tokenizer.json.
# The spiece asset was extracted from the local umt5 fp8 checkpoint's embedded
# `spiece_model` tensor (ComfyUI convention).
UMT5_PAD = 0
UMT5_MIN_LEN = 512

CLIP_BOS = 49406
CLIP_EOS = 49407
CLIP_PAD = 49407
CLIP_LEN = 77


def _load_tokenizer(subdir: str, cls_name: str):
    """Load a bundled transformers tokenizer fully offline."""
    import transformers

    cls = getattr(transformers, cls_name)
    path = _ASSETS / subdir
    if not path.is_dir():
        raise FileNotFoundError(f"bundled tokenizer assets missing: {path}")
    return cls.from_pretrained(str(path), local_files_only=True)


def _pad_batch(
    id_lists: list[list[int]],
    pad_token: int,
    min_length: int,
    device: torch.device | str,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Right-pad a batch to ``max(min_length, longest)``; build the padding mask.

    The mask is 1 for the real tokens of each row and 0 for the padding, matching
    ComfyUI (first pad token onward is masked out).
    """
    target = max(min_length, max((len(x) for x in id_lists), default=0))
    ids = torch.full((len(id_lists), target), pad_token, dtype=torch.long, device=device)
    mask = torch.zeros((len(id_lists), target), dtype=torch.long, device=device)
    for i, row in enumerate(id_lists):
        n = len(row)
        if n:
            ids[i, :n] = torch.tensor(row, dtype=torch.long, device=device)
            mask[i, :n] = 1
    return ids, mask


def _pad_weighted(
    id_list: list[int], weight_list: list[float], pad_token: int, min_length: int,
    device: torch.device | str,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """Pad a single weighted prompt to ``max(min_length, len)`` -> (ids, mask, weights).

    Padding positions carry weight 1.0 (so :func:`apply_token_weights` leaves them
    unchanged). Batch size is 1 (the ClipTextEncoder adapters weight one prompt).
    """
    target = max(min_length, len(id_list))
    ids = torch.full((1, target), pad_token, dtype=torch.long, device=device)
    mask = torch.zeros((1, target), dtype=torch.long, device=device)
    weights = torch.ones((1, target), dtype=torch.float32, device=device)
    n = len(id_list)
    if n:
        ids[0, :n] = torch.tensor(id_list, dtype=torch.long, device=device)
        mask[0, :n] = 1
        weights[0, :n] = torch.tensor(weight_list, dtype=torch.float32, device=device)
    return ids, mask, weights


class Qwen3Tokenizer:
    """Klein / Flux2 Qwen3 tokenizer (chat-templated, min length 512)."""

    def __init__(self) -> None:
        self._tok = _load_tokenizer("qwen3_tokenizer", "Qwen2Tokenizer")

    def __call__(
        self, texts: list[str], device: torch.device | str = "cpu"
    ) -> tuple[torch.Tensor, torch.Tensor]:
        id_lists = [self._tok(QWEN3_TEMPLATE.format(t))["input_ids"] for t in texts]
        return _pad_batch(id_lists, QWEN3_PAD, QWEN3_MIN_LEN, device)

    def tokenize_with_weights(self, prompt: str, device="cpu"):
        prefix, suffix = QWEN3_TEMPLATE.split("{}")
        ids, weights = weighted_token_ids(self._tok, prompt, prefix, suffix)
        return _pad_weighted(ids, weights, QWEN3_PAD, QWEN3_MIN_LEN, device)


class ZImageTokenizer:
    """Z-Image Qwen3-4B tokenizer (chat-templated, min length 1, no prefix strip).

    Same Qwen2 vocab / pad id as :class:`Qwen3Tokenizer`, but uses the Z-Image
    template (no ``<think>`` block) and does NOT force a 512-length pad
    (ComfyUI ``ZImageTokenizer`` min_length=1 -> pad to the batch longest only).
    The whole templated sequence is fed to the DiT (no prefix stripping).
    """

    def __init__(self) -> None:
        self._tok = _load_tokenizer("qwen3_tokenizer", "Qwen2Tokenizer")

    def __call__(
        self, texts: list[str], device: torch.device | str = "cpu"
    ) -> tuple[torch.Tensor, torch.Tensor]:
        id_lists = [self._tok(ZIMAGE_TEMPLATE.format(t))["input_ids"] for t in texts]
        return _pad_batch(id_lists, QWEN3_PAD, 1, device)   # min_length=1

    def tokenize_with_weights(self, prompt: str, device="cpu"):
        prefix, suffix = ZIMAGE_TEMPLATE.split("{}")
        ids, weights = weighted_token_ids(self._tok, prompt, prefix, suffix)
        return _pad_weighted(ids, weights, QWEN3_PAD, 1, device)


class Qwen3VLTokenizer:
    """Krea-2 Qwen3-VL tokenizer (system+user template; prefix stripped later).

    Shares the Qwen2 vocab with :class:`Qwen3Tokenizer`. Returns
    ``(ids, mask, prefix_len)`` — the encoder drops the first ``prefix_len``
    positions (the system-prompt template) from the model output, matching
    diffusers' ``Krea2Pipeline`` (whose ``prompt_template_encode_start_idx`` is a
    hardcoded 34 for the canonical prefix). ``prefix_len`` is computed from the
    tokenizer instead, so it stays correct if the vocab or prefix ever changes.
    """

    def __init__(self) -> None:
        self._tok = _load_tokenizer("qwen3_tokenizer", "Qwen2Tokenizer")
        self._prefix_len = len(self._tok(QWEN3VL_PREFIX)["input_ids"])

    def __call__(
        self, texts: list[str], device: torch.device | str = "cpu"
    ) -> tuple[torch.Tensor, torch.Tensor, int]:
        id_lists = [
            self._tok(QWEN3VL_PREFIX + t + QWEN3VL_SUFFIX)["input_ids"] for t in texts
        ]
        ids, mask = _pad_batch(id_lists, QWEN3_PAD, QWEN3VL_MIN_LEN, device)
        return ids, mask, self._prefix_len

    def tokenize_with_weights(self, prompt: str, device="cpu"):
        ids, weights = weighted_token_ids(self._tok, prompt, QWEN3VL_PREFIX, QWEN3VL_SUFFIX)
        return _pad_weighted(ids, weights, QWEN3_PAD, QWEN3VL_MIN_LEN, device)

    def tokenize_with_images(
        self, text: str, *, num_images: int, device: torch.device | str = "cpu",
        system_prompt: str | None = None,
    ) -> tuple[torch.Tensor, torch.Tensor, int]:
        """Vision-grounded template (Krea-2 edit mode): the system
        prompt (default :data:`KREA2VL_DEFAULT_SYSTEM_PROMPT`, overridable),
        one ``<|vision_start|><|image_pad|><|vision_end|>`` block per image,
        then the instruction text. Single prompt per call (the encoder's
        image-conditioned path is batch-size-1 — see
        ``Qwen3VLTextEncoder._encode_with_images``).

        ``prefix_len`` is recomputed every call (unlike the text-only path's
        ``self._prefix_len``, cached once in ``__init__``) since a caller-
        supplied ``system_prompt`` or a different ``num_images`` changes the
        prefix's tokenized length. No forced 512-token minimum (matches
        ``Qwen25VLTokenizer``'s image path: natural length, pad to longest
        only, since the joint text+vision sequence already dwarfs 512 for
        anything but a trivial instruction).
        """
        if num_images < 1:
            raise ValueError("tokenize_with_images requires at least one image")
        prefix = krea2vl_image_prefix(system_prompt or KREA2VL_DEFAULT_SYSTEM_PROMPT, num_images)
        prefix_len = len(self._tok(prefix)["input_ids"])
        ids = self._tok(prefix + text + QWEN3VL_SUFFIX)["input_ids"]
        ids_t, mask = _pad_batch([ids], QWEN3_PAD, 1, device)
        return ids_t, mask, prefix_len


# Qwen-Image-Edit system prompt (ComfyUI QwenImageTokenizer.llama_template_images).
# Different text than QWEN3VL_PREFIX/the text-only template above — the vision
# markup + image content sit AFTER this fixed prefix, so its own tokenized
# length is a fixed prefix-drop boundary independent of image size/content,
# exactly like QWEN3VL_PREFIX's 34 is for the text-only template.
QWEN25VL_IMAGE_SYSTEM = (
    "<|im_start|>system\nDescribe the key features of the input image (color, shape, size, "
    "texture, objects, background), then explain how the user's text instruction should alter "
    "or modify the image. Generate a new image that meets the user's requirements while "
    "maintaining consistency with the original input where appropriate.<|im_end|>\n<|im_start|>user\n"
)
# One occurrence per image; the text encoder locates these tokens in the
# tokenized ids and splices the vision tower's merged embeddings in their place.
QWEN25VL_VISION_MARKUP = "<|vision_start|><|image_pad|><|vision_end|>"


class Qwen25VLTokenizer:
    """Qwen-Image Qwen2.5-VL tokenizer (same system+user template, NO 512 pad).

    Reuses the shared Qwen2 vocab and the same template as :class:`Qwen3VLTokenizer`
    (Qwen-Image and Krea-2 use the identical system prompt), but ComfyUI's
    ``Qwen25_7BVLITokenizer`` uses ``min_length=1`` — no forced 512 padding, natural
    length. Returns ``(ids, mask, prefix_len)``; the encoder drops the first
    ``prefix_len`` positions (the template prefix, 34 tokens for the canonical
    template — verified equal to ComfyUI's dynamic ``template_end``).

    ``has_image=True`` (Qwen-Image-Edit) swaps in the image-conditioned system
    prompt + a ``<|vision_start|><|image_pad|><|vision_end|>`` vision-markup slot
    right after ``<|im_start|>user\\n`` (ComfyUI ``llama_template_images``); this
    tokenizer never touches actual image tensors — ``<|image_pad|>`` is an
    ordinary vocab token, and the encoder (not the tokenizer) is what finds its
    position(s) in the returned ids and splices in vision-tower output.
    """

    IMAGE_PAD_TOKEN = 151655

    def __init__(self) -> None:
        self._tok = _load_tokenizer("qwen3_tokenizer", "Qwen2Tokenizer")
        self._prefix_len = len(self._tok(QWEN3VL_PREFIX)["input_ids"])
        self._prefix_len_images = len(self._tok(QWEN25VL_IMAGE_SYSTEM)["input_ids"])

    def __call__(
        self, texts: list[str], device: torch.device | str = "cpu", has_image: bool = False
    ) -> tuple[torch.Tensor, torch.Tensor, int]:
        if has_image:
            id_lists = [
                self._tok(QWEN25VL_IMAGE_SYSTEM + QWEN25VL_VISION_MARKUP + t + QWEN3VL_SUFFIX)["input_ids"]
                for t in texts
            ]
            ids, mask = _pad_batch(id_lists, QWEN3_PAD, 1, device)
            return ids, mask, self._prefix_len_images
        id_lists = [self._tok(QWEN3VL_PREFIX + t + QWEN3VL_SUFFIX)["input_ids"] for t in texts]
        ids, mask = _pad_batch(id_lists, QWEN3_PAD, 1, device)  # min_length=1: pad to longest only
        return ids, mask, self._prefix_len

    def tokenize_with_weights(self, prompt: str, device="cpu"):
        ids, weights = weighted_token_ids(self._tok, prompt, QWEN3VL_PREFIX, QWEN3VL_SUFFIX)
        return _pad_weighted(ids, weights, QWEN3_PAD, 1, device)


class MiniMaxH3Tokenizer:
    """MiniMax-H3's Qwen3-VL-32B tokenizer: raw prompt, NO chat template, NO
    special tokens (``add_special_tokens=False``), no padding ever (MiniMax-H3
    packs one request into one unpadded sequence — see
    ``qwen3.py``'s ``MiniMaxH3TextEncoder``).

    Reuses the SAME bundled ``qwen3_tokenizer`` asset as :class:`Qwen3Tokenizer`
    — no separate assets were bundled for this variant. Verified equivalent,
    not merely assumed: MiniMax-H3's `text_encoder/config.json` reports
    `vocab_size: 151936`, matching the bundled asset's vocab size and the real
    checkpoint's `model.embed_tokens.weight` row count exactly (ai/minimax_h3/
    te_bf16_header.json); both are `tokenizer_class: Qwen2Tokenizer` with the
    same 26 added special tokens and the same `<|endoftext|>` pad token.
    """

    def __init__(self) -> None:
        self._tok = _load_tokenizer("qwen3_tokenizer", "Qwen2Tokenizer")

    def __call__(self, text: str) -> list[int]:
        return self._tok(text, add_special_tokens=False)["input_ids"]

    def convert_tokens_to_ids(self, token: str) -> int:
        return self._tok.convert_tokens_to_ids(token)


class UMT5Tokenizer:
    """Wan UMT5-XXL SentencePiece tokenizer (add_eos, pad 0, min length 512).

    Loads the bundled spiece model fully offline. ``add_bos=False, add_eos=True``
    matches ComfyUI's ``SPieceTokenizer`` / ``UMT5XXlTokenizer``.
    """

    def __init__(self) -> None:
        import sentencepiece

        path = _ASSETS / "umt5_spiece" / "spiece.model"
        if not path.is_file():
            raise FileNotFoundError(f"bundled UMT5 spiece model missing: {path}")
        self._sp = sentencepiece.SentencePieceProcessor(
            model_file=str(path), add_bos=False, add_eos=True
        )

    def __call__(
        self, texts: list[str], device: torch.device | str = "cpu"
    ) -> tuple[torch.Tensor, torch.Tensor]:
        id_lists = [self._sp.encode(t) for t in texts]
        return _pad_batch(id_lists, UMT5_PAD, UMT5_MIN_LEN, device)


class Gemma3Tokenizer:
    """LTX-2 Gemma3-12B SentencePiece tokenizer (add_bos, no eos, no template).

    ComfyUI ``Gemma3_12BTokenizer``: ``add_bos=True, add_eos=False`` (BOS=2), pad 0,
    min_length 1, plain prompt (llama_template "{}"). Spiece extracted from the
    local gemma checkpoint's embedded ``spiece_model`` tensor.
    """

    def __init__(self) -> None:
        import sentencepiece

        path = _ASSETS / "gemma3_spiece" / "spiece.model"
        if not path.is_file():
            raise FileNotFoundError(f"bundled Gemma3 spiece model missing: {path}")
        self._sp = sentencepiece.SentencePieceProcessor(
            model_file=str(path), add_bos=True, add_eos=False
        )

    def __call__(
        self, texts: list[str], device: torch.device | str = "cpu"
    ) -> tuple[torch.Tensor, torch.Tensor]:
        id_lists = [self._sp.encode(t) for t in texts]
        return _pad_batch(id_lists, 0, 1, device)   # pad 0, min_length 1


class Gemma4Tokenizer:
    """LTX-2.5 Gemma4-Unified-12B tokenizer (add_bos, no eos, no template).

    Unlike Gemma3, Gemma4-Unified has no SentencePiece model to bundle as an
    asset: the real checkpoint embeds an HF *fast* tokenizer (a ``tokenizer_json``
    byte tensor, ~32MB) whose model type is BPE, not Unigram — there is no
    spiece.model this could ever be extracted into. The tokenizer is therefore
    built at load time from that checkpoint blob (see ``loader.py``'s gemma4
    branch, which captures the bytes before stripping the tensor from the state
    dict), never from a bundled asset.

    The blob's ``post_processor`` is a ``TemplateProcessing`` with an EMPTY
    ``special_tokens`` map, so ``Tokenizer.encode`` adds no special tokens by
    itself — BOS is prepended here explicitly, exactly once per row, matching
    diffusers' LTX-2.5 encode path (``_get_gemma_prompt_embeds``, which tokenizes
    with ``add_special_tokens=True`` for both the Gemma3 and Gemma4 text
    encoders) and the sibling :class:`Gemma3Tokenizer` (no EOS, min_length 1).
    BOS/PAD ids are read from the blob's own vocab rather than hardcoded, though
    the shipped checkpoint uses the classic Gemma layout (pad 0, eos 1, bos 2).
    """

    def __init__(self, tokenizer_json_bytes: bytes) -> None:
        from tokenizers import Tokenizer

        self._tok = Tokenizer.from_str(tokenizer_json_bytes.decode("utf-8"))
        bos_id = self._tok.token_to_id("<bos>")
        pad_id = self._tok.token_to_id("<pad>")
        if bos_id is None or pad_id is None:
            raise ValueError(
                "gemma4 tokenizer_json vocab is missing '<bos>' and/or '<pad>' "
                f"(bos={bos_id}, pad={pad_id})"
            )
        self._bos_id = bos_id
        self._pad_id = pad_id

    def __call__(
        self, texts: list[str], device: torch.device | str = "cpu"
    ) -> tuple[torch.Tensor, torch.Tensor]:
        id_lists = [[self._bos_id, *self._tok.encode(t).ids] for t in texts]
        return _pad_batch(id_lists, self._pad_id, 1, device)   # min_length 1


class T5XXLTokenizerWrap:
    """Flux1 T5-XXL tokenizer (trailing EOS kept, min length 256)."""

    def __init__(self) -> None:
        self._tok = _load_tokenizer("t5_tokenizer", "T5TokenizerFast")

    def __call__(
        self, texts: list[str], device: torch.device | str = "cpu"
    ) -> tuple[torch.Tensor, torch.Tensor]:
        id_lists = [self._tok(t)["input_ids"] for t in texts]
        return _pad_batch(id_lists, T5_PAD, T5_MIN_LEN, device)


class AnimaQwen3Tokenizer:
    """Anima Qwen3-0.6B tokenizer — plain text, NO chat template, pad 151643, min 1.

    ComfyUI's ``AnimaTokenizer`` uses the shared Qwen2 vocab but (unlike Klein's
    :class:`Qwen3Tokenizer`) applies no chat template and adds no start/end tokens
    (``has_start_token=False, has_end_token=False``). Batch size is normally 1
    (the generator encodes one prompt at a time)."""

    def __init__(self) -> None:
        self._tok = _load_tokenizer("qwen3_tokenizer", "Qwen2Tokenizer")

    def __call__(
        self, texts: list[str], device: torch.device | str = "cpu"
    ) -> tuple[torch.Tensor, torch.Tensor]:
        id_lists = [self._tok(t, add_special_tokens=False)["input_ids"] for t in texts]
        return _pad_batch(id_lists, QWEN3_PAD, 1, device)


class AnimaT5Tokenizer:
    """Anima T5 tokenizer — the LLMAdapter's target token ids + A1111 weights.

    Anima has no T5 *model*: the DiT's ``LLMAdapter`` owns a ``Embedding(32128, …)``
    that these T5 token ids index. So only the tokenizer is needed. Plain T5
    tokenization with the trailing EOS kept (weight 1.0), pad 0, min length 1. A1111
    ``(word:1.3)`` weights ride through per-token so the DiT can scale the fused
    context by them (ComfyUI applies emphasis on the t5 side, forcing the Qwen
    weights to 1.0). Returns ``(ids [1,S], mask [1,S], weights [1,S])`` for one
    prompt."""

    def __init__(self) -> None:
        self._tok = _load_tokenizer("t5_tokenizer", "T5TokenizerFast")
        self._eos = self._tok.eos_token_id if self._tok.eos_token_id is not None else 1

    def __call__(
        self, text: str, device: torch.device | str = "cpu"
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        ids, weights = weighted_token_ids(self._tok, text)
        ids = ids + [self._eos]        # T5TokenizerFast appends </s>; keep it (weight 1.0)
        weights = weights + [1.0]
        return _pad_weighted(ids, weights, T5_PAD, 1, device)


class CLIPLTokenizerWrap:
    """Flux1 CLIP-L tokenizer (BOS+text+EOS, padded to 77 with EOS)."""

    def __init__(self) -> None:
        self._tok = _load_tokenizer("clip_l_tokenizer", "CLIPTokenizer")

    def __call__(
        self, texts: list[str], device: torch.device | str = "cpu"
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Return ``(ids [B,77], mask [B,77], eos_index [B])``.

        ``eos_index`` is the position of the first EOS token per row — the CLIP
        pooled output is read there.
        """
        id_lists = [self._tok(t)["input_ids"] for t in texts]
        ids = torch.full((len(id_lists), CLIP_LEN), CLIP_PAD, dtype=torch.long, device=device)
        mask = torch.zeros((len(id_lists), CLIP_LEN), dtype=torch.long, device=device)
        eos_index = torch.zeros(len(id_lists), dtype=torch.long, device=device)
        for i, row in enumerate(id_lists):
            row = row[:CLIP_LEN]
            n = len(row)
            ids[i, :n] = torch.tensor(row, dtype=torch.long, device=device)
            # First EOS marks the end of real content (pad == eos afterwards).
            first_eos = next((j for j, t in enumerate(row) if t == CLIP_EOS), n - 1)
            mask[i, : first_eos + 1] = 1
            eos_index[i] = first_eos
        return ids, mask, eos_index
