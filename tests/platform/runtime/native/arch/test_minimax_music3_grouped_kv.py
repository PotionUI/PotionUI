"""The Music3 global LM attends over grouped K/V without expanding them.

``_GlobalAttention.prefill``/``step`` used to ``repeat_interleave`` the kv heads
up to the query head count before every attention call — in ``step``, over the
whole KV prefix, once per generated frame. Both now go through the shared
grouped seam. The oracle is ``_old_prefill``/``_old_step`` below, verbatim
copies of the pre-change methods, exercised through the real
``MiniMaxMusic3AudioLM.prefill``/``step`` entry points.
"""

from __future__ import annotations

import pytest
import torch

import src.platform.runtime.native.attention as att
from src.platform.runtime.native.arch.minimax_music3._nn import apply_rope
from src.platform.runtime.native.arch.minimax_music3.config import MiniMaxMusic3TextEncoderConfig
from src.platform.runtime.native.arch.minimax_music3.lm import MiniMaxMusic3AudioLM, _GlobalAttention
from src.platform.runtime.native.text_encoders._functional import optimized_attention
from vendor.gpl.comfyui.ops import disable_weight_init


def _old_prefill(self, x, cos, sin, cache_k, cache_v):
    q, k, v = self._qkv(x)
    q, k = apply_rope(q, k, cos, sin)
    length = x.shape[1]
    cache_k[:, :, :length, :] = k.to(cache_k.dtype)
    cache_v[:, :, :length, :] = v.to(cache_v.dtype)
    rep = self.num_heads // self.num_kv_heads
    k_rep = k.repeat_interleave(rep, dim=1)
    v_rep = v.repeat_interleave(rep, dim=1)
    causal = torch.full((length, length), float("-inf"), device=x.device, dtype=x.dtype).triu(1)
    out = optimized_attention(q, k_rep, v_rep, self.num_heads, mask=causal, skip_reshape=True)
    return self.o_proj(out)


def _old_step(self, x, cos, sin, cache_k, cache_v, pos):
    q, k, v = self._qkv(x)
    q, k = apply_rope(q, k, cos, sin)
    cache_k[:, :, pos:pos + 1, :] = k.to(cache_k.dtype)
    cache_v[:, :, pos:pos + 1, :] = v.to(cache_v.dtype)
    rep = self.num_heads // self.num_kv_heads
    k_all = cache_k[:, :, :pos + 1, :].to(q.dtype).repeat_interleave(rep, dim=1)
    v_all = cache_v[:, :, :pos + 1, :].to(q.dtype).repeat_interleave(rep, dim=1)
    out = optimized_attention(q, k_all, v_all, self.num_heads, mask=None, skip_reshape=True)
    return self.o_proj(out)


def _tiny_config() -> MiniMaxMusic3TextEncoderConfig:
    return MiniMaxMusic3TextEncoderConfig(
        hidden_size=32, intermediate_size=24, num_layers=2, head_dim=8,
        num_attention_heads=4, num_key_value_heads=1, rope_theta=10000.0,
        rms_norm_eps=1e-6, max_position_embeddings=64,
        decoder_intermediate_size=20, decoder_num_layers=2, decoder_num_heads=2, decoder_head_dim=8,
        audio_vocab_size=6, num_codebooks=8,
        merged_qkv=True, merged_mlp=True, decoder_merged_qkv=True, decoder_merged_mlp=True,
        pruned_embeddings=True, pruned_lm_head=True,
    )


def _build_lm(seed: int = 0) -> MiniMaxMusic3AudioLM:
    lm = MiniMaxMusic3AudioLM(_tiny_config(), disable_weight_init, dtype=torch.float32)
    g = torch.Generator().manual_seed(seed)
    with torch.no_grad():
        for p in lm.parameters():
            p.copy_(torch.randn(p.shape, generator=g) * 0.5)
    lm.post_load()
    lm.eval()
    return lm


def _prompt(lm: MiniMaxMusic3AudioLM, length: int = 5) -> torch.Tensor:
    vocab = lm.cfg.text_vocab_size if hasattr(lm.cfg, "text_vocab_size") else 6
    g = torch.Generator().manual_seed(7)
    ids = torch.randint(0, min(vocab, 6), (1, length), generator=g)
    return ids.repeat(2, 1)


