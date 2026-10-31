from __future__ import annotations

import json

import pytest
import torch

from src.platform.runtime.native.arch.minimax_h3.config import MiniMaxH3Config
from src.platform.runtime.native.arch.minimax_h3.control import (
    CONTROL_V1_PLACES,
    CONTROL_V2_PLACES,
    HINT_ROWS_NON_AUDIO,
    HINT_ROWS_TRAINED,
    ControlContext,
    ControlPatchInfo,
    ControlStream,
    MiniMaxH3ControlBranch,
    attach_control_branch,
    control_attached,
    detect_control_patch,
)
from src.platform.runtime.native.arch.minimax_h3.model import MiniMaxH3Model
from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.errors import NativeEngineUnsupportedError

from .._native_ops import fp32_ops

COMMON = {
    "image_model": "minimax_h3", "hidden_size": 64, "num_layers": 3, "num_refiner_layers": 1,
    "num_attention_heads": 2, "attention_head_dim": 40, "ffn_dim": 48, "in_channels": 4,
    "audio_in_channels": 6, "patch_size": (1, 2, 2), "text_dim": 10, "rope_freq_dim": 3,
}
PRUNED = dict(COMMON, pruned=True, time_embed_dim=6, adaln_curve_grid=5)
FULL = dict(COMMON, pruned=False, time_embed_dim=12, freq_dim=8, time_embed_hidden_dim=16)
PLACES = (0, 2)
VIDEO_COLUMNS = 16
INPAINT_COLUMNS = 20
TEXT_N, COND_N, TARGET_N, AUDIO_N = 2, 2, 3, 2


def _info(places=PLACES, columns=VIDEO_COLUMNS, quantized=False) -> ControlPatchInfo:
    return ControlPatchInfo("v1", tuple(places), "pre_norm", 64, 6, 80, columns, quantized)


def _model(config=PRUNED) -> MiniMaxH3Model:
    torch.manual_seed(0)
    m = MiniMaxH3Model(MiniMaxH3Config.from_detect_config(config), fp32_ops())
    sd = {k: torch.ones_like(v) if ".norm" in k else torch.randn_like(v) * 0.05 for k, v in m.state_dict().items()}
    load_into_module(m, sd, match_model_spec(config))
    return m.eval()


def _patch_sd(columns=VIDEO_COLUMNS, places=PLACES) -> dict[str, torch.Tensor]:
    torch.manual_seed(1)
    branch = MiniMaxH3ControlBranch(
        MiniMaxH3Config.from_detect_config(PRUNED), tuple(places), columns, fp32_ops(),
    )
    return {k: torch.ones_like(v) if ".norm" in k else torch.randn_like(v) * 0.05 for k, v in branch.state_dict().items()}


def _attached(places=PLACES, columns=VIDEO_COLUMNS) -> MiniMaxH3Model:
    m = _model()
    attach_control_branch(m, _patch_sd(columns, places), _info(places, columns))
    return m


def _layout() -> dict:
    seq = TEXT_N + COND_N + TARGET_N + AUDIO_N
    text = torch.arange(TEXT_N)
    video = torch.arange(TEXT_N, TEXT_N + COND_N + TARGET_N)
    audio = torch.arange(TEXT_N + COND_N + TARGET_N, seq)
    tags = torch.zeros(seq, dtype=torch.long)
    tags[text] = 1
    tags[audio] = 2
    stamps = torch.zeros(seq, dtype=torch.long)
    stamps[audio] = 1
    return dict(
        text_indices=text, video_indices=video, audio_indices=audio, token_tags=tags,
        timestep_indices=stamps, position_ids=torch.rand(seq, 3, dtype=torch.float64),
    )


def _inputs(layout: dict) -> tuple:
    g = torch.Generator().manual_seed(3)
    return (
        torch.randn(1, layout["video_indices"].numel(), VIDEO_COLUMNS, generator=g),
        torch.randn(1, layout["audio_indices"].numel(), COMMON["audio_in_channels"], generator=g),
        torch.randn(1, layout["text_indices"].numel(), COMMON["text_dim"], generator=g),
        torch.tensor([0.2, 0.9]),
    )


def _run(m: MiniMaxH3Model, layout: dict, **extra):
    video, audio, text, t = _inputs(layout)
    return m(
        video, audio, text, t, layout["timestep_indices"], layout["token_tags"], layout["position_ids"],
        layout["video_indices"], layout["audio_indices"], layout["text_indices"],
        num_condition_video_rows=COND_N, **extra,
    )


