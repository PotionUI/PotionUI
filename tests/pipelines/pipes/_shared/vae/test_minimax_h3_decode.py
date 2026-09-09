"""Tests for the MiniMax-H3 video-decode budget/marks helper.

CPU-only, tiny synthetic VAE (the same ``_TINY_CONFIG`` shape the VAE module's
own tests use) -- no real H3 weights are loaded anywhere here.
"""

from __future__ import annotations

import torch

from src.pipelines.pipes._shared.vae import minimax_h3_decode as h3
from src.platform.runtime.native.vae.minimax_h3_video import MiniMaxH3VideoVAE
from tests.platform.runtime.native.vae.test_minimax_h3_video import _TINY_CONFIG, _build

_LATENT_FRAMES = 8
_LATENT_H = 7   # 14 px at the tiny config's 2x spatial ratio -> 3 tile rows
_LATENT_W = 5   # 10 px -> 2 tile columns


class _Recorder:
    def __init__(self) -> None:
        self.marks: list[tuple[str, dict]] = []

    def mark(self, event: str, **fields) -> None:
        self.marks.append((event, fields))

    def events(self, name: str) -> list[dict]:
        return [fields for event, fields in self.marks if event == name]


def _vae() -> MiniMaxH3VideoVAE:
    torch.manual_seed(7)
    return _build(use_tiling=True, config=_TINY_CONFIG)


def _latent() -> torch.Tensor:
    torch.manual_seed(11)
    return torch.randn(1, 4, _LATENT_FRAMES, _LATENT_H, _LATENT_W)


class TestActivationEstimate:
    def test_bytes_per_token_is_the_width_sum(self):
        """The calibration: the estimate is the module's own widths added up,
        not a fitted constant, so it tracks any config change. Hand-derived
        here for the tiny config -- dim 16, FFN inner 32, 24-element output
        patch, 6-wide RoPE, fp32 parameters.
        """
        dim = inner = 16
        ffn_inner = 32
        patch = 3 * 2 * 2 * 2
        rope_width = 6
        expected = 4 * (4 * dim + 9 * inner + 4 * ffn_inner + 2 * patch) + 4 * (dim + 2 * inner + 2 * rope_width)
        assert h3.decode_bytes_per_tile_token(_vae()) == expected

    def test_tile_tokens_count_the_appended_tokens(self):
        module = _vae()
        # 4x4 latent tile (8px tile / 2x ratio), 5 latent frames per decoded
        # chunk (3 + 2 of look-ahead), plus 2 register tokens and the mask token.
        assert h3.decode_tile_tokens(module, _LATENT_H, _LATENT_W) == 5 * 4 * 4 + 3

    def test_untiled_tile_tokens_cover_the_whole_frame(self):
        module = _vae()
        module.use_tiling = False
        assert h3.decode_tile_tokens(module, _LATENT_H, _LATENT_W) == 5 * _LATENT_H * _LATENT_W + 3


class TestPlanTileBatch:
    def test_unknown_free_vram_stays_sequential(self):
        """A CPU decode, or a card whose mem_get_info failed: guessing a batch
        would trade a slow decode for an OOM."""
        plan = h3.plan_tile_batch(_vae(), _latent(), "cpu")
        assert plan["batch_size"] == 1
        assert plan["budget_gb"] is None
        assert plan["tiles_per_chunk"] == 6

    def test_batch_is_the_budget_divided_by_the_tile(self, monkeypatch):
        module = _vae()
        per_tile_gb = h3.decode_bytes_per_tile_token(module) * h3.decode_tile_tokens(module, _LATENT_H, _LATENT_W) / (1 << 30)
        free = 3.5 * per_tile_gb / h3.DECODE_VRAM_BUDGET_FRACTION
        monkeypatch.setattr(h3, "free_vram_gb", lambda device: free)

        plan = h3.plan_tile_batch(module, _latent(), "cuda:0")
        assert plan["batch_size"] == 3
        assert plan["estimated_gb"] == per_tile_gb * 3

    def test_batch_never_exceeds_the_tile_count(self, monkeypatch):
        monkeypatch.setattr(h3, "free_vram_gb", lambda device: 4096.0)
        plan = h3.plan_tile_batch(_vae(), _latent(), "cuda:0")
        assert plan["batch_size"] == plan["tiles_per_chunk"] == 6

    def test_a_budget_below_one_tile_stays_sequential(self, monkeypatch):
        monkeypatch.setattr(h3, "free_vram_gb", lambda device: 1e-9)
        assert h3.plan_tile_batch(_vae(), _latent(), "cuda:0")["batch_size"] == 1


class TestDecodeVideo:
    def test_marks_the_decode_and_every_chunk(self, monkeypatch):
        recorder = _Recorder()
        monkeypatch.setattr(h3, "get_profiler", lambda: recorder)
        module = _vae()

        pixels = h3.decode_video(module, _latent(), "cpu")

        (decode,) = recorder.events("minimax_h3.decode")
        assert decode["frames"] == pixels.shape[2]
        assert decode["latent_frames"] == _LATENT_FRAMES
        assert (decode["height"], decode["width"]) == (14, 10)
        assert decode["chunks"] == 2
        assert decode["tile_px"] == 8
        assert decode["tiles_per_chunk"] == 6
        assert decode["batch_size"] == 1
        assert decode["seconds"] > 0
        assert decode["peak_vram_gb"] is None

        chunks = recorder.events("minimax_h3.decode.chunk")
        assert [c["index"] for c in chunks] == [0, 1]
        assert all(c["tiles"] == 6 and c["seconds"] > 0 for c in chunks)

    def test_decode_matches_a_plain_module_decode(self, monkeypatch):
        monkeypatch.setattr(h3, "get_profiler", lambda: _Recorder())
        module = _vae()
        z = _latent()
        with torch.no_grad():
            expected = module.decode(z)
        torch.testing.assert_close(h3.decode_video(module, z, "cpu"), expected)

    def test_tile_px_override_is_restored(self, monkeypatch):
        """``ModelLifecycle`` caches the VAE across generations, so a leaked
        tile size would silently re-blend every later decode at a seam spacing
        that request never asked for."""
        recorder = _Recorder()
        monkeypatch.setattr(h3, "get_profiler", lambda: recorder)
        module = _vae()

        h3.decode_video(module, _latent(), "cpu", tile_px=0)

        (decode,) = recorder.events("minimax_h3.decode")
        assert decode["tile_px"] == 0
        assert decode["tiles_per_chunk"] == 1
        assert module.use_tiling is True
        assert module.tile_sample_min_height == 8
        assert module.decode_tile_batch_size == 1
        assert module.decode_chunk_observer is None

    def test_state_is_restored_when_the_decode_raises(self, monkeypatch):
        monkeypatch.setattr(h3, "get_profiler", lambda: _Recorder())
        module = _vae()
        module.decoder.forward = lambda x: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            h3.decode_video(module, _latent(), "cpu", tile_px=512)
        except RuntimeError:
            pass
        assert module.tile_sample_min_height == 8
        assert module.decode_chunk_observer is None

    def test_apply_tile_px_zero_disables_tiling(self):
        module = _vae()
        h3.apply_tile_px(module, 0)
        assert module.use_tiling is False
        h3.apply_tile_px(module, 512)
        assert module.use_tiling is True
        assert (module.tile_sample_min_height, module.tile_sample_min_width) == (512, 512)
