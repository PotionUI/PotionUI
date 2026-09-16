import types
from unittest.mock import patch

import pytest

from src.pipelines.pipes.detailer.native.loras import (
    DROP,
    KEEP,
    SELECT,
    baked_loras,
    face_lora_scope,
    face_loras,
)


class _FakeDit:
    def __init__(self):
        self.module = object()
        self.switches = []


def _lora(path, weight=1.0, **extra):
    return {"file_path": path, "weight": weight, **extra}


def _recording_sync(dit):
    def _sync(dit_model, loras, lora_fp, apply, **kwargs):
        dit.switches.append([lora["file_path"] for lora in loras])

    return _sync


def _run_scope(mode, selected, run, sync=None):
    dit = _FakeDit()
    sync = sync or _recording_sync(dit)
    with patch("src.pipelines.pipes.detailer.native.loras.sync_loras", side_effect=sync):
        with face_lora_scope(dit, mode, selected, run) as active:
            inside = list(dit.switches)
    return dit, inside, active


def test_keep_never_touches_the_stack():
    dit, inside, active = _run_scope(KEEP, [], [_lora("run.safetensors")])

    assert dit.switches == []
    assert inside == []
    assert active == []


def test_drop_clears_before_the_faces_and_restores_after():
    run = [_lora("run.safetensors", 0.8)]
    dit, inside, active = _run_scope(DROP, [], run)

    assert inside == [[]]
    assert dit.switches == [[], ["run.safetensors"]]
    assert active == []


def test_select_applies_the_face_stack_and_restores_the_run_stack():
    run = [_lora("run.safetensors", 0.8)]
    selected = [_lora("skin.safetensors", 0.5)]
    dit, inside, active = _run_scope(SELECT, selected, run)

    assert inside == [["skin.safetensors"]]
    assert dit.switches == [["skin.safetensors"], ["run.safetensors"]]
    assert active == selected


def test_a_failing_switch_restores_the_run_stack_and_raises():
    dit = _FakeDit()
    calls = []

    def _sync(dit_model, loras, lora_fp, apply, **kwargs):
        calls.append([lora["file_path"] for lora in loras])
        if len(calls) == 1:
            raise RuntimeError("apply blew up")

    with patch("src.pipelines.pipes.detailer.native.loras.sync_loras", side_effect=_sync):
        with pytest.raises(RuntimeError, match="could not switch LoRAs"):
            with face_lora_scope(dit, DROP, [], [_lora("run.safetensors")]):
                pass

    assert calls == [[]]


def test_a_failing_restore_raises_after_the_faces_are_done():
    dit = _FakeDit()
    calls = []

    def _sync(dit_model, loras, lora_fp, apply, **kwargs):
        calls.append([lora["file_path"] for lora in loras])
        if len(calls) == 2:
            raise RuntimeError("restore blew up")

    with patch("src.pipelines.pipes.detailer.native.loras.sync_loras", side_effect=_sync):
        with pytest.raises(RuntimeError, match="could not restore the run's LoRAs"):
            with face_lora_scope(dit, SELECT, [_lora("skin.safetensors")], [_lora("run.safetensors")]):
                pass

    assert calls == [["skin.safetensors"], ["run.safetensors"]]


def test_an_absent_dit_is_a_no_op():
    with face_lora_scope(None, DROP, [], [_lora("run.safetensors")]) as active:
        assert active == []


def test_face_loras_drops_blank_and_zero_weight_entries():
    entries = [_lora("a.safetensors", 0.0), _lora("", 1.0), _lora("b.safetensors", 0.7)]
    assert [lora["file_path"] for lora in face_loras(entries)] == ["b.safetensors"]


def test_baked_loras_excludes_step_windowed_entries():
    entries = [
        _lora("plain.safetensors", 0.8),
        _lora("windowed.safetensors", 1.0, step_start=1, step_end=2),
    ]
    assert [lora["file_path"] for lora in baked_loras(entries)] == ["plain.safetensors"]


def test_a_windowed_face_lora_is_refused():
    with pytest.raises(Exception):
        face_loras([_lora("windowed.safetensors", 1.0, step_start=1, step_end=2)])