def _control(scale=1.0, hint_rows=HINT_ROWS_TRAINED, columns=VIDEO_COLUMNS) -> ControlContext:
    g = torch.Generator().manual_seed(5)
    return ControlContext(torch.randn(1, TARGET_N, columns, generator=g), scale, hint_rows)


def test_scale_zero_and_absent_control_are_bit_identical_to_an_unattached_model():
    layout = _layout()
    plain = _run(_model(), layout)
    attached = _attached()
    for extra in ({}, {"control": None}, {"control": _control(0.0)}):
        out = _run(attached, layout, **extra)
        assert torch.equal(out[0], plain[0])
        assert torch.equal(out[1], plain[1])


def test_control_changes_the_output():
    layout = _layout()
    m = _attached()
    base = _run(m, layout)
    out = _run(m, layout, control=_control(1.0))
    assert not torch.allclose(out[0], base[0])
    half = _run(m, layout, control=_control(0.5))
    assert not torch.allclose(half[0], out[0])


def _stream(m, hint_rows):
    layout = _layout()
    packed = torch.randn(1, TEXT_N + COND_N + TARGET_N + AUDIO_N, 64, generator=torch.Generator().manual_seed(7))
    stream = ControlStream(
        m.control_branch, _control(1.0, hint_rows), packed, layout["video_indices"], layout["audio_indices"], COND_N,
    )
    temb = torch.randn(2, 6)
    adaln = layout["timestep_indices"] * 3 + layout["token_tags"]
    rotary = m._prepare_positional_embeddings(layout["position_ids"], torch.float32)
    return stream, temb, adaln, rotary


def _delta(m, hint_rows):
    stream, temb, adaln, rotary = _stream(m, hint_rows)
    stream.before(0, temb, adaln, rotary, 0, 0)
    zeros = torch.zeros_like(stream.packed)
    return stream.after(0, zeros)[0]


def test_hints_never_reach_audio_rows_and_trained_mode_skips_condition_rows():
    m = _attached()
    delta = _delta(m, HINT_ROWS_TRAINED)
    text = slice(0, TEXT_N)
    cond = slice(TEXT_N, TEXT_N + COND_N)
    target = slice(TEXT_N + COND_N, TEXT_N + COND_N + TARGET_N)
    audio = slice(TEXT_N + COND_N + TARGET_N, None)
    assert torch.count_nonzero(delta[audio]) == 0
    assert torch.count_nonzero(delta[cond]) == 0
    assert torch.count_nonzero(delta[text]) > 0
    assert torch.count_nonzero(delta[target]) > 0


def test_non_audio_mode_also_hints_condition_rows():
    m = _attached()
    delta = _delta(m, HINT_ROWS_NON_AUDIO)
    assert torch.count_nonzero(delta[TEXT_N:TEXT_N + COND_N]) > 0
    assert torch.count_nonzero(delta[TEXT_N + COND_N + TARGET_N:]) == 0


def test_interleaved_run_matches_running_every_control_block_first(monkeypatch):
    layout = _layout()
    m = _attached()
    ctx = _control(1.0)
    expected = _run(m, layout, control=ctx)
    original = MiniMaxH3ControlBranch.stream
    hints = {}

    def recording(self, *args):
        stream = original(self, *args)
        real_before = stream.before

        def before(layer, *rest):
            real_before(layer, *rest)
            if stream.hint is not None:
                hints[layer] = stream.hint

        stream.before = before
        return stream

    monkeypatch.setattr(MiniMaxH3ControlBranch, "stream", recording)
    _run(m, layout, control=ctx)
    assert set(hints) == set(PLACES)

    def precomputed(self, *args):
        stream = original(self, *args)
        stream.before = lambda *rest: None
        def after(layer, hidden):
            if layer not in hints:
                return hidden
            return hidden + (hints[layer] * stream.keep).to(hidden.dtype) * stream.scale

        stream.after = after
        return stream

    monkeypatch.setattr(MiniMaxH3ControlBranch, "stream", precomputed)
    got = _run(m, layout, control=ctx)
    torch.testing.assert_close(got[0], expected[0])
    torch.testing.assert_close(got[1], expected[1])


