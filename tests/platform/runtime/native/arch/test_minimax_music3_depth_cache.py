"""Tiny-weights tests for the MiniMax-Music3 depth decoder's per-frame KV
cache: numerical equivalence with the whole-prefix reference path, the
reset-per-frame / release-on-every-exit contract, and its bounds.
"""

from __future__ import annotations

import contextlib

import pytest
import torch

from src.platform.runtime.native.arch.minimax_music3 import ar_loop
from src.platform.runtime.native.arch.minimax_music3.ar_loop import generate
from src.platform.runtime.native.arch.minimax_music3.cfg_sampling import guided_top_k_sample_id
from src.platform.runtime.native.arch.minimax_music3.config import MiniMaxMusic3TextEncoderConfig
from src.platform.runtime.native.arch.minimax_music3.depth_decoder import (
    MAX_DEPTH_TOKENS,
    NUM_RESIDUAL_CODEBOOKS,
    DepthKVCache,
    depth_kv_cache,
    generate_depth_codes,
)
from src.platform.runtime.native.arch.minimax_music3.lm import MiniMaxMusic3AudioLM
from src.platform.runtime.native.errors import SamplingCancelled
from vendor.gpl.comfyui.ops import disable_weight_init


def _tiny_config(pruned: bool) -> MiniMaxMusic3TextEncoderConfig:
    return MiniMaxMusic3TextEncoderConfig(
        hidden_size=16, intermediate_size=24, num_layers=2, head_dim=8,
        num_attention_heads=2, num_key_value_heads=1, rope_theta=10000.0,
        rms_norm_eps=1e-6, max_position_embeddings=64,
        decoder_intermediate_size=20, decoder_num_layers=2, decoder_num_heads=2, decoder_head_dim=8,
        audio_vocab_size=6, num_codebooks=8,
        merged_qkv=pruned, merged_mlp=pruned, decoder_merged_qkv=pruned, decoder_merged_mlp=pruned,
        pruned_embeddings=pruned, pruned_lm_head=pruned,
    )


def _build_lm(pruned: bool, seed: int = 0, dtype: torch.dtype = torch.float32) -> MiniMaxMusic3AudioLM:
    cfg = _tiny_config(pruned)
    lm = MiniMaxMusic3AudioLM(cfg, disable_weight_init, dtype=dtype)
    g = torch.Generator().manual_seed(seed)
    with torch.no_grad():
        for p in lm.parameters():
            p.copy_((torch.randn(p.shape, generator=g) * 0.5).to(dtype))
    lm.post_load()
    lm.eval()
    return lm


def _seed_hidden(lm: MiniMaxMusic3AudioLM, prompt_len: int = 4, seed: int = 4) -> torch.Tensor:
    ids = torch.randint(0, 500, (2, prompt_len), generator=torch.Generator().manual_seed(seed))
    with torch.inference_mode():
        cache = lm.new_kv_cache(max_len=prompt_len + 2, dtype=lm.model.norm.weight.dtype)
        return lm.prefill(ids, cache)[:, -1, :].unsqueeze(1)  # [2, 1, hidden]


def _reference_depth_codes(lm, llm_hidden, code0, generator, cfg_scale, top_k):
    """The whole-prefix depth path, verbatim as it stood before the cache:
    every codebook re-runs the decoder over the entire growing prefix, with
    no per-frame state. Kept here (not imported) so the cached path is
    checked against an independent implementation of the same recipe, and so
    the reference survives any later edit to the production function.

    Additionally returns the per-step ``(last_hidden, logits)`` pairs the
    production function does not expose, so the equivalence test can compare
    each prefix's own head input and head output, not just the concatenation.
    """
    decoder = lm.model.audio_decoder
    hidden = llm_hidden.squeeze(1)

    embed_c0 = lm.embed_audio_code0(code0.reshape(1)).expand(2, -1)
    tokens = decoder.projection(torch.stack([hidden, embed_c0], dim=1))

    codes = torch.empty(NUM_RESIDUAL_CODEBOOKS, dtype=torch.long, device=hidden.device)
    depth_hiddens: list[torch.Tensor] = []
    per_step: list[tuple[torch.Tensor, torch.Tensor]] = []
    for i in range(1, NUM_RESIDUAL_CODEBOOKS + 1):
        out = decoder(tokens)
        last = out[:, -1, :]
        depth_hiddens.append(last[0:1])
        logits = decoder.audio_heads[i - 1](last)
        per_step.append((last.clone(), logits.clone()))
        code_i = guided_top_k_sample_id(logits[0], logits[1], cfg_scale, top_k, generator, mask_fn=None)
        codes[i - 1] = code_i
        if i < NUM_RESIDUAL_CODEBOOKS:
            extra_idx = code_i + (i - 1) * lm.cfg.audio_vocab_size
            embed_i = lm.model.audio_extra_embedding(extra_idx.reshape(1)).expand(2, -1)
            tokens = torch.cat([tokens, decoder.projection(embed_i).unsqueeze(1)], dim=1)

    return codes, torch.cat(depth_hiddens, dim=-1), per_step


