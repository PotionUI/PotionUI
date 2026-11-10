from __future__ import annotations

import math
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import torch

import src.platform.runtime.native.arch.minimax_h3.model as model_module
from src.pipelines.pipes.generator.video_minimax_h3 import main as h3_main
from src.pipelines.pipes.generator.video_minimax_h3.layout import build_packed_sequence, build_row_timesteps
from src.platform.runtime.native.arch.minimax_h3.packing import TEXT_TAG
from src.pipelines.pipes.generator.video_minimax_h3.main import H3_FFN_DIM, H3_HIDDEN_SIZE, GeneratorMinimaxH3Pipe
from src.platform.runtime.native.arch.minimax_h3.model import default_ffn_chunk_rows
from tests.pipelines.pipes.generator.video_minimax_h3.test_main import PATCH, _BIG_LAYOUT, _run_generate_one

_MAIN = "src.pipelines.pipes.generator.video_minimax_h3.main"


def _run(monkeypatch, config_overrides: dict) -> tuple[dict, list]:
    monkeypatch.setattr(model_module, "_FFN_CHUNK_MIN_ROWS", 1)
    seen: list = []
    real_call = h3_main._MiniMaxH3Forward.__call__

    def recording_call(self, *args, **kwargs):
        seen.append((kwargs.get("seq_chunk_rows"), kwargs.get("ffn_chunk_rows")))
        return real_call(self, *args, **kwargs)

    monkeypatch.setattr(h3_main._MiniMaxH3Forward, "__call__", recording_call)
    with patch(f"{_MAIN}.place_dit_for_sequence",
               side_effect=lambda *a, **k: MagicMock(seq_chunk_rows=k.get("seq_chunk_rows", 0))) as placement:
        _run_generate_one(config_overrides, steps=2, ctx_overrides=_BIG_LAYOUT)
    calls = [call.kwargs for call in placement.call_args_list]
    assert len(calls) == 1
    return calls[0], seen


def _default_rows(placement: dict) -> int:
    seq_len = placement["video_tokens"] + placement["audio_tokens"]
    return default_ffn_chunk_rows(seq_len, H3_HIDDEN_SIZE, H3_FFN_DIM)


def test_ffn_chunking_is_on_by_default():
    assert GeneratorMinimaxH3Pipe.get_default_config()["ffn_chunking"] is True


def test_the_default_rows_reach_both_the_placement_and_every_forward(monkeypatch):
    placement, seen = _run(monkeypatch, {})
    rows = _default_rows(placement)
    assert rows == math.ceil((placement["video_tokens"] + placement["audio_tokens"]) / 6)
    assert placement["ffn_chunk_rows"] == rows and placement["seq_chunk_rows"] == 0
    assert placement["seq_chunking"] is True
    assert seen == [(0, rows)] * 2


def test_turning_ffn_chunking_off_chunks_nothing_and_reserves_the_whole_ffn(monkeypatch):
    placement, seen = _run(monkeypatch, {"ffn_chunking": False})
    assert placement["ffn_chunk_rows"] == 0
    assert placement["ffn_dim"] == H3_FFN_DIM
    assert seen == [(0, 0)] * 2


@pytest.mark.parametrize("ffn_chunking", [True, False])
def test_a_configured_seq_chunk_width_reaches_the_placement_and_every_forward(monkeypatch, ffn_chunking):
    placement, seen = _run(monkeypatch, {"seq_chunk_rows": 50, "ffn_chunking": ffn_chunking})
    assert placement["seq_chunk_rows"] == 50
    assert placement["ffn_chunk_rows"] == (_default_rows(placement) if ffn_chunking else 0)
    assert all(seq_rows == 50 for seq_rows, _ in seen)


def test_a_seq_chunk_width_the_placement_picks_reaches_every_forward(monkeypatch):
    monkeypatch.setattr(model_module, "_FFN_CHUNK_MIN_ROWS", 1)
    seen: list = []
    real_call = h3_main._MiniMaxH3Forward.__call__

    def recording_call(self, *args, **kwargs):
        seen.append(kwargs.get("seq_chunk_rows"))
        return real_call(self, *args, **kwargs)

    monkeypatch.setattr(h3_main._MiniMaxH3Forward, "__call__", recording_call)
    with patch(f"{_MAIN}.place_dit_for_sequence", return_value=MagicMock(seq_chunk_rows=7)):
        _run_generate_one({}, steps=2, ctx_overrides=_BIG_LAYOUT)
    assert seen == [7, 7]


def test_a_wider_seq_chunk_width_leaves_the_default_ffn_chunk_in_the_reserve(monkeypatch):
    placement, _seen = _run(monkeypatch, {"seq_chunk_rows": 10_000})
    assert placement["ffn_chunk_rows"] == _default_rows(placement)
    assert placement["seq_chunk_rows"] == 10_000


def test_the_forward_wrapper_hands_its_ffn_chunk_rows_to_the_dit():
    seen: list = []

    def dit_module(*, hidden_states, audio_hidden_states, ffn_chunk_rows="ABSENT", **kwargs):
        seen.append(ffn_chunk_rows)
        return hidden_states * 0.1, audio_hidden_states * -0.2

    dit_module.prepare_text_context = lambda states, weight_revision=None: SimpleNamespace(text_embeds=states)
    layout = build_packed_sequence(
        torch.full((3,), TEXT_TAG, dtype=torch.long),
        num_latent_frames=2, latent_height=2, latent_width=2, num_audio_latents=2, patch_size=PATCH,
    )
    forward = h3_main._MiniMaxH3Forward(dit_module, layout, torch.zeros(1, 3, 8))
    unique_ts, ts_idx = build_row_timesteps(
        layout.video_indices, layout.audio_indices,
        num_condition_video_rows=layout.num_condition_video_rows, num_condition_audio_rows=0,
        num_text_tokens=3, video_timestep=0.5, audio_timestep=0.5,
        condition_video_timestep=0.999, condition_audio_timestep=1.0,
    )
    video_rows = torch.randn(layout.video_indices.numel(), 24 * PATCH[0] * PATCH[1] * PATCH[2])
    audio_rows = torch.randn(layout.audio_indices.numel(), 32)

    forward(video_rows, audio_rows, unique_ts, ts_idx, ffn_chunk_rows=5)
    forward(video_rows, audio_rows, unique_ts, ts_idx)

    assert seen == [5, None]
