# Derived from: https://github.com/multimodal-art-projection/YuE src/yue2/tokenization_yue2.py (Apache-2.0)

"""YuE2's text/ABC tokenizer."""

from __future__ import annotations

import unicodedata
from pathlib import Path

_ASSET_PATH = Path(__file__).resolve().parents[2] / "text_encoders" / "assets" / "qwen3_tokenizer"

BASE_VOCAB_SIZE = 151643

_SPECIAL_NAMES: list[str] = [
    "<|endoftext|>", "<|im_start|>", "<|im_end|>", "<R>", "<S>", "<X>", "<mask>", "<sep>",
] + [f"<extra_{i}>" for i in range(200)]
_SPECIAL_NAMES[204:206] = ["<abc>", "</abc>"]

SPECIAL_IDS: dict[str, int] = {name: BASE_VOCAB_SIZE + i for i, name in enumerate(_SPECIAL_NAMES)}

TEXT_VOCAB_SIZE = BASE_VOCAB_SIZE + len(_SPECIAL_NAMES)

EOD = SPECIAL_IDS["<|endoftext|>"]
IM_START = SPECIAL_IDS["<|im_start|>"]
IM_END = SPECIAL_IDS["<|im_end|>"]
ABC_START = SPECIAL_IDS["<abc>"]
ABC_END = SPECIAL_IDS["</abc>"]


class YuE2Tokenizer:
    """Encode-only: ordinary BPE ids in ``[0, BASE_VOCAB_SIZE)`` plus :data:`SPECIAL_IDS` for the frame tokens ``encode`` itself never emits."""

    special_ids = SPECIAL_IDS

    def __init__(self) -> None:
        from transformers import Qwen2Tokenizer

        self._tok = Qwen2Tokenizer.from_pretrained(str(_ASSET_PATH), local_files_only=True)

    def encode(self, text: str) -> list[int]:
        normalized = unicodedata.normalize("NFC", text)
        return self._tok.encode(normalized, add_special_tokens=False, split_special_tokens=True)