class TestCachedDepthMatchesWholePrefix:
    """The load-bearing check: the cached path's per-prefix hidden states,
    per-prefix head logits and sampled codes must match the whole-prefix
    reference — over both projection layouts."""

    @pytest.mark.parametrize("pruned", [True, False])
    def test_codes_and_hiddens_match_the_reference(self, pruned):
        lm = _build_lm(pruned=pruned, seed=11)
        llm_hidden = _seed_hidden(lm)
        code0 = torch.tensor(2)

        with torch.inference_mode():
            ref_codes, ref_hidden, _ = _reference_depth_codes(
                lm, llm_hidden, code0, torch.Generator().manual_seed(77), 1.5, 50,
            )
            with depth_kv_cache() as cache:
                codes, depth_hidden = generate_depth_codes(
                    lm, llm_hidden, code0, torch.Generator().manual_seed(77), 1.5, 50, cache=cache,
                )

        assert torch.equal(codes, ref_codes)
        torch.testing.assert_close(depth_hidden, ref_hidden, atol=1e-5, rtol=1e-5)

    @pytest.mark.parametrize("pruned", [True, False])
    def test_each_prefix_final_hidden_and_logits_match_the_reference(self, pruned):
        """Per codebook, not just in aggregate: the conditional row's hidden
        for prefix ``i`` is ``depth_hidden``'s ``i``-th slice, and running it
        back through that codebook's own head must reproduce the reference's
        conditional logits for the same prefix."""
        lm = _build_lm(pruned=pruned, seed=12)
        llm_hidden = _seed_hidden(lm, seed=6)
        code0 = torch.tensor(1)
        hidden_size = lm.cfg.hidden_size

        with torch.inference_mode():
            _, _, per_step = _reference_depth_codes(
                lm, llm_hidden, code0, torch.Generator().manual_seed(5), 1.5, 50,
            )
            with depth_kv_cache() as cache:
                _, depth_hidden = generate_depth_codes(
                    lm, llm_hidden, code0, torch.Generator().manual_seed(5), 1.5, 50, cache=cache,
                )
            for i, (ref_last, ref_logits) in enumerate(per_step):
                got_hidden = depth_hidden[:, i * hidden_size:(i + 1) * hidden_size]
                torch.testing.assert_close(got_hidden, ref_last[0:1], atol=1e-5, rtol=1e-5)
                got_logits = lm.model.audio_decoder.audio_heads[i](got_hidden)
                torch.testing.assert_close(got_logits, ref_logits[0:1], atol=1e-5, rtol=1e-5)

    def test_both_cfg_rows_are_carried_through_the_cache(self):
        """A cache that kept only the conditional row would still produce a
        7-code tensor and a right-shaped depth hidden; it would sample
        different codes, because the unconditional row's logits feed the CFG
        combination. Two prompts whose rows differ are enough to catch it."""
        lm = _build_lm(pruned=True, seed=13)
        llm_hidden = _seed_hidden(lm, seed=8)
        assert not torch.allclose(llm_hidden[0], llm_hidden[1])
        code0 = torch.tensor(4)

        with torch.inference_mode():
            ref_codes, _, _ = _reference_depth_codes(
                lm, llm_hidden, code0, torch.Generator().manual_seed(31), 3.0, 4,
            )
            with depth_kv_cache() as cache:
                codes, _ = generate_depth_codes(
                    lm, llm_hidden, code0, torch.Generator().manual_seed(31), 3.0, 4, cache=cache,
                )
        assert torch.equal(codes, ref_codes)

    def test_bf16_run_matches_the_reference_codes(self):
        lm = _build_lm(pruned=True, seed=14, dtype=torch.bfloat16)
        llm_hidden = _seed_hidden(lm, seed=2)
        code0 = torch.tensor(3)
        with torch.inference_mode():
            ref_codes, _, _ = _reference_depth_codes(
                lm, llm_hidden, code0, torch.Generator().manual_seed(19), 1.5, 50,
            )
            with depth_kv_cache() as cache:
                codes, depth_hidden = generate_depth_codes(
                    lm, llm_hidden, code0, torch.Generator().manual_seed(19), 1.5, 50, cache=cache,
                )
                assert cache.keys[0].dtype == torch.bfloat16
        assert torch.equal(codes, ref_codes)
        assert depth_hidden.dtype == torch.bfloat16


