"""LTX-2 RoPE + compressed-timestep infrastructure tests (Task #28 increment 1)."""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from vendor.gpl.comfyui.ltx.rope import (  # noqa: E402
    CompressedTimestep,
    apply_rotary_emb,
    build_freqs_cis_chunked,
    freq_feature_dim,
    generate_freq_grid_np,
    generate_freqs,
    get_fractional_positions,
    interleaved_freqs_cis,
    split_freqs_cis,
)


def test_interleaved_rotary_identity():
    x = torch.randn(1, 2, 4, 8)
    cos = torch.ones(1, 1, 4, 8)
    sin = torch.zeros(1, 1, 4, 8)
    assert torch.allclose(apply_rotary_emb(x, (cos, sin, False)), x)


def test_interleaved_rotary_quarter_turn():
    # cos=0, sin=1 rotates each interleaved pair (a, b) -> (-b, a).
    x = torch.tensor([[[[1.0, 2.0, 3.0, 4.0]]]])
    cos = torch.zeros(1, 1, 1, 4)
    sin = torch.ones(1, 1, 1, 4)
    out = apply_rotary_emb(x, (cos, sin, False))
    assert out.flatten().tolist() == [-2.0, 1.0, -4.0, 3.0]


def test_compressed_timestep_roundtrip():
    b, frames, patches, feat = 1, 2, 3, 4
    per_frame = (
        torch.randn(b, frames, 1, feat)
        .expand(b, frames, patches, feat)
        .reshape(b, frames * patches, feat)
        .contiguous()
    )
    ct = CompressedTimestep(per_frame, patches)
    assert ct.num_frames == frames and ct.patches_per_frame == patches
    assert torch.allclose(ct.expand(), per_frame)


def test_compressed_timestep_no_compression_when_indivisible():
    ct = CompressedTimestep(torch.randn(1, 7, 4), patches_per_frame=3)  # 7 % 3 != 0
    assert ct.patches_per_frame == 1 and ct.num_frames == 7


def test_expand_for_computation_shapes():
    # timestep feature_dim = n_params * per-param dim; the table is [n_params, dim].
    b, tokens, dim, n_params = 1, 6, 4, 2
    ct = CompressedTimestep(torch.randn(b, tokens, dim * n_params), patches_per_frame=3)
    table = torch.randn(n_params, dim)
    vals = ct.expand_for_computation(table, b)
    assert len(vals) == n_params
    assert all(v.shape == (b, tokens, dim) for v in vals)


def test_freqs_cis_shapes():
    grid = generate_freq_grid_np(10000.0, 3, 96)
    assert grid.shape == (16,)  # inner_dim // (2 * max_pos_count)
    indices_grid = torch.arange(6).reshape(1, 1, 6).float().expand(1, 3, 6).contiguous()
    freqs = generate_freqs(grid, indices_grid, [20, 2048, 2048])
    cos_i, sin_i = interleaved_freqs_cis(freqs, 0)
    assert cos_i.shape == sin_i.shape == (1, 6, 96)
    cos_s, sin_s = split_freqs_cis(freqs, 0, 4)
    assert cos_s.shape == sin_s.shape == (1, 4, 6, 12)   # (B, heads, T, halfdim/heads)


