"""MiniMax-Music3 prompt-contract tests: caption/lyrics normalization, the
assembled special-token template (exact whitespace), the unconditional-ids
slice rule, and the embedded-tokenizer wrapper's id-table validation.

Ported test cases from diffusers' `encoders.py` reference behavior (see
`arch/minimax_music3/prompt.py`'s module docstring) — NOT from ComfyUI's
`normalize_lyrics`, which genuinely differs (keeps tag-line text).
"""

from __future__ import annotations

import contextlib
import os

import pytest
import torch

from src.platform.runtime.native.arch.minimax_music3.prompt import (
    AUDIO_CFG_TOKEN_ID,
    MAX_PROMPT_TOKENS,
    SPECIAL_TOKEN_IDS,
    MiniMaxMusic3Tokenizer,
    build_prompt,
    clean_caption,
    normalize_lyrics,
)


class TestCleanCaption:
    def test_flattens_bpm_style_special_tag(self):
        """`<|bpm 128|>` -> "bpm is 128" — the training-caption idiom the
        model card documents; the model never sees the `<|...|>` syntax."""
        assert clean_caption("<|bpm 128|>") == "bpm is 128"

    def test_flattens_key_scale_tag(self):
        assert clean_caption("<|key C|>") == "key is C"

    def test_single_word_tag_survives_unrewritten(self):
        assert clean_caption("<|solo|>") == "solo"

    def test_strips_markdown_heading_and_bullets_and_emphasis(self):
        caption = "### Genre\n- upbeat pop\n* **loud** drums\n*soft* vocals"
        assert clean_caption(caption) == "Genre\nupbeat pop\nloud drums\nsoft vocals"

    def test_collapses_blank_lines(self):
        assert clean_caption("a\n\n\nb") == "a\nb"


class TestNormalizeLyrics:
    def test_prepends_start_tag(self):
        assert normalize_lyrics("hello").startswith("[start]\n")

    def test_lowercases_tags(self):
        """`[Post-Chorus]` -> `[post-chorus]` — tags are case-normalized."""
        assert "[post-chorus]" in normalize_lyrics("[Post-Chorus]\nla la la")

    def test_caret_marks_a_manual_line_break(self):
        assert normalize_lyrics("la ^ la") == "[start]\nla\nla"

    def test_text_sharing_a_leading_tag_line_is_dropped(self):
        """The diffusers-specific rule this file follows (NOT ComfyUI's): only
        the tag itself survives when text shares its line."""
        result = normalize_lyrics("[verse] some lyric text here")
        assert result == "[start]\n[verse]"

    def test_text_on_its_own_line_after_a_tag_is_kept(self):
        result = normalize_lyrics("[verse]\nsome lyric text here")
        assert result == "[start]\n[verse]\nsome lyric text here"

    def test_consecutive_leading_tags_on_one_line_both_survive_on_separate_lines(self):
        """Both tags survive; the ``"] " -> "]\\n"`` normalization (applied
        globally, after the tag-line drop) then splits them onto their own
        lines like any other ``"] "``/``" ["`` boundary."""
        result = normalize_lyrics("[verse] [male] some text")
        assert result == "[start]\n[verse]\n[male]"


class TestBuildPrompt:
    def test_exact_whitespace_and_token_order(self):
        """Whitespace here is the checkpoint contract — a byte-exact golden,
        not a loosely-matched assertion."""
        prompt = build_prompt("upbeat pop", "[verse]\nla la")
        assert prompt == (
            "<|im_start|><|caption_start|>upbeat pop<|caption_end|>"
            "<|lyrics_start|>[start]\n[verse]\nla la<|lyrics_end|>"
            "<|im_end|><|audio_start|>"
        )


def _write_all_stdout_to_devnull():
    """The `tokenizers` Rust extension writes an "OrderedVocab ... contains
    holes" notice straight to fd 1 when a vocab has non-contiguous ids (true
    by construction here — the special tokens sit at their real, sparse
    150000-range ids) — it bypasses ``sys.stdout`` entirely, so
    ``contextlib.redirect_stdout`` cannot catch it; only an fd-level
    redirect can.
    """
    devnull = os.open(os.devnull, os.O_WRONLY)
    saved = os.dup(1)
    os.dup2(devnull, 1)
    os.close(devnull)
    return saved