class _SkipAfterFirstBlock:
    def should_skip(self, probe) -> bool:
        return True

    def record_skip(self):
        return "skipped"

    def record_compute(self, probe, result) -> None:
        raise AssertionError("a skipped step must not record a compute")


def test_a_step_cache_skip_stops_before_the_remaining_control_blocks():
    layout = _layout()
    m = _attached(places=(0, 1, 2))
    calls = []
    for n, block in enumerate(m.control_branch.control_blocks):
        block.register_forward_hook(lambda module, args, out, n=n: calls.append(n))
    result = _run(m, layout, control=_control(1.0), step_cache=_SkipAfterFirstBlock())
    assert result == "skipped"
    assert calls == [0]


def test_control_row_count_must_match_target_rows():
    m = _attached()
    bad = ControlContext(torch.zeros(1, TARGET_N + 1, VIDEO_COLUMNS), 1.0)
    with pytest.raises(ValueError, match="one control row per target row"):
        _run(m, _layout(), control=bad)


def test_control_column_width_must_match_the_branch():
    m = _attached()
    with pytest.raises(ValueError, match="columns wide"):
        _run(m, _layout(), control=_control(1.0, columns=INPAINT_COLUMNS))


def test_control_without_a_branch_is_refused():
    with pytest.raises(ValueError, match="no control branch"):
        _run(_model(), _layout(), control=_control(1.0))


def test_a_wider_inpaint_branch_attaches_and_runs():
    layout = _layout()
    m = _attached(columns=VIDEO_COLUMNS + INPAINT_COLUMNS)
    out = _run(m, layout, control=_control(1.0, columns=VIDEO_COLUMNS + INPAINT_COLUMNS))
    assert torch.isfinite(out[0]).all()


def test_attach_twice_is_refused():
    m = _attached()
    assert control_attached(m)
    with pytest.raises(ValueError, match="already carries"):
        attach_control_branch(m, _patch_sd(), _info())


def test_attach_rejects_a_missing_and_an_unexpected_key():
    sd = _patch_sd()
    gone = dict(sd)
    gone.pop("control_blocks.1.after_proj.bias")
    with pytest.raises(ValueError, match="missing"):
        attach_control_branch(_model(), gone, _info())
    extra = dict(sd, **{"control_blocks.1.surplus": torch.zeros(1)})
    with pytest.raises(ValueError, match="unexpected"):
        attach_control_branch(_model(), extra, _info())


def test_attach_rejects_a_patch_that_does_not_fit_the_dit():
    with pytest.raises(NativeEngineUnsupportedError, match="full one"):
        attach_control_branch(_model(FULL), _patch_sd(), _info())
    with pytest.raises(NativeEngineUnsupportedError, match="hidden size"):
        attach_control_branch(_model(), _patch_sd(), ControlPatchInfo("v1", PLACES, "pre_norm", 32, 6, 80, 16, False))
    with pytest.raises(NativeEngineUnsupportedError, match="outside"):
        attach_control_branch(_model(), _patch_sd(), _info(places=(0, 7)))
    with pytest.raises(NativeEngineUnsupportedError, match="columns"):
        attach_control_branch(_model(), _patch_sd(), _info(columns=17))


def _fake_patch(blocks: int, hidden=8, inner=6, time_dim=4, columns=14, scaled=False, unfused=False) -> dict:
    sd = {
        "control_proj_in.weight": torch.zeros(hidden, columns),
        "control_proj_in.bias": torch.zeros(hidden),
    }
    for i in range(blocks):
        p = f"control_blocks.{i}."
        sd[p + "after_proj.weight"] = torch.zeros(hidden, hidden)
        sd[p + "attn.qkv_proj.weight"] = torch.zeros(3 * inner, hidden)
        sd[p + "adaln_proj.linear.weight"] = torch.zeros(10, time_dim)
        if scaled:
            sd[p + "attn.qkv_proj.weight_scale"] = torch.zeros(3 * inner, 1)
    sd["control_blocks.0.before_proj.weight"] = torch.zeros(hidden, hidden)
    if unfused:
        sd["control_blocks.0.attn.to_q.weight"] = torch.zeros(inner, hidden)
    return sd


def _meta(places, mode) -> dict[str, str]:
    return {"control_blocks_places": json.dumps(list(places)), "inpaint_masked_pixel_mode": mode}


