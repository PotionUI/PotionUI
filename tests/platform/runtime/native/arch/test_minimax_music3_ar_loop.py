"""Tiny-weights behavioral tests for the MiniMax-Music3 AR core: KV-cache
correctness, the AR generation loop's contract (frame-0 discard, both
stop-token conventions, cancellation, validation), and the position-budget
guard.
"""

from __future__ import annotations

import torch

from src.platform.runtime.native.arch.minimax_music3 import _ar_timing, ar_loop
from src.platform.runtime.native.arch.minimax_music3.ar_loop import generate, position_budget_warning
from src.platform.runtime.native.arch.minimax_music3.cfg_sampling import full_vocab_mask, guided_top_k_sample
from src.platform.runtime.native.arch.minimax_music3.config import MiniMaxMusic3TextEncoderConfig
from src.platform.runtime.native.arch.minimax_music3.depth_decoder import NUM_RESIDUAL_CODEBOOKS, generate_depth_codes
from src.platform.runtime.native.arch.minimax_music3.lm import MiniMaxMusic3AudioLM
from src.platform.runtime.native.arch.minimax_music3.prompt import AUDIO_CODE_OFFSET, AUDIO_END_TOKEN_ID
from src.platform.runtime.native.arch.minimax_music3._nn import module_device
from src.platform.runtime.native.errors import SamplingCancelled
from vendor.gpl.comfyui.ops import disable_weight_init

import pytest


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


def _randomize(module: torch.nn.Module, seed: int = 0) -> None:
    # std=0.5, not the usual small init scale: the KV-cache equivalence test
    # needs activations large/diffuse enough that a wrong-position cache
    # write produces a divergence the tolerance can actually discriminate
    # from floating-point noise (verified by bite-check -- a smaller std
    # left the corrupted and correct outputs within the same 1e-4 band).
    g = torch.Generator().manual_seed(seed)
    with torch.no_grad():
        for p in module.parameters():
            p.copy_(torch.randn(p.shape, generator=g) * 0.5)


def _build_lm(pruned: bool, seed: int = 0, dtype: torch.dtype = torch.float32) -> MiniMaxMusic3AudioLM:
    cfg = _tiny_config(pruned)
    lm = MiniMaxMusic3AudioLM(cfg, disable_weight_init, dtype=dtype)
    _randomize(lm, seed)
    lm.post_load()
    lm.eval()
    return lm


def _force_stop_token(lm: MiniMaxMusic3AudioLM, input_ids: torch.Tensor) -> None:
    """Overwrite the lm_head so the stop token wins by an overwhelming
    margin, deterministically, for whatever hidden state THIS prompt
    produces. ``input_ids``' two rows must be IDENTICAL (a degenerate,
    unrealistic CFG pair, but this helper is testing the stop-token wiring
    in isolation, not CFG's guidance behavior): a single shared weight row
    cannot be aligned with two INDEPENDENT hidden vectors' sign patterns at
    once, but with both rows equal, ``weight = K * hidden`` gives the SAME
    huge positive ``logits[stop] = K * ||hidden||**2`` on both the
    conditional and unconditional branch, so the CFG combination
    (``uncond + (cond - uncond) * scale``) can't cancel it out -- cond and
    uncond are numerically identical at that position.
    """
    assert torch.equal(input_ids[0], input_ids[1]), "the two rows must be identical for this helper's guarantee to hold"
    with torch.inference_mode():
        probe_cache = lm.new_kv_cache(max_len=input_ids.shape[1] + 1)
        hidden = lm.prefill(input_ids, probe_cache)[0, -1]  # both rows equal here
    scaled = hidden * 1e4
    with torch.no_grad():
        if lm.cfg.pruned_lm_head:
            lm.model.lm_head_pruned.weight.zero_()
            lm.model.lm_head_pruned.weight[0].copy_(scaled)
        else:
            lm.model.lm_head.weight.zero_()
            lm.model.lm_head.weight[AUDIO_END_TOKEN_ID].copy_(scaled)


