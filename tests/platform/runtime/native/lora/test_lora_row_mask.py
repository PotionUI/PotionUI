from __future__ import annotations

import pytest
import torch
import torch.nn as nn

from src.platform.runtime.native.lora import (
    FULL,
    ROWS,
    SKIP,
    RowMaskTarget,
    apply_loras,
    apply_loras_with_report,
    lora_row_mask,
    lora_row_window,
    remove_loras,
    restore_lora_state,
    snapshot_lora_state,
)
from vendor.gpl.comfyui.ops import pick_operations

IN, OUT, RANK = 16, 24, 4
ROW_MASK = torch.tensor([True, True, False, True, False, False, True])
AUDIO = ~ROW_MASK


class _Tiny(nn.Module):
    def __init__(self, ops, targets=None):
        super().__init__()
        self.proj = ops.Linear(IN, OUT, bias=True)
        self.audio_head = ops.Linear(IN, OUT, bias=False)
        self.adaln = ops.Linear(IN, OUT, bias=False)
        self._targets = targets or {"proj": ROWS, "audio_head": SKIP}

    def lora_row_mask_target(self, stem):
        return self._targets.get(stem, FULL)


class _NoPolicy(nn.Module):
    def __init__(self, ops):
        super().__init__()
        self.proj = ops.Linear(IN, OUT, bias=True)


def _fp32_tiny(seed=0, targets=None):
    torch.manual_seed(seed)
    m = _Tiny(pick_operations(torch.float32, torch.float32), targets)
    for p in m.parameters():
        p.data = torch.randn_like(p) * 0.2
    return m.eval()


def _fp8_tiny(seed=0):
    ops = pick_operations(torch.float8_e4m3fn, torch.bfloat16)
    torch.manual_seed(seed)
    m = _Tiny(ops)
    for lin in (m.proj, m.audio_head, m.adaln):
        lin.weight.data = (torch.randn(OUT, IN) * 0.2).to(torch.float8_e4m3fn)
        lin.weight_scale = torch.tensor(0.5)
        if lin.bias is not None:
            lin.bias.data = (torch.randn(OUT) * 0.1).to(torch.bfloat16)
    return m.eval()


def _lora(stem, seed=1, out=OUT):
    g = torch.Generator().manual_seed(seed)
    return {
        f"diffusion_model.{stem}.lora_down.weight": torch.randn(RANK, IN, generator=g) * 0.3,
        f"diffusion_model.{stem}.lora_up.weight": torch.randn(out, RANK, generator=g) * 0.3,
    }


def _x(batch=1, dtype=torch.float32, seed=5):
    g = torch.Generator().manual_seed(seed)
    return torch.randn(batch, ROW_MASK.numel(), IN, generator=g).to(dtype)


def _run(m, x, layer="proj"):
    with torch.no_grad(), lora_row_mask(ROW_MASK):
        return getattr(m, layer)(x)


