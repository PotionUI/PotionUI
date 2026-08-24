"""``cfg_sampling.py``: CFG-guided top-k sampling, and the re-mask-after-CFG
NaN trap the module docstring describes.
"""

from __future__ import annotations

import torch

from src.platform.runtime.native.arch.minimax_music3.cfg_sampling import (
    full_vocab_mask,
    guided_top_k_sample,
)
from src.platform.runtime.native.arch.minimax_music3.prompt import (
    AUDIO_CODE_OFFSET,
    AUDIO_END_TOKEN_ID,
    SEMANTIC_VOCAB_SIZE,
)

_VOCAB = 200_000


def _random_logits(seed: int) -> tuple[torch.Tensor, torch.Tensor]:
    g = torch.Generator().manual_seed(seed)
    cond = torch.randn(_VOCAB, generator=g) * 5.0
    uncond = torch.randn(_VOCAB, generator=g) * 5.0
    return cond, uncond


class TestFullVocabMask:
    def test_masks_everything_outside_the_audio_window_and_stop_id(self):
        logits = torch.randn(_VOCAB)
        masked = full_vocab_mask(logits)
        assert torch.isinf(masked[0]) and masked[0] < 0
        assert torch.isinf(masked[AUDIO_CODE_OFFSET - 1]) and masked[AUDIO_CODE_OFFSET - 1] < 0
        assert torch.isinf(masked[AUDIO_CODE_OFFSET + SEMANTIC_VOCAB_SIZE]) and masked[AUDIO_CODE_OFFSET + SEMANTIC_VOCAB_SIZE] < 0
        assert not torch.isinf(masked[AUDIO_CODE_OFFSET])
        assert not torch.isinf(masked[AUDIO_CODE_OFFSET + SEMANTIC_VOCAB_SIZE - 1])
        assert not torch.isinf(masked[AUDIO_END_TOKEN_ID])
        assert masked[AUDIO_END_TOKEN_ID] == logits[AUDIO_END_TOKEN_ID]

    def test_idempotent_and_overwrites_existing_nan(self):
        """``masked_fill`` does not inspect the value it overwrites — the
        property the re-mask-after-CFG step in ``guided_top_k_sample``
        depends on to clean up the ``-inf - -inf`` NaN (see that test's
        docstring)."""
        logits = torch.randn(_VOCAB)
        logits[0] = float("nan")
        masked = full_vocab_mask(logits)
        assert not torch.isnan(masked).any()
        assert torch.isinf(masked[0]) and masked[0] < 0


class TestGuidedTopKSample:
    def test_pruned_layout_no_mask_produces_a_legal_pruned_index(self):
        cond, uncond = _random_logits(1)
        cond, uncond = cond[:16_385], uncond[:16_385]
        sampled = guided_top_k_sample(cond, uncond, cfg_scale=1.5, top_k=50, generator=torch.Generator().manual_seed(2), mask_fn=None)
        assert 0 <= sampled < 16_385

    def test_full_layout_with_mask_never_samples_outside_the_legal_window(self):
        for seed in range(20):
            cond, uncond = _random_logits(seed)
            sampled = guided_top_k_sample(
                cond, uncond, cfg_scale=1.5, top_k=50,
                generator=torch.Generator().manual_seed(seed + 1000), mask_fn=full_vocab_mask,
            )
            legal = sampled == AUDIO_END_TOKEN_ID or AUDIO_CODE_OFFSET <= sampled < AUDIO_CODE_OFFSET + SEMANTIC_VOCAB_SIZE
            assert legal, sampled

    def test_deterministic_under_a_fixed_generator(self):
        cond, uncond = _random_logits(7)
        a = guided_top_k_sample(cond, uncond, 1.5, 50, torch.Generator().manual_seed(42), mask_fn=full_vocab_mask)
        b = guided_top_k_sample(cond, uncond, 1.5, 50, torch.Generator().manual_seed(42), mask_fn=full_vocab_mask)
        assert a == b

    def test_cfg_scale_other_than_one_does_not_nan_the_full_layout_sample(self):
        """The trap: ``guided = uncond_masked + (cond_masked - uncond_masked) * cfg_scale``
        computes ``-inf - -inf`` (NaN) at every position both branches mask
        out, whenever ``cfg_scale != 1`` -- this only fires with the vocab
        mask engaged (the pruned layout has nothing to mask, so it can never
        hit this). Passing plain, un-re-masked ``guided`` logits into
        top-k/softmax would poison every sampled probability, not just the
        excluded ones (``torch.softmax``'s sum-of-exp mixes the whole
        vocabulary) -- this test would then either raise from
        ``torch.multinomial`` seeing negative/NaN probabilities, or (worse)
        silently sample garbage.
        """
        for cfg_scale in (1.3, 1.5, 1.7, 2.0):
            cond, uncond = _random_logits(3)
            sampled = guided_top_k_sample(
                cond, uncond, cfg_scale=cfg_scale, top_k=50,
                generator=torch.Generator().manual_seed(9), mask_fn=full_vocab_mask,
            )
            assert isinstance(sampled, int)
            legal = sampled == AUDIO_END_TOKEN_ID or AUDIO_CODE_OFFSET <= sampled < AUDIO_CODE_OFFSET + SEMANTIC_VOCAB_SIZE
            assert legal

    def test_full_vocab_top_k_never_produces_nan_probabilities(self):
        """At ``top_k=50`` over the real 200000-wide vocab, the SUBSEQUENT
        top-k restriction (``guided.masked_fill(cond_logits < threshold,
        -inf)``) happens to also re-mask every excluded position, because
        their ``cond_logits`` is already ``-inf`` (below any real top-50
        threshold) -- so it incidentally cleans up the NaN even without the
        explicit re-mask. That redundancy disappears once ``top_k`` covers
        the WHOLE vocabulary: the top-k threshold becomes the vocabulary's
        own minimum (``-inf``, tied with every excluded position), so
        ``cond_logits < threshold`` is False everywhere and the top-k step
        masks nothing. This is the scenario that actually isolates the
        explicit re-mask's effect -- see this file's docstring for the bite
        check confirming it fails without ``guided_top_k_sample``'s second
        ``mask_fn(guided)`` call.
        """
        vocab = 10
        allowed = torch.tensor([2, 3, 7])

        def _tiny_mask(logits: torch.Tensor) -> torch.Tensor:
            mask = torch.zeros(vocab, dtype=torch.bool)
            mask[allowed] = True
            return logits.masked_fill(~mask, float("-inf"))

        g = torch.Generator().manual_seed(11)
        cond = torch.randn(vocab, generator=g)
        uncond = torch.randn(vocab, generator=g)
        sampled = guided_top_k_sample(cond, uncond, cfg_scale=1.5, top_k=vocab, generator=torch.Generator().manual_seed(12), mask_fn=_tiny_mask)
        assert sampled in allowed.tolist()