class TestKVCacheEquivalence:
    """The load-bearing check: incremental (KV-cached, one token at a time)
    forward must be numerically identical to a full-sequence forward over
    the same tokens -- not an approximation, since the cache never drops or
    truncates anything."""

    def test_incremental_step_matches_full_prefill(self):
        lm = _build_lm(pruned=True)
        torch.manual_seed(1)
        prompt_len, extra = 5, 4
        total = prompt_len + extra
        ids = torch.randint(0, 500, (2, total))

        cache_full = lm.new_kv_cache(max_len=total, dtype=torch.float32)
        with torch.inference_mode():
            hidden_full = lm.prefill(ids, cache_full)

        cache_inc = lm.new_kv_cache(max_len=total, dtype=torch.float32)
        with torch.inference_mode():
            lm.prefill(ids[:, :prompt_len], cache_inc)
            step_hiddens = []
            for i in range(prompt_len, total):
                h = lm.step(lm.embed_text(ids[:, i:i + 1]), cache_inc)
                step_hiddens.append(h)
            hidden_inc = torch.cat(step_hiddens, dim=1)

        torch.testing.assert_close(hidden_inc, hidden_full[:, prompt_len:], atol=1e-4, rtol=1e-4)
        assert cache_full.filled_len == total
        assert cache_inc.filled_len == total

    def test_cache_writes_land_at_the_advancing_position_not_position_zero(self):
        """A cache bug that always writes to slot 0 (instead of the current
        ``filled_len``) would make every step attend to a 1-token history
        instead of the real growing prefix -- this diverges from the full
        prefill for any position beyond the second, which the equivalence
        test above already proves doesn't happen; this test additionally
        pins that ``filled_len`` itself advances by exactly one per step."""
        lm = _build_lm(pruned=True, seed=2)
        ids = torch.randint(0, 500, (2, 3))
        cache = lm.new_kv_cache(max_len=6, dtype=torch.float32)
        with torch.inference_mode():
            lm.prefill(ids, cache)
            assert cache.filled_len == 3
            lm.step(lm.embed_text(torch.tensor([[7], [7]])), cache)
            assert cache.filled_len == 4
            lm.step(lm.embed_text(torch.tensor([[8], [8]])), cache)
            assert cache.filled_len == 5


class TestPositionBudgetWarning:
    def test_none_when_within_budget(self):
        assert position_budget_warning(prompt_tokens=100, max_frames=500, max_position_embeddings=10_240) is None

    def test_none_exactly_at_the_boundary(self):
        # prompt_tokens + max_frames + 1 == max_position_embeddings -> fits exactly.
        assert position_budget_warning(prompt_tokens=100, max_frames=9_999 - 100, max_position_embeddings=10_000) is None

    def test_warns_one_past_the_boundary(self):
        msg = position_budget_warning(prompt_tokens=100, max_frames=10_000 - 100, max_position_embeddings=10_000)
        assert msg is not None
        assert "10001" in msg or "10,001" in msg

    def test_message_names_the_overflow(self):
        msg = position_budget_warning(prompt_tokens=6000, max_frames=5000, max_position_embeddings=10_240)
        assert "6000" in msg and "5000" in msg and "10240" in msg


class TestGenerateValidation:
    def test_zero_max_frames_raises(self):
        lm = _build_lm(pruned=True)
        with pytest.raises(ValueError):
            generate(lm, torch.zeros(2, 3, dtype=torch.long), torch.Generator(), max_frames=0)

    def test_negative_max_frames_raises(self):
        lm = _build_lm(pruned=True)
        with pytest.raises(ValueError):
            generate(lm, torch.zeros(2, 3, dtype=torch.long), torch.Generator(), max_frames=-1)

    def test_max_frames_over_the_hard_cap_raises(self):
        lm = _build_lm(pruned=True)
        with pytest.raises(ValueError):
            generate(lm, torch.zeros(2, 3, dtype=torch.long), torch.Generator(), max_frames=9_001)

    def test_wrong_batch_raises(self):
        lm = _build_lm(pruned=True)
        with pytest.raises(ValueError):
            generate(lm, torch.zeros(1, 3, dtype=torch.long), torch.Generator(), max_frames=3)

    def test_prompt_over_the_hard_cap_raises(self):
        lm = _build_lm(pruned=True)
        with pytest.raises(ValueError):
            generate(lm, torch.zeros(2, 5_001, dtype=torch.long), torch.Generator(), max_frames=3)


