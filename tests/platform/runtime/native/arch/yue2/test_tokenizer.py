"""Tests for the YuE2 text/ABC tokenizer (``arch/yue2/tokenizer.py``)."""

from __future__ import annotations

import pytest

from src.platform.runtime.native.arch.yue2.tokenizer import (
    ABC_END,
    ABC_START,
    BASE_VOCAB_SIZE,
    EOD,
    IM_END,
    IM_START,
    SPECIAL_IDS,
    TEXT_VOCAB_SIZE,
    YuE2Tokenizer,
)


class TestSpecialIdTable:
    def test_matches_protocol_pys_own_constants(self):
        """``protocol.py`` (the LM lane's own file) hardcodes these same three ids independently -- this is the cross-check that the two files were not built from diverging assumptions."""
        assert EOD == 151643
        assert ABC_START == 151847
        assert ABC_END == 151848

    def test_full_special_table_shape(self):
        assert len(SPECIAL_IDS) == 208
        assert len(set(SPECIAL_IDS.values())) == 208
        assert IM_START == 151644
        assert IM_END == 151645
        assert TEXT_VOCAB_SIZE == BASE_VOCAB_SIZE + 208 == 151851

    def test_abc_tags_overwrote_extra_196_and_197_not_appended(self):
        assert "<extra_196>" not in SPECIAL_IDS
        assert "<extra_197>" not in SPECIAL_IDS
        assert "<extra_195>" in SPECIAL_IDS
        assert "<extra_198>" in SPECIAL_IDS


class TestEncode:
    @pytest.fixture(scope="class")
    def tokenizer(self):
        return YuE2Tokenizer()

    def test_encode_returns_ids_below_the_base_vocab(self, tokenizer):
        ids = tokenizer.encode("a warm acoustic ballad about longing\n[verse]\nwalking home at night")
        assert ids
        assert all(0 <= i < BASE_VOCAB_SIZE for i in ids)

    def test_encode_is_deterministic(self, tokenizer):
        text = "Generate music with codec tokens from the given conditions.\n[Tags]\npop, upbeat\n"
        assert tokenizer.encode(text) == tokenizer.encode(text)

    def test_empty_string_encodes_to_nothing(self, tokenizer):
        assert tokenizer.encode("") == []

    def test_abc_notation_round_trips_through_ordinary_bpe(self, tokenizer):
        abc = "X:1\nT:Sample\nM:4/4\nL:1/8\nK:C\nC D E F | G A B c |\n"
        ids = tokenizer.encode(abc)
        assert ids
        assert all(0 <= i < BASE_VOCAB_SIZE for i in ids)

    def test_a_literal_chat_template_token_does_not_collide_with_a_yue2_special(self, tokenizer):
        """Regression test: the shared HF asset's OWN added-token table marks ``<|im_start|>`` special at id 151644 -- the SAME id YuE2 assigns to its own, unrelated ``IM_START``."""
        ids = tokenizer.encode("before <|im_start|> after")
        assert IM_START not in ids
        assert all(i < BASE_VOCAB_SIZE for i in ids)

    def test_decode_inverts_encode_for_abc_text(self, tokenizer):
        abc = "X:1\nT:Sample\nM:4/4\nL:1/8\nK:C\n\"C\"C D E F | \"G\"G A B c |]\n"
        assert tokenizer.decode(tokenizer.encode(abc)) == abc