def test_split_rotary_flat_input_batch_comes_from_x_not_cos():
    """Regression (quality mode, 2026-07-15): the connector builds cos/sin with a
    batch dim of 1 (broadcast), but ``apply_split_rotary_emb``'s flat-input
    reshape took the batch size from COS — folding a batch-2 pos+neg encode into
    the head dim ("size of tensor a (128) must match b (64) at dimension 4").
    Batch-2 must equal the two per-sample results."""
    from vendor.gpl.comfyui.ltx.rope import apply_split_rotary_emb

    torch.manual_seed(7)
    heads, dim_head, seq = 4, 16, 6
    x2 = torch.randn(2, seq, heads * dim_head)
    cos = torch.randn(1, heads, seq, dim_head // 2)
    sin = torch.randn(1, heads, seq, dim_head // 2)

    out_batched = apply_split_rotary_emb(x2, cos, sin)
    assert out_batched.shape == x2.shape

    for b in range(2):
        out_single = apply_split_rotary_emb(x2[b : b + 1].clone(), cos, sin)
        assert torch.allclose(out_batched[b], out_single[0], atol=1e-6), f"sample {b} diverged"


# -- chunked construction bit-exactness + per-generation cache ----------------
#
# Long-video LTX (S≈110,880 tokens at 720x1280) rebuilds the fp32 RoPE cos/sin
# tables from scratch every denoise step, a ~2.5GB fp32 transient (freqs + cos +
# sin, inner_dim=2048) that contributes to the long-video OOM. Two independent
# fixes, both required to be numerically inert:
#   1. build_freqs_cis_chunked: builds the tables token-chunk at a time, casting
#      each chunk to the target dtype before the next chunk is built, capping the
#      fp32 transient at chunk_tokens regardless of S.
#   2. LTXAVModel / Embeddings1DConnector cache their finished cos/sin across
#      denoise steps (grid shape + fps/dtype/device/split-mode is invariant for
#      the whole generation), so construction runs once per generation instead
#      of once per forward.
#
# The reference below is the ORIGINAL (pre-chunking) construction, reimplemented
# inline from unchanged primitives (get_fractional_positions, generate_freq_grid_np,
# interleaved_freqs_cis, split_freqs_cis) — NOT calling generate_freqs/
# build_freqs_cis_chunked themselves, so a regression in either can't hide from
# its own reference.

def _reference_freqs_cis(indices, indices_grid, max_pos, pad_size, out_dtype,
                          use_middle_indices_grid, split_mode, num_attention_heads):
    grid = indices_grid
    if use_middle_indices_grid:
        start, end = grid[..., 0], grid[..., 1]
        grid = (start + end) / 2.0
    elif len(grid.shape) == 4:
        grid = grid[..., 0]
    fractional = get_fractional_positions(grid, max_pos)
    idx = indices.to(device=fractional.device)
    freqs = (idx * (fractional.unsqueeze(-1) * 2 - 1)).transpose(-1, -2).flatten(2)
    if split_mode:
        cos, sin = split_freqs_cis(freqs, pad_size, num_attention_heads)
    else:
        cos, sin = interleaved_freqs_cis(freqs, pad_size)
    return cos.to(out_dtype), sin.to(out_dtype)


@pytest.mark.parametrize("chunk_tokens", [16, 1024])
def test_chunked_matches_reference_interleaved(chunk_tokens):
    torch.manual_seed(0)
    n_pos_dims, inner_dim, T = 3, 96, 40
    grid = torch.rand(1, n_pos_dims, T) * 50
    indices = generate_freq_grid_np(10000.0, n_pos_dims, inner_dim)
    max_pos = [64.0, 64.0, 64.0]
    pad = inner_dim % (2 * n_pos_dims)

    ref_cos, ref_sin = _reference_freqs_cis(
        indices, grid, max_pos, pad, torch.bfloat16,
        use_middle_indices_grid=False, split_mode=False, num_attention_heads=None,
    )
    got_cos, got_sin = build_freqs_cis_chunked(
        indices, grid, max_pos, pad, torch.bfloat16,
        use_middle_indices_grid=False, split_mode=False, chunk_tokens=chunk_tokens,
    )
    assert torch.equal(ref_cos, got_cos)
    assert torch.equal(ref_sin, got_sin)


@pytest.mark.parametrize("chunk_tokens", [16, 1024])
def test_chunked_matches_reference_split_mode(chunk_tokens):
    torch.manual_seed(1)
    n_pos_dims, inner_dim, T, heads = 3, 96, 40, 4
    grid = torch.rand(1, n_pos_dims, T) * 50
    indices = generate_freq_grid_np(10000.0, n_pos_dims, inner_dim)
    max_pos = [64.0, 64.0, 64.0]
    pad = inner_dim // 2 - freq_feature_dim(indices, n_pos_dims)

    ref_cos, ref_sin = _reference_freqs_cis(
        indices, grid, max_pos, pad, torch.bfloat16,
        use_middle_indices_grid=False, split_mode=True, num_attention_heads=heads,
    )
    got_cos, got_sin = build_freqs_cis_chunked(
        indices, grid, max_pos, pad, torch.bfloat16,
        use_middle_indices_grid=False, split_mode=True, num_attention_heads=heads,
        chunk_tokens=chunk_tokens,
    )
    assert torch.equal(ref_cos, got_cos)
    assert torch.equal(ref_sin, got_sin)


def test_chunked_matches_reference_with_middle_indices_grid():
    """4-D (start, end)-paired grid, use_middle_indices_grid=True — the real
    model's default (RoPE positions sample the patch/frame CENTER)."""
    torch.manual_seed(2)
    n_pos_dims, inner_dim, T, heads = 3, 64, 30, 2
    starts = torch.rand(1, n_pos_dims, T) * 50
    grid = torch.stack([starts, starts + 1.0], dim=-1)  # (1, n_pos_dims, T, 2)
    indices = generate_freq_grid_np(10000.0, n_pos_dims, inner_dim)
    max_pos = [40.0, 40.0, 40.0]
    pad = inner_dim // 2 - freq_feature_dim(indices, n_pos_dims)

    ref_cos, ref_sin = _reference_freqs_cis(
        indices, grid, max_pos, pad, torch.bfloat16,
        use_middle_indices_grid=True, split_mode=True, num_attention_heads=heads,
    )
    got_cos, got_sin = build_freqs_cis_chunked(
        indices, grid, max_pos, pad, torch.bfloat16,
        use_middle_indices_grid=True, split_mode=True, num_attention_heads=heads,
        chunk_tokens=7,
    )
    assert torch.equal(ref_cos, got_cos)
    assert torch.equal(ref_sin, got_sin)


def test_chunked_matches_reference_large_multi_chunk_sequence():
    """S≈20k, exercising the default chunk size (8192) across >1 chunk boundary
    with a non-uniform final chunk (20_000 % 8192 != 0)."""
    torch.manual_seed(3)
    n_pos_dims, inner_dim, T, heads = 3, 128, 20_000, 8
    grid = torch.rand(1, n_pos_dims, T) * 100
    indices = generate_freq_grid_np(10000.0, n_pos_dims, inner_dim)
    max_pos = [128.0, 128.0, 128.0]
    pad = inner_dim // 2 - freq_feature_dim(indices, n_pos_dims)

    ref_cos, ref_sin = _reference_freqs_cis(
        indices, grid, max_pos, pad, torch.bfloat16,
        use_middle_indices_grid=False, split_mode=True, num_attention_heads=heads,
    )
    got_cos, got_sin = build_freqs_cis_chunked(
        indices, grid, max_pos, pad, torch.bfloat16,
        use_middle_indices_grid=False, split_mode=True, num_attention_heads=heads,
    )  # default chunk_tokens
    assert torch.equal(ref_cos, got_cos)
    assert torch.equal(ref_sin, got_sin)
    assert T % 8192 != 0  # sanity: the test actually exercises a ragged last chunk


def test_freq_feature_dim_matches_generate_freqs_last_dim():
    """freq_feature_dim's analytic shape must match generate_freqs's actual
    last-dim size — the pad-size computation depends on this equivalence."""
    n_pos_dims, inner_dim, T = 3, 96, 10
    grid = torch.rand(1, n_pos_dims, T) * 50
    indices = generate_freq_grid_np(10000.0, n_pos_dims, inner_dim)
    freqs = generate_freqs(indices, grid, [64.0, 64.0, 64.0])
    assert freqs.shape[-1] == freq_feature_dim(indices, n_pos_dims)


# -- Per-generation positional-embedding cache (LTXAVModel + Embeddings1DConnector)

def _tiny_ltxav_config() -> dict:
    return {
        "image_model": "ltxav", "in_channels": 8, "out_channels": 8,
        "num_attention_heads": 2, "attention_head_dim": 4, "cross_attention_dim": 8,
        "caption_channels": 12, "num_layers": 1,
        "audio_num_attention_heads": 2, "audio_attention_head_dim": 4,
        "audio_cross_attention_dim": 8, "audio_in_channels": 128,
        "has_caption_projection": True,
        "use_embeddings_connector": True, "connector_attention_head_dim": 4,
        "video_connector_inner": 8, "audio_connector_inner": 8, "connector_num_layers": 1,
        "connector_num_learnable_registers": 4,
        "blocks_gated": False, "has_prompt_adaln": False,
    }


def _build_tiny_ltxav_model():
    from src.platform.runtime.native.arch.ltx.model import LTXAVModel
    from vendor.gpl.comfyui.ops import pick_operations

    ops = pick_operations(torch.float32, torch.float32)
    m = LTXAVModel.from_config(_tiny_ltxav_config(), ops)
    with torch.no_grad():
        for p in m.parameters():
            p.copy_(torch.randn_like(p) * 0.02)
    return m.eval()


def _tiny_pixel_coords():
    """(video, audio) coord tensors matching the real patchifier output shape
    (SymmetricPatchifier/AudioPatchifier, start_end=True): video is
    (B, 3, T_v, 2) start/end pairs (frame/h/w), audio is (B, 1, T_a, 2)
    (single time axis)."""
    t_v = 6
    v_start = torch.stack([
        torch.arange(t_v, dtype=torch.float32) // 2,
        torch.arange(t_v, dtype=torch.float32) % 2,
        torch.zeros(t_v),
    ]).unsqueeze(0)  # (1, 3, T_v)
    v = torch.stack([v_start, v_start + 1.0], dim=-1)  # (1, 3, T_v, 2)
    t_a = 4
    a_start = torch.arange(t_a, dtype=torch.float32).unsqueeze(0).unsqueeze(0)  # (1, 1, T_a)
    a = torch.stack([a_start, a_start + 1.0], dim=-1)  # (1, 1, T_a, 2)
    return [v, a]


def test_ltxav_model_pe_cache_hit_skips_reconstruction(monkeypatch):
    from src.platform.runtime.native.arch.ltx import model as ltx_model

    m = _build_tiny_ltxav_model()
    calls = {"n": 0}
    original = ltx_model.rope.build_freqs_cis_chunked

    def counting(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(ltx_model.rope, "build_freqs_cis_chunked", counting)

    pixel_coords = _tiny_pixel_coords()
    first = m._prepare_positional_embeddings(pixel_coords, 25.0, torch.float32)
    assert calls["n"] == 4  # v_pe, a_pe, av_cross_video, av_cross_audio

    second = m._prepare_positional_embeddings(pixel_coords, 25.0, torch.float32)
    assert calls["n"] == 4, "identical inputs must hit the cache, not rebuild"
    # Cache hit returns the SAME cached object, not merely equal values.
    assert second is first
    for (v_pe_a, cross_a), (v_pe_b, cross_b) in zip(first, second):
        assert v_pe_a[0] is v_pe_b[0] and v_pe_a[1] is v_pe_b[1]


@pytest.mark.parametrize("mutate", ["frame_rate", "dtype", "shape", "split"])
def test_ltxav_model_pe_cache_rebuilds_on_signature_change(monkeypatch, mutate):
    from src.platform.runtime.native.arch.ltx import model as ltx_model

    m = _build_tiny_ltxav_model()
    calls = {"n": 0}
    original = ltx_model.rope.build_freqs_cis_chunked

    def counting(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(ltx_model.rope, "build_freqs_cis_chunked", counting)

    pixel_coords = _tiny_pixel_coords()
    m._prepare_positional_embeddings(pixel_coords, 25.0, torch.float32)
    assert calls["n"] == 4

    if mutate == "frame_rate":
        m._prepare_positional_embeddings(_tiny_pixel_coords(), 30.0, torch.float32)
    elif mutate == "dtype":
        m._prepare_positional_embeddings(_tiny_pixel_coords(), 25.0, torch.bfloat16)
    elif mutate == "shape":
        v, a = _tiny_pixel_coords()
        v = torch.cat([v, v[:, :, :1, :]], dim=2)  # one extra token (dim 2 = T)
        m._prepare_positional_embeddings([v, a], 25.0, torch.float32)
    elif mutate == "split":
        import dataclasses

        m.config = dataclasses.replace(m.config, rope_split=not m.config.rope_split)
        m._prepare_positional_embeddings(_tiny_pixel_coords(), 25.0, torch.float32)

    assert calls["n"] == 8, f"changing {mutate} must bust the cache and rebuild"


def test_ltxav_model_pe_cache_dropped_on_device_or_dtype_move():
    m = _build_tiny_ltxav_model()
    pixel_coords = _tiny_pixel_coords()
    m._prepare_positional_embeddings(pixel_coords, 25.0, torch.float32)
    assert m._pe_cache is not None and m._pe_cache_key is not None

    m.to(torch.float32)  # any .to() call runs _apply, even a same-dtype no-op move
    assert m._pe_cache is None and m._pe_cache_key is None, (
        "offload/reload (.to()) must drop the cache so a stale GPU-resident "
        "table can never survive past the residency it was built for"
    )


def test_embeddings_connector_pe_cache_hit_skips_reconstruction(monkeypatch):
    from src.platform.runtime.native.arch.ltx import model as ltx_model
    from vendor.gpl.comfyui.ops import pick_operations

    conn = ltx_model.Embeddings1DConnector(
        inner=8, dim_head=4, num_layers=1, num_learnable_registers=4,
        operations=pick_operations(torch.float32, torch.float32),
    )
    calls = {"n": 0}
    original = ltx_model.rope.build_freqs_cis_chunked

    def counting(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(ltx_model.rope, "build_freqs_cis_chunked", counting)

    first = conn._freqs_cis(1024, torch.device("cpu"), torch.float32)
    assert calls["n"] == 1
    second = conn._freqs_cis(1024, torch.device("cpu"), torch.float32)
    assert calls["n"] == 1, "identical (seq_len, device, dtype) must hit the cache"
    assert second[0] is first[0] and second[1] is first[1]

    conn._freqs_cis(2048, torch.device("cpu"), torch.float32)  # different seq_len
    assert calls["n"] == 2

    conn.to(torch.float32)
    assert conn._pe_cache is None and conn._pe_cache_key is None


def test_connector_and_main_stream_caches_do_not_cross_contaminate():
    """video_embeddings_connector and audio_embeddings_connector are separate
    Embeddings1DConnector instances with separate register counts (and thus
    different self.inner) — populating one's cache must never affect the
    other's, and must never affect the parent LTXAVModel's own _pe_cache."""
    m = _build_tiny_ltxav_model()
    pixel_coords = _tiny_pixel_coords()
    m._prepare_positional_embeddings(pixel_coords, 25.0, torch.float32)
    model_cache_key = m._pe_cache_key

    v_conn = m.video_embeddings_connector
    a_conn = m.audio_embeddings_connector
    v_conn._freqs_cis(1024, torch.device("cpu"), torch.float32)
    assert v_conn._pe_cache is not None
    assert a_conn._pe_cache is None  # untouched by the video connector's build

    a_conn._freqs_cis(1024, torch.device("cpu"), torch.float32)
    assert a_conn._pe_cache is not None
    # The video connector's own cache and the model's are both still intact.
    assert v_conn._pe_cache is not None
    assert m._pe_cache_key == model_cache_key