class TestCancellation:
    def test_is_cancelled_raises_within_one_frame(self):
        lm = _build_lm(pruned=True)
        ids = torch.randint(0, 500, (2, 4))
        with pytest.raises(SamplingCancelled):
            generate(lm, ids, torch.Generator().manual_seed(0), max_frames=5, is_cancelled=lambda: True)


class TestStopTokenConventions:
    def test_pruned_stop_at_index_zero_yields_no_frames(self):
        row = torch.randint(0, 500, (1, 4))
        ids = row.repeat(2, 1)
        lm = _build_lm(pruned=True)
        _force_stop_token(lm, ids)
        out = generate(lm, ids, torch.Generator().manual_seed(0), max_frames=5)
        assert out.shape == (1, 0, 8 * lm.cfg.hidden_size)

    def test_full_stop_at_audio_end_token_id_yields_no_frames(self):
        row = torch.randint(0, 500, (1, 4))
        ids = row.repeat(2, 1)
        lm = _build_lm(pruned=False)
        _force_stop_token(lm, ids)
        out = generate(lm, ids, torch.Generator().manual_seed(0), max_frames=5)
        assert out.shape == (1, 0, 8 * lm.cfg.hidden_size)


def _reference_generate(
    lm: MiniMaxMusic3AudioLM, input_ids: torch.Tensor, generator: torch.Generator,
    max_frames: int, cfg_scale: float = 1.5, top_k: int = 50,
) -> torch.Tensor:
    """Pre-refactor semantics, kept ONLY as a golden reference: every sampled
    code round-trips through a python int (``guided_top_k_sample``'s
    ``.item()``, then straight back into ``torch.tensor([code], ...)`` for
    the next embedding lookup) -- 8 GPU->CPU syncs per frame, the exact shape
    ``ar_loop.py``/``depth_decoder.py`` used to have before the sync-free
    tensor path. Deliberately re-derived here (not imported) so this test
    still exercises the real, current low-level primitives (``lm.prefill``/
    ``lm.step``/``lm.embed_audio_code0``/the depth decoder's own modules) and
    only the sampling glue differs -- proving the refactor is a pure
    representation change, not a numerics change.
    """
    def sample_semantic(hidden: torch.Tensor):
        logits = lm.lm_head_logits(hidden)
        mask_fn = None if lm.cfg.pruned_lm_head else full_vocab_mask
        sampled = guided_top_k_sample(logits[0], logits[1], cfg_scale, top_k, generator, mask_fn=mask_fn)
        if lm.cfg.pruned_lm_head:
            if sampled == 0:
                return None, True
            return sampled - 1, False
        if sampled == AUDIO_END_TOKEN_ID:
            return None, True
        return sampled - AUDIO_CODE_OFFSET, False

    def sample_depth_codes(llm_hidden: torch.Tensor, code0: int):
        decoder = lm.model.audio_decoder
        hidden = llm_hidden.squeeze(1)
        embed_c0 = lm.embed_audio_code0(torch.tensor([code0], device=hidden.device)).expand(2, -1)
        tokens = decoder.projection(torch.stack([hidden, embed_c0], dim=1))
        codes: list[int] = []
        depth_hiddens: list[torch.Tensor] = []
        for i in range(1, NUM_RESIDUAL_CODEBOOKS + 1):
            out = decoder(tokens)
            last = out[:, -1, :]
            depth_hiddens.append(last[0:1])
            logits = decoder.audio_heads[i - 1](last)
            code_i = guided_top_k_sample(logits[0], logits[1], cfg_scale, top_k, generator, mask_fn=None)
            codes.append(code_i)
            if i < NUM_RESIDUAL_CODEBOOKS:
                extra_idx = code_i + (i - 1) * lm.cfg.audio_vocab_size
                embed_i = lm.model.audio_extra_embedding(torch.tensor([extra_idx], device=hidden.device)).expand(2, -1)
                next_token = decoder.projection(embed_i).unsqueeze(1)
                tokens = torch.cat([tokens, next_token], dim=1)
        return codes, torch.cat(depth_hiddens, dim=-1)

    def feedback_embedding(code0: int, codes: list[int]) -> torch.Tensor:
        device = module_device(lm)
        total = lm.embed_audio_code0(torch.tensor([code0], device=device)).squeeze(0)
        for i, code in enumerate(codes, start=1):
            idx = code + (i - 1) * lm.cfg.audio_vocab_size
            total = total + lm.model.audio_extra_embedding(torch.tensor([idx], device=device)).squeeze(0).to(torch.float32)
        return (total * (8.0 ** -0.5)).unsqueeze(0)

    device = module_device(lm)
    cache = lm.new_kv_cache(max_len=input_ids.shape[1] + max_frames + 1, device=device)
    hidden_all = lm.prefill(input_ids.to(device), cache)
    llm_hidden = hidden_all[:, -1, :]

    frame_hiddens: list[torch.Tensor] = []
    for frame_idx in range(max_frames + 1):
        code0, stopped = sample_semantic(llm_hidden)
        if stopped:
            break
        codes, depth_hidden = sample_depth_codes(llm_hidden.unsqueeze(1), code0)
        if frame_idx > 0:
            frame_hiddens.append(torch.cat([llm_hidden[0:1], depth_hidden], dim=-1))
        feedback = feedback_embedding(code0, codes).to(device)
        llm_hidden = lm.step(feedback.unsqueeze(0).expand(2, 1, -1), cache).squeeze(1)

    if not frame_hiddens:
        return torch.zeros(1, 0, 8 * lm.cfg.hidden_size)
    return torch.cat(frame_hiddens, dim=0).unsqueeze(0).cpu()