def test_masked_rows_match_base_exactly_and_kept_rows_match_the_merged_lora(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    x = _x(batch=2)
    base = _run(_fp32_tiny(), x)
    merged_model = _fp32_tiny()
    apply_loras(merged_model, [(_lora("proj"), 0.9)])
    merged = _run(merged_model, x)
    masked_model = _fp32_tiny()
    apply_loras(masked_model, [(_lora("proj"), 0.9)], row_masked=[True])
    masked = _run(masked_model, x)

    assert torch.equal(masked_model.proj.weight, _fp32_tiny().proj.weight)
    assert torch.equal(masked[:, AUDIO], base[:, AUDIO])
    assert torch.allclose(masked[:, ROW_MASK], merged[:, ROW_MASK], atol=1e-5)
    assert not torch.allclose(masked[:, ROW_MASK], base[:, ROW_MASK], atol=1e-3)


def test_a_masked_lora_outside_a_row_mask_refuses_to_run():
    m = _fp32_tiny()
    apply_loras(m, [(_lora("proj"), 1.0)], row_masked=[True])
    with torch.no_grad(), pytest.raises(RuntimeError, match="lora_row_mask"):
        m.proj(_x())


def test_a_row_mask_of_the_wrong_length_is_refused():
    m = _fp32_tiny()
    apply_loras(m, [(_lora("proj"), 1.0)], row_masked=[True])
    with torch.no_grad(), lora_row_mask(ROW_MASK[:3]), pytest.raises(RuntimeError, match="covers 3 rows"):
        m.proj(torch.randn(1, 7, IN))


def test_unmasked_flag_is_bit_identical_to_the_plain_apply(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    plain = _fp32_tiny()
    patched_plain, unmatched_plain = apply_loras(plain, [(_lora("proj"), 0.7)])
    flagged = _fp32_tiny()
    patched_flagged, unmatched_flagged = apply_loras(flagged, [(_lora("proj"), 0.7)], row_masked=[False])

    assert (patched_plain, unmatched_plain) == (patched_flagged, unmatched_flagged)
    for a, b in zip(plain.parameters(), flagged.parameters()):
        assert torch.equal(a, b)
    assert not flagged.proj._forward_hooks
    x = _x()
    with torch.no_grad():
        assert torch.equal(plain.proj(x), flagged.proj(x))


def test_unmasked_flag_needs_no_policy_but_a_masked_one_does():
    ops = pick_operations(torch.float32, torch.float32)
    apply_loras(_NoPolicy(ops), [(_lora("proj"), 1.0)], row_masked=[False])
    with pytest.raises(ValueError, match="cannot keep a LoRA out"):
        apply_loras(_NoPolicy(ops), [(_lora("proj"), 1.0)], row_masked=[True])


def test_skip_targets_are_left_untouched_and_reported(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    m = _fp32_tiny()
    before = m.audio_head.weight.clone()
    sd = {**_lora("proj"), **_lora("audio_head", seed=2)}
    _, _, reports = apply_loras_with_report(m, [(sd, 1.0)], row_masked=[True])

    assert torch.equal(m.audio_head.weight, before)
    assert not getattr(m.audio_head, "lora_masked_deltas", None)
    assert reports[0].masked_params == 1
    assert reports[0].matched_params == 1
    assert [(i.kind, i.count) for i in reports[0].ignored] == [("row_masked_out", 1)]


def test_keep_columns_bake_only_the_kept_output_columns(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    keep = torch.ones(OUT, dtype=torch.bool)
    keep[8:16] = False
    targets = {"adaln": RowMaskTarget("full", keep_columns=keep)}
    m = _fp32_tiny(targets=targets)
    before = m.adaln.weight.clone()
    apply_loras(m, [(_lora("adaln"), 1.0)], row_masked=[True])

    assert torch.equal(m.adaln.weight[~keep], before[~keep])
    assert not torch.allclose(m.adaln.weight[keep], before[keep])
    assert not m.adaln._forward_hooks


def test_masked_and_unmasked_loras_stack(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    x = _x()
    only_plain = _fp32_tiny()
    apply_loras(only_plain, [(_lora("proj", seed=3), 0.5)])
    both_plain = _fp32_tiny()
    apply_loras(both_plain, [(_lora("proj", seed=3), 0.5), (_lora("proj", seed=4), 1.2)])
    mixed = _fp32_tiny()
    apply_loras(mixed, [(_lora("proj", seed=4), 1.2), (_lora("proj", seed=3), 0.5)], row_masked=[True, False])

    out = _run(mixed, x)
    assert torch.allclose(out[:, AUDIO], _run(only_plain, x)[:, AUDIO], atol=1e-5)
    assert torch.allclose(out[:, ROW_MASK], _run(both_plain, x)[:, ROW_MASK], atol=1e-5)


def test_snapshot_restore_removes_the_masked_path_exactly(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    x = _x()
    m = _fp32_tiny()
    apply_loras(m, [(_lora("proj", seed=3), 0.5)])
    reference = _run(m, x)

    snapshot = snapshot_lora_state(m)
    apply_loras(m, [(_lora("proj", seed=4), 1.0), (_lora("proj", seed=6), 0.8)], row_masked=[True, False])
    assert m.proj.lora_masked_deltas
    assert not torch.allclose(_run(m, x), reference, atol=1e-4)
    restore_lora_state(snapshot)

    assert not hasattr(m.proj, "lora_masked_deltas")
    assert not m.proj._forward_hooks
    assert torch.allclose(_run(m, x), reference, atol=1e-5)


def test_restore_keeps_masked_deltas_applied_before_the_snapshot(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    x = _x()
    m = _fp32_tiny()
    apply_loras(m, [(_lora("proj", seed=3), 0.5)], row_masked=[True])
    reference = _run(m, x)
    snapshot = snapshot_lora_state(m)
    apply_loras(m, [(_lora("proj", seed=4), 1.0)], row_masked=[True])
    assert len(m.proj.lora_masked_deltas) == 2
    restore_lora_state(snapshot)

    assert len(m.proj.lora_masked_deltas) == 1
    assert len(m.proj._forward_hooks) == 1
    assert torch.equal(_run(m, x), reference)


def test_remove_loras_detaches_masked_paths():
    m = _fp32_tiny()
    apply_loras(m, [(_lora("proj"), 1.0)], row_masked=[True])
    remove_loras(m)
    assert not hasattr(m.proj, "lora_masked_deltas")
    assert not m.proj._forward_hooks


def test_masked_path_on_a_runtime_delta_layer(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    x = _x(dtype=torch.bfloat16)
    base = _run(_fp8_tiny(), x).float()
    merged_model = _fp8_tiny()
    apply_loras(merged_model, [(_lora("proj"), 0.9)])
    assert merged_model.proj.lora_deltas
    merged = _run(merged_model, x).float()
    masked_model = _fp8_tiny()
    apply_loras(masked_model, [(_lora("proj"), 0.9)], row_masked=[True])
    assert masked_model.proj.lora_deltas is None
    masked = _run(masked_model, x).float()

    assert torch.equal(masked[:, AUDIO], base[:, AUDIO])
    assert torch.allclose(masked[:, ROW_MASK], merged[:, ROW_MASK], atol=3e-2, rtol=3e-2)
    assert not torch.allclose(masked[:, ROW_MASK], base[:, ROW_MASK], atol=3e-2)


def test_a_prepared_chunk_loop_applies_the_windowed_mask(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    x = _x(dtype=torch.bfloat16)
    m = _fp8_tiny()
    apply_loras(m, [(_lora("proj"), 0.9)], row_masked=[True])
    whole = _run(m, x)

    chunks, start = [], 0
    with torch.no_grad(), lora_row_mask(ROW_MASK), m.proj.prepared_linear(x) as proj:
        assert proj is m.proj
        for chunk in x.split(3, dim=1):
            with lora_row_window(start, chunk.shape[1]):
                chunks.append(proj(chunk))
            start += chunk.shape[1]
    assert torch.equal(torch.cat(chunks, dim=1), whole)


def test_a_two_dimensional_input_uses_the_same_mask(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    x = _x()
    m = _fp32_tiny()
    apply_loras(m, [(_lora("proj"), 0.9)], row_masked=[True])
    assert torch.equal(_run(m, x[0]), _run(m, x)[0])