def test_detect_returns_none_for_a_state_dict_without_control_keys():
    assert detect_control_patch({"blocks.0.norm1.weight": torch.zeros(2)}) is None


def test_detect_reads_a_2_0_patch_from_metadata():
    info = detect_control_patch(_fake_patch(10), _meta(CONTROL_V2_PLACES, "post_norm"))
    assert info.kind == "2.0" and info.places == CONTROL_V2_PLACES
    assert info.masked_pixel_mode == "post_norm" and info.num_blocks == 10
    assert (info.hidden_size, info.inner_dim, info.time_embed_dim, info.control_in_columns) == (8, 6, 4, 14)
    assert not info.quantized


def test_detect_falls_back_to_the_v1_layout_without_metadata():
    info = detect_control_patch(_fake_patch(5))
    assert info.kind == "v1" and info.places == CONTROL_V1_PLACES and info.masked_pixel_mode == "pre_norm"


def test_detect_flags_quantised_patches():
    assert detect_control_patch(_fake_patch(5, scaled=True)).quantized


def test_a_v1_config_on_a_2_0_state_dict_is_rejected():
    with pytest.raises(NativeEngineUnsupportedError, match="mismatched"):
        detect_control_patch(_fake_patch(10), _meta(CONTROL_V1_PLACES, "post_norm"))


def test_a_2_0_config_on_a_v1_state_dict_is_rejected():
    with pytest.raises(NativeEngineUnsupportedError, match="mismatched"):
        detect_control_patch(_fake_patch(5), _meta(CONTROL_V2_PLACES, "post_norm"))


def test_masked_pixel_mode_must_match_the_block_count():
    with pytest.raises(NativeEngineUnsupportedError, match="inpaint_masked_pixel_mode"):
        detect_control_patch(_fake_patch(10), _meta(CONTROL_V2_PLACES, "pre_norm"))
    with pytest.raises(NativeEngineUnsupportedError, match="inpaint_masked_pixel_mode"):
        detect_control_patch(_fake_patch(5), _meta(CONTROL_V1_PLACES, "post_norm"))


@pytest.mark.parametrize("places", [(5, 10, 15, 20, 25), (0, 10, 10, 30, 40), (0, 10, 20, 30, 41)])
def test_odd_places_are_rejected(places):
    with pytest.raises(NativeEngineUnsupportedError):
        detect_control_patch(_fake_patch(5), _meta(places, "pre_norm"))


def test_an_unsupported_block_count_is_rejected():
    with pytest.raises(NativeEngineUnsupportedError, match="expected 5"):
        detect_control_patch(_fake_patch(7))


def test_the_original_full_adaln_layout_points_at_the_pruned_patch():
    with pytest.raises(NativeEngineUnsupportedError, match="Comfy-Org/MiniMax-H3"):
        detect_control_patch(_fake_patch(10, unfused=True))


def test_a_state_dict_missing_the_core_keys_is_rejected():
    sd = _fake_patch(5)
    sd.pop("control_blocks.0.before_proj.weight")
    with pytest.raises(NativeEngineUnsupportedError, match="before_proj"):
        detect_control_patch(sd)


def _int8_patch_sd() -> dict[str, torch.Tensor]:
    sd = _patch_sd()
    blob = torch.tensor(list(json.dumps({"format": "int8_tensorwise"}).encode()), dtype=torch.uint8)
    out = {}
    for key, value in sd.items():
        if key.endswith(("attn.qkv_proj.weight", "attn.out_proj.weight", "mlp.fc1.weight", "mlp.fc2.weight")):
            scale = value.abs().amax(dim=1, keepdim=True).clamp(min=1e-6) / 127.0
            out[key] = torch.round(value / scale).clamp(-127, 127).to(torch.int8)
            out[key[:-len("weight")] + "weight_scale"] = scale.float()
            out[key[:-len("weight")] + "comfy_quant"] = blob.clone()
        else:
            out[key] = value
    return out


def test_an_int8_patch_attaches_under_the_quantised_ops_and_runs():
    m = _model()
    attach_control_branch(m, _int8_patch_sd(), _info(quantized=True))
    block = m.control_branch.control_blocks[0]
    assert block.attn.qkv_proj.weight is None or block.attn.qkv_proj.weight.dtype == torch.int8
    out = _run(m, _layout(), control=_control(1.0))
    assert torch.isfinite(out[0]).all()