def _feedback(step: int) -> torch.Tensor:
    g = torch.Generator().manual_seed(1000 + step)
    return torch.randn(2, 1, 32, generator=g) * 0.5


def _run(lm: MiniMaxMusic3AudioLM, decode_steps: int) -> list[torch.Tensor]:
    cache = lm.new_kv_cache(max_len=32, batch=2, device=torch.device("cpu"), dtype=torch.float32)
    outs = [lm.prefill(_prompt(lm), cache)]
    for i in range(decode_steps):
        outs.append(lm.step(_feedback(i), cache))
    return outs


@pytest.fixture(autouse=True)
def _clean_backend_state(monkeypatch):
    monkeypatch.delenv(att.ENV_VAR, raising=False)
    att.set_backend_override(None)
    att.reset_backend_cache()
    yield
    att.set_backend_override(None)
    att.reset_backend_cache()


def test_prefill_and_decode_match_the_expanded_oracle(monkeypatch):
    """Prefill (masked) and four decode positions (unmasked, growing prefix)
    against the pre-change expand-then-attend implementation."""
    lm = _build_lm()
    new = _run(lm, decode_steps=4)

    monkeypatch.setattr(_GlobalAttention, "prefill", _old_prefill)
    monkeypatch.setattr(_GlobalAttention, "step", _old_step)
    old = _run(_build_lm(), decode_steps=4)

    assert len(new) == len(old) == 5
    for i, (a, b) in enumerate(zip(new, old)):
        assert torch.allclose(a, b, atol=1e-6, rtol=1e-5), f"output {i} diverged"


def test_decode_never_expands_the_kv_prefix():
    """The allocation this change exists to avoid: on a grouped-capable
    backend, no step re-materializes the prefix at query-head width."""
    if not att.supports_grouped_kv(att.SDPA):
        pytest.skip("installed torch SDPA has no enable_gqa; only the repeat layout exists")
    lm = _build_lm()
    seen = []
    real_repeat = torch.Tensor.repeat_interleave

    def tracking_repeat(self, *args, **kwargs):
        seen.append(tuple(self.shape))
        return real_repeat(self, *args, **kwargs)

    torch.Tensor.repeat_interleave = tracking_repeat
    try:
        _run(lm, decode_steps=4)
    finally:
        torch.Tensor.repeat_interleave = real_repeat
    assert seen == []


def test_decode_reads_the_cache_prefix_without_copying_it():
    """A cache stored in the compute dtype must reach the kernel as a view."""
    lm = _build_lm()
    cache = lm.new_kv_cache(max_len=32, batch=2, device=torch.device("cpu"), dtype=torch.float32)
    lm.prefill(_prompt(lm), cache)

    storages = {c.data_ptr() for c in cache.keys} | {c.data_ptr() for c in cache.values}
    seen = []

    real = att.grouped_attention

    def recording(q, k, v, **kwargs):
        seen.append((k.data_ptr(), v.data_ptr()))
        return real(q, k, v, **kwargs)

    import src.platform.runtime.native.arch.minimax_music3.lm as lm_mod
    lm_mod.grouped_attention = recording
    try:
        lm.step(_feedback(0), cache)
    finally:
        lm_mod.grouped_attention = real

    assert seen, "step did not reach the grouped seam"
    for k_ptr, v_ptr in seen:
        assert k_ptr in storages and v_ptr in storages


def test_lm_attention_stays_on_sdpa_whatever_the_dit_is_pinned_to(monkeypatch):
    """The AR core must not follow a global pin onto a quantizing kernel."""
    chosen = []
    real = att._resolve_dispatch
    monkeypatch.setattr(att, "_resolve_dispatch",
                        lambda q, mask, backend: chosen.append(backend) or real(q, mask, backend))
    monkeypatch.setattr(att, "_get_availability",
                        lambda device_index=None: {att.SDPA: True, att.SAGE: True, att.SAGE2: True})
    att.set_backend_override("sage2")

    _run(_build_lm(), decode_steps=2)
    assert chosen and set(chosen) == {"sdpa"}