def _capture_cache(monkeypatch) -> list[DepthKVCache]:
    """Hand the AR loop a real depth cache the test keeps a reference to, so
    its state can be inspected after ``generate`` has returned or raised."""
    held: list[DepthKVCache] = []
    real = ar_loop.depth_kv_cache

    @contextlib.contextmanager
    def capturing():
        with real() as cache:
            held.append(cache)
            yield cache

    monkeypatch.setattr(ar_loop, "depth_kv_cache", capturing)
    return held


class TestMultiFrameThroughTheRealArLoop:
    @pytest.mark.parametrize("pruned", [True, False])
    def test_seeded_run_matches_an_uncached_run_of_the_same_loop(self, pruned, monkeypatch):
        """The reference here is the REAL AR loop with the cache argument
        dropped at the call seam — same frames, same generator, same LM KV
        cache, only the depth path uncached."""
        lm = _build_lm(pruned=pruned, seed=15)
        ids = torch.randint(0, 500, (2, 4), generator=torch.Generator().manual_seed(3))

        real = ar_loop.generate_depth_codes
        monkeypatch.setattr(
            ar_loop, "generate_depth_codes",
            lambda *args, cache=None, **kw: real(*args, **kw),
        )
        reference = generate(lm, ids.clone(), torch.Generator().manual_seed(101), max_frames=4)
        monkeypatch.undo()

        actual = generate(lm, ids.clone(), torch.Generator().manual_seed(101), max_frames=4)
        assert actual.shape == reference.shape
        assert actual.shape[1] > 1
        torch.testing.assert_close(actual, reference, atol=1e-5, rtol=1e-5)

    def test_cache_is_reset_between_frames(self, monkeypatch):
        """Without the per-frame reset the second frame would start at
        ``filled_len == 8`` and overflow; this pins that every frame sees a
        rewound cache and leaves it exactly full."""
        lm = _build_lm(pruned=True, seed=16)
        ids = torch.randint(0, 500, (2, 4), generator=torch.Generator().manual_seed(3))

        seen: list[tuple[int, int]] = []
        real = ar_loop.generate_depth_codes

        def spy(*args, cache=None, **kw):
            before = cache.filled_len
            out = real(*args, cache=cache, **kw)
            seen.append((before, cache.filled_len))
            return out

        monkeypatch.setattr(ar_loop, "generate_depth_codes", spy)
        generate(lm, ids, torch.Generator().manual_seed(102), max_frames=3)

        assert len(seen) >= 3
        assert all(after == MAX_DEPTH_TOKENS for _, after in seen)
        assert [before for before, _ in seen[1:]] == [MAX_DEPTH_TOKENS] * (len(seen) - 1)

    def test_buffers_are_released_when_the_run_completes(self, monkeypatch):
        lm = _build_lm(pruned=True, seed=17)
        ids = torch.randint(0, 500, (2, 4), generator=torch.Generator().manual_seed(3))
        held = _capture_cache(monkeypatch)
        generate(lm, ids, torch.Generator().manual_seed(103), max_frames=2)
        assert held[0].released and held[0].keys == []

    def test_buffers_are_released_on_cancellation(self, monkeypatch):
        lm = _build_lm(pruned=True, seed=18)
        ids = torch.randint(0, 500, (2, 4), generator=torch.Generator().manual_seed(3))
        held = _capture_cache(monkeypatch)
        calls = {"n": 0}

        def cancel_after_one_frame() -> bool:
            calls["n"] += 1
            return calls["n"] > 1

        with pytest.raises(SamplingCancelled):
            generate(lm, ids, torch.Generator().manual_seed(104), max_frames=5,
                     is_cancelled=cancel_after_one_frame)
        assert held[0].released and held[0].keys == []

    def test_buffers_are_released_when_a_frame_raises(self, monkeypatch):
        lm = _build_lm(pruned=True, seed=19)
        ids = torch.randint(0, 500, (2, 4), generator=torch.Generator().manual_seed(3))
        held = _capture_cache(monkeypatch)

        def boom(*args, **kw):
            raise RuntimeError("depth step exploded")

        monkeypatch.setattr(ar_loop, "generate_depth_codes", boom)
        with pytest.raises(RuntimeError, match="exploded"):
            generate(lm, ids, torch.Generator().manual_seed(105), max_frames=2)
        assert held[0].released and held[0].keys == []


