import logging
import os
import sys
import threading
import time

import numpy as np
import pytest
import trimesh

from src.platform.runtime.offload import pool
from src.platform.runtime.offload.mesh_ops import clean_and_decimate_arrays, unwrap_uv_arrays
from src.platform.runtime.offload.pool import OffloadCancelled, ping, run_offloaded, shutdown_offload


@pytest.fixture(scope="module", autouse=True)
def _worker():
    shutdown_offload()
    yield
    shutdown_offload()


@pytest.fixture(scope="module")
def sphere():
    mesh = trimesh.creation.icosphere(subdivisions=4)
    return np.asarray(mesh.vertices, dtype=np.float32), np.asarray(mesh.faces, dtype=np.int64)


@pytest.fixture(scope="module")
def dense_sphere():
    mesh = trimesh.creation.icosphere(subdivisions=5)
    return np.asarray(mesh.vertices, dtype=np.float32), np.asarray(mesh.faces, dtype=np.int64)


def _ticks_while(call):
    outcome = {}

    def target():
        outcome["value"] = call()
        outcome["done"] = time.perf_counter()

    thread = threading.Thread(target=target)
    started = time.perf_counter()
    thread.start()
    ticks = 0
    longest_gap = 0.0
    previous = started
    while thread.is_alive():
        time.sleep(0.01)
        now = time.perf_counter()
        longest_gap = max(longest_gap, now - previous)
        previous = now
        ticks += 1
    thread.join()
    return ticks, longest_gap, outcome["done"] - started


def test_main_thread_keeps_ticking_while_xatlas_runs_offloaded(dense_sphere):
    vertices, faces = dense_sphere
    run_offloaded(ping)

    ticks, longest_gap, elapsed = _ticks_while(lambda: run_offloaded(unwrap_uv_arrays, vertices, faces))

    assert elapsed > 0.5
    assert longest_gap < min(0.25, elapsed / 2)
    assert ticks >= elapsed / 0.01 * 0.5


def test_probe_detects_the_inline_unwrap_holding_the_gil(dense_sphere):
    vertices, faces = dense_sphere

    ticks, longest_gap, elapsed = _ticks_while(lambda: unwrap_uv_arrays(vertices, faces))

    assert longest_gap > elapsed / 2
    assert ticks < elapsed / 0.01 * 0.25


def test_offloaded_unwrap_equals_the_inline_result(sphere):
    vertices, faces = sphere

    inline = unwrap_uv_arrays(vertices, faces)
    offloaded = run_offloaded(unwrap_uv_arrays, vertices, faces)

    assert len(inline) == len(offloaded) == 4
    for expected, actual in zip(inline, offloaded):
        assert expected.dtype == actual.dtype
        np.testing.assert_array_equal(expected, actual)


def test_offloaded_clean_and_decimate_equals_the_inline_result(sphere):
    vertices, faces = sphere

    inline = clean_and_decimate_arrays(vertices, faces, 600)
    offloaded = run_offloaded(clean_and_decimate_arrays, vertices, faces, 600)

    for expected, actual in zip(inline, offloaded):
        np.testing.assert_array_equal(expected, actual)


def test_worker_never_imports_torch_and_is_another_process():
    state = run_offloaded(ping)

    assert state["torch"] is False
    assert state["pid"] != os.getpid()


def test_worker_is_reused_between_calls():
    assert run_offloaded(ping)["pid"] == run_offloaded(ping)["pid"]


def test_spawning_leaves_the_real_main_module_in_place():
    shutdown_offload()
    main = sys.modules["__main__"]

    run_offloaded(ping)

    assert sys.modules["__main__"] is main


def test_exceptions_propagate_with_their_type_and_message():
    with pytest.raises(ValueError, match="invalid literal for int"):
        run_offloaded(int, "not a number")

    assert run_offloaded(int, "7") == 7


def test_cancellation_kills_the_worker_and_the_next_call_gets_a_fresh_one():
    before = run_offloaded(ping)["pid"]
    flag = threading.Event()
    threading.Timer(0.3, flag.set).start()

    started = time.perf_counter()
    with pytest.raises(OffloadCancelled):
        run_offloaded(time.sleep, 60, is_cancelled=flag.is_set)

    assert time.perf_counter() - started < 10
    assert run_offloaded(ping)["pid"] != before


def test_already_cancelled_call_never_reaches_the_worker():
    with pytest.raises(OffloadCancelled):
        run_offloaded(time.sleep, 60, is_cancelled=lambda: True)


def test_shutdown_terminates_the_worker_process():
    pid = run_offloaded(ping)["pid"]
    workers = [w for w in pool._pool._processes.values() if w.pid == pid]

    shutdown_offload()

    assert workers
    assert all(not w.is_alive() and w.exitcode is not None for w in workers)
    assert pool._pool is None
    assert run_offloaded(ping)["pid"] != pid


def test_shutdown_fails_a_running_call_instead_of_hanging():
    run_offloaded(ping)
    errors = []

    def target():
        try:
            run_offloaded(time.sleep, 60)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=target)
    thread.start()
    time.sleep(0.5)
    shutdown_offload()
    thread.join(timeout=15)

    assert not thread.is_alive()
    assert isinstance(errors[0], pool.OffloadError)


def test_spawn_failure_falls_back_inline_and_says_so(monkeypatch, caplog):
    shutdown_offload()

    def refuse():
        raise OSError("process spawn forbidden")

    monkeypatch.setattr(pool, "_spawn_pool", refuse)

    with caplog.at_level(logging.WARNING, logger=pool.logger.name):
        assert run_offloaded(int, "5") == 5

    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "cannot be spawned" in messages
    assert "inline" in messages