def _restore_stdout(saved: int) -> None:
    os.dup2(saved, 1)
    os.close(saved)


@contextlib.contextmanager
def _quiet_tokenizer_save():
    saved = _write_all_stdout_to_devnull()
    try:
        yield
    finally:
        _restore_stdout(saved)


def _tokenizer_json_bytes(*, token_ids: dict[str, int] | None = None) -> bytes:
    """Build a sparse-vocab tokenizer whose special tokens sit at their real
    ids. ``token_ids`` overrides/extends the real table, for the mismatch test.
    """
    from tokenizers import Tokenizer
    from tokenizers.models import BPE

    # Plain BPE with no merges only recognizes single-character tokens
    # individually (a multi-char vocab entry like "hello" needs a merge path
    # to be reachable) -- 'a'/'b' are enough to exercise `encode`.
    vocab = {"<unk>": 0, "a": 1, "b": 2}
    vocab.update(token_ids if token_ids is not None else SPECIAL_TOKEN_IDS)
    tok = Tokenizer(BPE(vocab=vocab, merges=[], unk_token="<unk>"))
    with _quiet_tokenizer_save():
        return tok.to_str().encode("utf-8")


class TestMiniMaxMusic3Tokenizer:
    def test_constructs_from_a_matching_blob(self):
        tok = MiniMaxMusic3Tokenizer(_tokenizer_json_bytes())
        assert tok.encode("ab") == [1, 2]

    def test_raises_when_a_special_token_id_disagrees_with_the_blob(self):
        """Bite-check for the id-table assertion: a blob whose
        `<|audio_start|>` sits at the wrong id must be rejected at
        construction, not silently accepted."""
        bad = dict(SPECIAL_TOKEN_IDS)
        bad["<|audio_start|>"] = SPECIAL_TOKEN_IDS["<|audio_start|>"] + 1
        with pytest.raises(ValueError, match="audio_start"):
            MiniMaxMusic3Tokenizer(_tokenizer_json_bytes(token_ids=bad))

    def test_raises_when_a_special_token_is_missing_entirely(self):
        missing = dict(SPECIAL_TOKEN_IDS)
        del missing["<|lyrics_end|>"]
        with pytest.raises(ValueError, match="lyrics_end"):
            MiniMaxMusic3Tokenizer(_tokenizer_json_bytes(token_ids=missing))

    def test_encode_does_not_add_special_tokens(self):
        """`add_special_tokens=False` semantics — this bare BPE model adds
        none anyway, but the call must request that explicitly (a future
        blob with a post_processor must not silently start adding a BOS)."""
        tok = MiniMaxMusic3Tokenizer(_tokenizer_json_bytes())
        assert tok.encode("a") == [1]

    def test_build_conditional_pair_shape_and_unconditional_slice(self):
        tok = MiniMaxMusic3Tokenizer(_tokenizer_json_bytes())
        pair = tok.build_conditional_pair("a", "b")
        assert pair.shape[0] == 2
        conditional, unconditional = pair[0], pair[1]
        assert torch.equal(conditional[0:1], unconditional[0:1])
        assert torch.equal(conditional[-2:], unconditional[-2:])
        assert torch.all(unconditional[1:-2] == AUDIO_CFG_TOKEN_ID)
        # The slice actually replaced something real, not a same-value no-op —
        # otherwise this "bite" would pass even if the assignment were dropped.
        assert not torch.equal(conditional[1:-2], unconditional[1:-2])

    def test_build_conditional_pair_raises_over_the_token_cap(self):
        tok = MiniMaxMusic3Tokenizer(_tokenizer_json_bytes())
        huge_lyrics = "a " * (MAX_PROMPT_TOKENS + 100)
        with pytest.raises(ValueError, match=str(MAX_PROMPT_TOKENS)):
            tok.build_conditional_pair("caption", huge_lyrics)
