# Derived from: diffusers `src/diffusers/pipelines/minimax_music3/encoders.py`
# (Apache-2.0, "Copyright 2026 The MiniMax Team and The HuggingFace Team") for
# ``clean_caption``/``normalize_lyrics``/``build_prompt`` and the unconditional-ids
# slice rule — ported verbatim, function for function. ComfyUI's (GPL-3.0)
# `comfy/ldm/minimax_music/prompt.py` implements a DIFFERENT ``normalize_lyrics``
# (it keeps text sharing a line with a leading tag; diffusers drops it) — this
# module follows diffusers, per the plan's reference-source rules; "sounds
# different from Comfy" is not a bug signal for this function. The special-token
# id table and the embedded-tokenizer extraction (``MiniMaxMusic3Tokenizer``) are
# consulted from ComfyUI's `minimax_music.py` (diffusers uses a bundled HF
# tokenizer asset instead, which this native engine does not ship — the real
# checkpoint carries no such asset, only the ``tokenizer_json`` blob) and
# `text_encoders/tokenization.py`'s ``Gemma4Tokenizer`` idiom (load-time
# construction from the checkpoint's own blob, never a bundled asset).

"""Prompt assembly + the embedded-tokenizer wrapper for MiniMax-Music3.

The assembled prompt string is part of the checkpoint contract: even a
whitespace-level change shifts every downstream token id, so
``clean_caption``/``normalize_lyrics``/``build_prompt`` are a byte-for-byte port
of the diffusers reference, not a reimplementation from the model card's prose.
"""

from __future__ import annotations

import re

import torch

# Special-token id table, asserted against the checkpoint's own tokenizer blob at
# construction (see MiniMaxMusic3Tokenizer) rather than trusted blindly — a
# checkpoint whose blob disagrees would otherwise misassemble the prompt/CFG
# split with no error until the audio came out wrong.
IM_START = "<|im_start|>"
IM_END = "<|im_end|>"
CAPTION_START = "<|caption_start|>"
CAPTION_END = "<|caption_end|>"
LYRICS_START = "<|lyrics_start|>"
LYRICS_END = "<|lyrics_end|>"
AUDIO_START = "<|audio_start|>"
AUDIO_END = "<|audio_end|>"
AUDIO_CFG = "<|audio_cfg|>"

SPECIAL_TOKEN_IDS: dict[str, int] = {
    IM_START: 151644,
    IM_END: 151645,
    AUDIO_CFG: 151654,
    AUDIO_START: 151669,
    AUDIO_END: 151670,
    CAPTION_START: 151671,
    CAPTION_END: 151672,
    LYRICS_START: 151673,
    LYRICS_END: 151674,
}
AUDIO_END_TOKEN_ID = SPECIAL_TOKEN_IDS[AUDIO_END]
AUDIO_CFG_TOKEN_ID = SPECIAL_TOKEN_IDS[AUDIO_CFG]
AUDIO_CODE_OFFSET = 151675
SEMANTIC_VOCAB_SIZE = 16384

MAX_PROMPT_TOKENS = 5_000
MAX_AUDIO_FRAMES = 9_000

_SPECIAL_TAG_RE = re.compile(r"<\|([^|]*)\|>")
_LEADING_TAGS_RE = re.compile(r"^[ \t]*((?:\[[^\]]+\][ \t]*)+)")