class TestCacheBounds:
    def test_context_manager_releases_on_error(self):
        held: list[DepthKVCache] = []
        with pytest.raises(ValueError):
            with depth_kv_cache() as cache:
                held.append(cache)
                cache.keys = [torch.zeros(1)]
                raise ValueError("boom")
        assert held[0].released and held[0].keys == []

    def test_released_cache_cannot_be_reused(self):
        lm = _build_lm(pruned=True, seed=20)
        llm_hidden = _seed_hidden(lm)
        with depth_kv_cache() as cache:
            pass
        with torch.inference_mode(), pytest.raises(RuntimeError, match="after release"):
            generate_depth_codes(lm, llm_hidden, torch.tensor(0), torch.Generator().manual_seed(1),
                                 1.5, 50, cache=cache)

    def test_overflowing_the_bounded_buffer_raises(self):
        lm = _build_lm(pruned=True, seed=21)
        decoder = lm.model.audio_decoder
        tokens = torch.zeros(2, 1, lm.cfg.hidden_size)
        with torch.inference_mode(), depth_kv_cache() as cache:
            decoder(torch.zeros(2, 2, lm.cfg.hidden_size), cache)
            for _ in range(MAX_DEPTH_TOKENS - 2):
                decoder(tokens, cache)
            assert cache.filled_len == MAX_DEPTH_TOKENS
            with pytest.raises(ValueError, match="overflow"):
                decoder(tokens, cache)

    def test_retained_bytes_covers_only_the_bounded_buffers(self):
        lm = _build_lm(pruned=True, seed=22)
        cfg = lm.cfg
        llm_hidden = _seed_hidden(lm)
        with torch.inference_mode(), depth_kv_cache() as cache:
            generate_depth_codes(lm, llm_hidden, torch.tensor(0), torch.Generator().manual_seed(1),
                                 1.5, 50, cache=cache)
            expected = (2 * cfg.decoder_num_heads * MAX_DEPTH_TOKENS * cfg.decoder_head_dim
                        * cfg.decoder_num_layers * 2 * 4)
            assert cache.retained_bytes() == expected
            assert len(cache.keys) == cfg.decoder_num_layers
            assert cache.keys[0].shape == (2, cfg.decoder_num_heads, MAX_DEPTH_TOKENS, cfg.decoder_head_dim)


class TestUncachedPathStillWorks:
    def test_no_cache_argument_runs_the_whole_prefix_path(self):
        lm = _build_lm(pruned=True, seed=23)
        llm_hidden = _seed_hidden(lm)
        with torch.inference_mode():
            codes, depth_hidden = generate_depth_codes(
                lm, llm_hidden, torch.tensor(5), torch.Generator().manual_seed(41), 1.5, 50,
            )
        assert codes.shape == (NUM_RESIDUAL_CODEBOOKS,)
        assert depth_hidden.shape == (1, NUM_RESIDUAL_CODEBOOKS * lm.cfg.hidden_size)
