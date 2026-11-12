import threading
import time

import pytest

from src.pipelines.outputs import ErrorGenerationOutput, ImageGenerationOutput
from tests.e2e.harness.stub_engine import (
    FAIL,
    HANG,
    OK,
    StubPipelineExecutor,
    parse_control,
    read_control,
    write_control,
)


def _run(executor, generation_id="g1"):
    outputs = []
    error = None
    try:
        executor.generate([{"type": "stub"}], outputs.append, generation_id)
    except Exception as exc:
        error = exc
    return outputs, error


@pytest.mark.parametrize(
    "text, expected",
    [
        (None, (OK, "")),
        ("", (OK, "")),
        ("ok", (OK, "")),
        ("fail CUDA out of memory", (FAIL, "CUDA out of memory")),
        ("FAIL   spaced   ", (FAIL, "spaced")),
        ("hang", (HANG, "")),
        ("nonsense", (OK, "")),
    ],
)
def test_the_control_file_text_says_what_the_engine_does(text, expected):
    assert parse_control(text) == expected


def test_a_missing_control_file_means_ok(tmp_path):
    assert read_control(tmp_path / "absent") == (OK, "")
    assert read_control(None) == (OK, "")


def test_ok_emits_one_saved_picture(tmp_path):
    control = tmp_path / "control"
    write_control(control, OK)

    outputs, error = _run(StubPipelineExecutor(control))

    assert error is None
    assert len(outputs) == 1
    assert isinstance(outputs[0], ImageGenerationOutput)
    assert outputs[0].temporary is False


def test_fail_emits_the_error_then_raises_it(tmp_path):
    control = tmp_path / "control"
    write_control(control, FAIL, "CUDA out of memory. Tried to allocate 2.00 GiB")

    outputs, error = _run(StubPipelineExecutor(control))

    assert isinstance(error, RuntimeError)
    assert [type(o) for o in outputs] == [ErrorGenerationOutput]
    assert outputs[0].error == "CUDA out of memory. Tried to allocate 2.00 GiB"


def test_the_control_file_is_read_on_every_generation(tmp_path):
    control = tmp_path / "control"
    executor = StubPipelineExecutor(control)
    write_control(control, FAIL, "boom")
    assert _run(executor)[1] is not None

    write_control(control, OK)
    outputs, error = _run(executor, "g2")

    assert error is None and len(outputs) == 1
    assert [call["mode"] for call in executor.calls] == [FAIL, OK]


def test_hang_waits_until_the_generation_is_cancelled(tmp_path):
    control = tmp_path / "control"
    write_control(control, HANG)
    executor = StubPipelineExecutor(control)
    done = threading.Event()
    threading.Thread(target=lambda: (_run(executor, "g1"), done.set()), daemon=True).start()
    deadline = time.monotonic() + 5
    while executor.running_generation_id != "g1" and time.monotonic() < deadline:
        time.sleep(0.01)

    assert executor.running_generation_id == "g1"
    assert not done.is_set()
    assert executor.cancel("g1") is True
    assert done.wait(5)
    assert executor.running_generation_id is None


def test_cancel_only_touches_the_run_in_flight():
    executor = StubPipelineExecutor()

    assert executor.cancel("someone-else") is False


def test_a_generation_cancelled_before_it_starts_produces_nothing():
    executor = StubPipelineExecutor()
    executor.cancel_on_start("g1")

    outputs, error = _run(executor, "g1")

    assert error is None and outputs == []