class TestSyncFreeSamplingBitParity:
    """The refactor's core promise: keeping sampled codes as on-device
    tensors instead of routing every one through a python int must not
    change a single sampled value or hidden state -- same generator, same
    op sequence, same dtype for probs (task contract). Compared against
    :func:`_reference_generate`, the pre-refactor python-int path."""

    @pytest.mark.parametrize("pruned", [True, False])
    def test_new_path_is_bit_identical_to_the_pre_refactor_reference(self, pruned):
        lm = _build_lm(pruned=pruned, seed=21)
        ids = torch.randint(0, 500, (2, 4), generator=torch.Generator().manual_seed(9))
        with torch.inference_mode():
            reference = _reference_generate(lm, ids.clone(), torch.Generator().manual_seed(123), max_frames=5)
        actual = generate(lm, ids.clone(), torch.Generator().manual_seed(123), max_frames=5)
        assert actual.shape == reference.shape
        torch.testing.assert_close(actual, reference, atol=0.0, rtol=0.0)


class TestDepthCodesStayOnDevice:
    """The other half of the contract: no code is ever forced through
    ``.item()``/a fresh ``torch.tensor([python_int], ...)`` inside the hot
    loop -- ``code0`` and ``codes`` stay tensors end to end."""

    def test_sample_semantic_returns_a_tensor_code0(self):
        lm = _build_lm(pruned=True, seed=8)
        ids = torch.randint(0, 500, (2, 4))
        with torch.inference_mode():
            cache = lm.new_kv_cache(max_len=8)
            hidden = lm.prefill(ids, cache)[:, -1, :]
            code0, stopped = ar_loop._sample_semantic(
                lm, hidden, cfg_scale=1.5, top_k=50, generator=torch.Generator().manual_seed(1),
            )
        assert stopped is False
        assert isinstance(code0, torch.Tensor)
        assert code0.dim() == 0

    def test_generate_depth_codes_returns_a_preallocated_tensor(self):
        lm = _build_lm(pruned=True, seed=9)
        ids = torch.randint(0, 500, (2, 4))
        with torch.inference_mode():
            cache = lm.new_kv_cache(max_len=8)
            hidden = lm.prefill(ids, cache)[:, -1, :]
            code0 = torch.tensor(3, device=hidden.device)
            codes, depth_hidden = generate_depth_codes(
                lm, hidden.unsqueeze(1), code0, torch.Generator().manual_seed(2), cfg_scale=1.5, top_k=50,
            )
        assert isinstance(codes, torch.Tensor)
        assert codes.shape == (NUM_RESIDUAL_CODEBOOKS,)
        assert codes.dtype == torch.long
        assert depth_hidden.shape == (1, NUM_RESIDUAL_CODEBOOKS * lm.cfg.hidden_size)