def clean_caption(caption: str) -> str:
    """Strip markdown and flatten ``<|a b|>`` special tags ("bpm is 128" etc).

    Byte-for-byte port of diffusers' ``_clean_caption`` (``encoders.py``).
    """

    def _rewrite_special_tag(match: re.Match) -> str:
        inner = match.group(1).strip()
        parts = inner.split(None, 1)
        return f"{parts[0]} is {parts[1]}" if len(parts) == 2 else inner

    text = _SPECIAL_TAG_RE.sub(_rewrite_special_tag, caption)
    lines_out = []
    for line in text.splitlines():
        line = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)
        line = re.sub(r"^\s*[*+-]\s+", "", line)
        line = re.sub(r"^\s*\*\s+", "", line)
        while "**" in line:
            updated = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
            if updated == line:
                break
            line = updated
        line = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", line)
        lines_out.append(line.rstrip())
    text = "\n".join(lines_out)
    text = re.sub(r"^\s*[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    text = text.replace("• ", "").replace("    ", "")
    return re.sub(r"\n{2,}", "\n", text)


def normalize_lyrics(lyrics: str) -> str:
    """Normalize structure tags and prepend ``[start]``.

    Byte-for-byte port of diffusers' ``_normalize_lyrics`` (``encoders.py``):
    only consecutive leading ``[tag]`` groups at the start of a line survive —
    text sharing a line with a leading tag is DROPPED (the discriminator
    against ComfyUI's ``normalize_lyrics``, which keeps that text; see the
    module docstring). ``" ^ "`` is a manual line break; tags are lowercased.
    """
    output = []
    for line in lyrics.split("\n"):
        match = _LEADING_TAGS_RE.match(line)
        output.append(match.group(1).strip() if match else line)
    text = "\n".join(output)
    text = text.replace("] ", "]\n")
    text = text.replace(" [", "\n[")
    text = text.replace(" ^ ", "\n")
    text = re.sub(r"\[([^\]]+)\]", lambda match: f"[{match.group(1).lower()}]", text)
    return f"[start]\n{text}"


def build_prompt(caption: str, lyrics: str) -> str:
    """Assemble the checkpoint's special-token prompt string.

    Whitespace here is part of the checkpoint contract — do not reformat.
    """
    return (
        f"{IM_START}{CAPTION_START}{clean_caption(caption)}{CAPTION_END}"
        f"{LYRICS_START}{normalize_lyrics(lyrics)}{LYRICS_END}{IM_END}{AUDIO_START}"
    )


class MiniMaxMusic3Tokenizer:
    """Tokenizer built from the checkpoint's own embedded ``tokenizer_json`` blob.

    Same construction idiom as ``text_encoders.tokenization.Gemma4Tokenizer``:
    the loader captures the ``tokenizer_json`` tensor's bytes before stripping it
    from the state dict, and this class is built from those bytes at load time —
    there is no bundled asset, because the checkpoint's blob is the only known
    source (ComfyUI ships no separate asset either; ``minimax_music.py``'s
    ``MiniMaxMusic3Tokenizer`` does exactly this same construction).

    Unlike Gemma4Tokenizer, no BOS is prepended: :func:`build_prompt` already
    places every special token explicitly, and encoding uses
    ``add_special_tokens=False`` (ComfyUI does the same — the post-processor
    would otherwise add nothing anyway, but this matches the reference call).
    """

    def __init__(self, tokenizer_json_bytes: bytes) -> None:
        from tokenizers import Tokenizer

        self._tok = Tokenizer.from_str(tokenizer_json_bytes.decode("utf-8"))
        for token, expected in SPECIAL_TOKEN_IDS.items():
            actual = self._tok.token_to_id(token)
            if actual != expected:
                raise ValueError(
                    f"minimax_music3 tokenizer_json mismatch for {token!r}: "
                    f"expected id {expected}, got {actual!r}"
                )

    def encode(self, text: str) -> list[int]:
        return self._tok.encode(text, add_special_tokens=False).ids

    def build_conditional_pair(self, caption: str, lyrics: str) -> torch.Tensor:
        """Return the ``[2, L]`` conditional/unconditional token-id pair.

        Row 0 is the assembled prompt; row 1 is the same ids with every token
        except the first and the two trailing structure tokens
        (``<|im_end|><|audio_start|>``) replaced by ``<|audio_cfg|>`` — the
        diffusers ``MiniMaxMusic3TokenizeStep`` slice rule
        (``ids[:, 1:-2] = AUDIO_CFG_TOKEN_ID``).

        Raises when the assembled prompt exceeds :data:`MAX_PROMPT_TOKENS`
        (the reference recipe's hard cap).
        """
        ids = self.encode(build_prompt(caption, lyrics))
        input_ids = torch.tensor([ids], dtype=torch.long)
        if input_ids.shape[1] > MAX_PROMPT_TOKENS:
            raise ValueError(
                f"the assembled MiniMax-Music3 prompt has {input_ids.shape[1]} tokens; "
                f"the maximum is {MAX_PROMPT_TOKENS}"
            )
        unconditional_ids = input_ids.clone()
        unconditional_ids[:, 1:-2] = AUDIO_CFG_TOKEN_ID
        return torch.cat((input_ids, unconditional_ids), dim=0)