class TestFrameZeroDiscardAndShape:
    def test_normal_run_keeps_at_most_max_frames_and_discards_frame_zero(self):
        lm = _build_lm(pruned=True, seed=5)
        ids = torch.randint(0, 500, (2, 4))
        on_frame_calls = []
        out = generate(
            lm, ids, torch.Generator().manual_seed(3), max_frames=3,
            on_frame=lambda i, m: on_frame_calls.append((i, m)),
        )
        assert out.shape[0] == 1
        assert out.shape[2] == 8 * lm.cfg.hidden_size
        assert out.shape[1] <= 3
        # on_frame is called once per KEPT frame, numbered from 1 (frame 0 discarded).
        assert all(i >= 1 for i, _m in on_frame_calls)
        assert len(on_frame_calls) == out.shape[1]


class TestArTiming:
    """The sub-stage timing added on top of the AR loop: emitted through the
    same ``get_profiler().mark(...)`` mechanism as ``native.move_to`` /
    ``models.acquire.miss``, gated on ``profiling_enabled()`` so a normal
    (non-profiled) run never pays for it."""

    def test_no_events_and_no_cuda_events_when_profiling_is_off(self, monkeypatch):
        monkeypatch.setattr(_ar_timing, "profiling_enabled", lambda: False)

        class ExplodingProfiler:
            def mark(self, *a, **k):
                raise AssertionError("get_profiler().mark() must not be called when profiling is off")

        monkeypatch.setattr(_ar_timing, "get_profiler", lambda: ExplodingProfiler())

        class ExplodingEvent:
            def __init__(self, *a, **k):
                raise AssertionError("torch.cuda.Event must not be constructed when profiling is off")

        monkeypatch.setattr(torch.cuda, "Event", ExplodingEvent, raising=False)

        lm = _build_lm(pruned=True, seed=30)
        ids = torch.randint(0, 500, (2, 4))
        generate(lm, ids, torch.Generator().manual_seed(7), max_frames=3)

    def test_emits_one_row_per_bucket_plus_frame_count_when_profiling_is_on(self, monkeypatch):
        monkeypatch.setattr(_ar_timing, "profiling_enabled", lambda: True)

        marks: list[tuple[str, dict]] = []

        class RecordingProfiler:
            def mark(self, event, **fields):
                marks.append((event, fields))

        monkeypatch.setattr(_ar_timing, "get_profiler", lambda: RecordingProfiler())

        lm = _build_lm(pruned=True, seed=31)
        ids = torch.randint(0, 500, (2, 4))
        max_frames = 4
        out = generate(lm, ids, torch.Generator().manual_seed(11), max_frames=max_frames)
        frame_count = out.shape[1]
        # No stop token forced -> the loop runs every frame_idx up to max_frames.
        assert frame_count == max_frames
        runs = frame_count + 1  # +1: the discarded frame-0 primer, a real iteration too

        by_event = dict(marks)
        assert set(by_event) == {"ar.prefill", "ar.lm_step", "ar.depth", "ar.sampling_feedback", "ar.frames"}

        assert by_event["ar.frames"]["frames"] == frame_count

        prefill = by_event["ar.prefill"]
        assert prefill["calls"] == 1
        assert prefill["cpu_s"] > 0
        assert prefill["gpu_s"] == 0.0  # CPU device: no CUDA events recorded

        for name in ("ar.lm_step", "ar.depth"):
            fields = by_event[name]
            assert fields["calls"] == runs
            assert fields["cpu_s"] > 0
            assert fields["gpu_s"] == 0.0

        # sampling_feedback is entered twice per iteration (semantic sample,
        # then feedback embedding + bookkeeping) -- see ar_loop.generate.
        sampling_feedback = by_event["ar.sampling_feedback"]
        assert sampling_feedback["calls"] == 2 * runs
        assert sampling_feedback["cpu_s"] > 0


class TestSyncCountPerFrame:
    """The whole point of the sync-free refactor: ``.item()`` (a GPU->CPU
    sync on real hardware) fires AT MOST once per AR frame -- the stop-token
    check -- never once per sampled code (the pre-refactor path called it 8
    times/frame: 1 semantic + 7 residual). Counted via a ``torch.Tensor.item``
    monkeypatch so this is exact and device-independent (meaningful on CPU
    too: the call COUNT is what changed, not just its GPU cost)."""

    def test_item_is_called_at_most_once_per_frame(self):
        lm = _build_lm(pruned=True, seed=13)
        ids = torch.randint(0, 500, (2, 4))
        max_frames = 6

        calls = 0
        original_item = torch.Tensor.item

        def counting_item(self):
            nonlocal calls
            calls += 1
            return original_item(self)

        torch.Tensor.item = counting_item
        try:
            generate(lm, ids, torch.Generator().manual_seed(4), max_frames=max_frames)
        finally:
            torch.Tensor.item = original_item

        # One frame-0 (primer, discarded) + up to `max_frames` kept frames,
        # each contributing exactly one `.item()` (the stop check) unless a
        # stop token fires and ends the loop early -- so this is an upper
        # bound, not an exact count, but it pins "at most 1/frame", not 8.
        assert calls <= max_frames + 1


class TestBf16Compute:
    """A ``bfloat16`` checkpoint's ``nn.Linear``s do not self-cast to an
    activation's dtype the way ``int8_convrot``'s weight-quantized ``Linear``
    does (``vendor/gpl/comfyui/ops.py``): every activation reaching a weight
    matmul must already be in the module's own dtype. This class pins that
    contract end to end (prefill, AR step, depth decoder, feedback
    embedding) for both checkpoint layouts -- the crash this guards against
    (``lm.py``'s ``embed_text``/``embed_audio_code0`` hardcoding
    ``.to(torch.float32)`` regardless of the module's real dtype) only shows
    up once the weights themselves stop tolerating a foreign activation
    dtype, which the float32-only tests elsewhere in this file can't catch."""

    @pytest.mark.bf16_cpu_heavy
    @pytest.mark.parametrize("pruned", [True, False])
    def test_generate_runs_end_to_end_in_bf16(self, pruned):
        lm = _build_lm(pruned=pruned, seed=11, dtype=torch.bfloat16)
        ids = torch.randint(0, 500, (2, 4), generator=torch.Generator().manual_seed(5))
        out = generate(lm, ids, torch.Generator().manual_seed(2), max_frames=4)
        assert out.dtype == torch.bfloat16
        assert out.shape[0] == 1
        assert out.shape[2] == 8 * lm.cfg.hidden_size

    def test_embed_text_returns_the_module_dtype_not_hardcoded_float32(self):
        lm = _build_lm(pruned=True, seed=12, dtype=torch.bfloat16)
        out = lm.embed_text(torch.tensor([[1, 2, 3]]))
        assert out.dtype == torch.bfloat16

    def test_embed_audio_code0_returns_the_module_dtype_not_hardcoded_float32(self):
        lm = _build_lm(pruned=True, seed=13, dtype=torch.bfloat16)
        out = lm.embed_audio_code0(torch.tensor([2]))
        assert out.dtype == torch.bfloat16

    @pytest.mark.bf16_cpu_heavy
    def test_incremental_step_matches_full_prefill_in_bf16(self):
        """The KV-cache equivalence contract (see ``TestKVCacheEquivalence``)
        must keep holding once every activation runs at bf16, not just fp32."""
        lm = _build_lm(pruned=True, dtype=torch.bfloat16)
        torch.manual_seed(1)
        prompt_len, extra = 5, 4
        total = prompt_len + extra
        ids = torch.randint(0, 500, (2, total))

        cache_full = lm.new_kv_cache(max_len=total, dtype=torch.bfloat16)
        with torch.inference_mode():
            hidden_full = lm.prefill(ids, cache_full)

        cache_inc = lm.new_kv_cache(max_len=total, dtype=torch.bfloat16)
        with torch.inference_mode():
            lm.prefill(ids[:, :prompt_len], cache_inc)
            step_hiddens = []
            for i in range(prompt_len, total):
                h = lm.step(lm.embed_text(ids[:, i:i + 1]), cache_inc)
                step_hiddens.append(h)
            hidden_inc = torch.cat(step_hiddens, dim=1)

        torch.testing.assert_close(hidden_inc, hidden_full[:, prompt_len:], atol=1e-2, rtol=1e-2)
