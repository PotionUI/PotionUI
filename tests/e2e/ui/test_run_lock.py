from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

_RUN = Path(__file__).resolve().parent / "run.py"
_HARNESS_DIR = _RUN.parents[1] / "harness"
if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))

_spec = importlib.util.spec_from_file_location("e2e_ui_run_lock", _RUN)
run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run)


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _acquire(path, clock=None, timeout=60.0, messages=None, sleep=None):
    clock = clock or FakeClock()
    return run.acquire_run_lock(
        path,
        timeout_seconds=timeout,
        poll_seconds=5.0,
        clock=clock.clock,
        sleep=sleep or clock.sleep,
        say=(messages.append if messages is not None else (lambda _m: None)),
    )


def _dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_acquire_writes_this_process_into_the_lock_and_release_removes_it(tmp_path):
    lock = tmp_path / ".run.lock"

    record = _acquire(lock)

    on_disk = json.loads(lock.read_text(encoding="utf-8"))
    assert on_disk["pid"] == os.getpid()
    assert on_disk == record
    assert on_disk["started_at"].endswith("+00:00")
    run.release_run_lock(record, lock)
    assert not lock.exists()


def test_a_second_run_waits_for_a_live_holder_and_then_times_out(tmp_path):
    lock = tmp_path / ".run.lock"
    held = _acquire(lock)
    clock = FakeClock()
    messages = []

    with pytest.raises(run.RunLockTimeout) as raised:
        _acquire(lock, clock=clock, timeout=12.0, messages=messages)

    assert clock.sleeps == [5.0, 5.0, 2.0]
    assert f"pid {os.getpid()}" in str(raised.value)
    assert f"pid {os.getpid()}" in messages[0] and "Waiting" in messages[0]
    assert all("Still waiting" in m for m in messages[1:])
    assert json.loads(lock.read_text(encoding="utf-8")) == held


def test_a_waiting_run_takes_the_lock_once_the_holder_releases_it(tmp_path):
    lock = tmp_path / ".run.lock"
    held = _acquire(lock)
    clock = FakeClock()

    def sleep(seconds):
        clock.sleep(seconds)
        run.release_run_lock(held, lock)

    record = _acquire(lock, clock=clock, sleep=sleep)

    assert clock.sleeps == [5.0]
    assert json.loads(lock.read_text(encoding="utf-8")) == record


def test_a_lock_left_by_a_dead_process_is_taken_over(tmp_path):
    lock = tmp_path / ".run.lock"
    pid = _dead_pid()
    lock.write_text(json.dumps({"pid": pid, "started_at": "2026-10-01T08:00:00+00:00"}), encoding="utf-8")
    clock = FakeClock()
    messages = []

    record = _acquire(lock, clock=clock, messages=messages)

    assert clock.sleeps == []
    assert record["pid"] == os.getpid()
    assert any("stale" in m and f"pid {pid}" in m for m in messages)


def test_a_takeover_never_deletes_a_lock_a_faster_waiter_created_meanwhile(tmp_path):
    lock = tmp_path / ".run.lock"
    pid = _dead_pid()
    lock.write_text(json.dumps({"pid": pid, "started_at": "2026-10-01T08:00:00+00:00"}), encoding="utf-8")
    rival = {"pid": os.getpid(), "process_created": None, "started_at": "rival"}

    def say(message):
        if "stale" in message:
            lock.unlink()
            lock.write_text(json.dumps(rival), encoding="utf-8")

    clock = FakeClock()
    with pytest.raises(run.RunLockTimeout):
        run.acquire_run_lock(lock, timeout_seconds=5.0, poll_seconds=5.0, clock=clock.clock, sleep=clock.sleep, say=say)

    assert json.loads(lock.read_text(encoding="utf-8")) == rival


def test_a_takeover_guard_left_by_a_crashed_run_does_not_block_forever(tmp_path):
    lock = tmp_path / ".run.lock"
    guard = tmp_path / ".run.lock.takeover"
    lock.write_text(json.dumps({"pid": _dead_pid()}), encoding="utf-8")
    guard.write_text("", encoding="utf-8")
    old = time.time() - run.UNREADABLE_LOCK_GRACE_SECONDS - 5
    os.utime(guard, (old, old))

    assert _acquire(lock)["pid"] == os.getpid()
    assert not guard.exists()


def test_a_reused_pid_with_a_different_start_time_is_stale(tmp_path):
    lock = tmp_path / ".run.lock"
    lock.write_text(json.dumps({"pid": os.getpid(), "process_created": 1.0}), encoding="utf-8")

    record = _acquire(lock)

    assert record["process_created"] != 1.0


def test_a_fresh_unreadable_lock_is_waited_on_but_an_old_one_is_taken_over(tmp_path):
    lock = tmp_path / ".run.lock"
    lock.write_text("", encoding="utf-8")

    with pytest.raises(run.RunLockTimeout):
        _acquire(lock, timeout=5.0)

    old = time.time() - run.UNREADABLE_LOCK_GRACE_SECONDS - 5
    os.utime(lock, (old, old))
    assert _acquire(lock, timeout=5.0)["pid"] == os.getpid()


def test_release_never_removes_another_runs_lock(tmp_path):
    lock = tmp_path / ".run.lock"
    held = _acquire(lock)

    run.release_run_lock({**held, "started_at": "someone else"}, lock)

    assert lock.exists()


@pytest.mark.skipif(not hasattr(signal, "SIGTERM") or os.name == "nt", reason="needs a catchable SIGTERM")
def test_sigterm_inside_the_run_becomes_a_system_exit_and_the_handler_is_restored():
    before = signal.getsignal(signal.SIGTERM)

    with pytest.raises(SystemExit) as raised:
        with run.termination_signals_raise_system_exit():
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(5)

    assert raised.value.code == 128 + signal.SIGTERM
    assert signal.getsignal(signal.SIGTERM) == before


def _one_spec():
    return run.discover_specs()[0]


def test_main_gives_up_with_its_own_exit_code_when_the_lock_stays_held(tmp_path, monkeypatch, capsys):
    lock = tmp_path / ".run.lock"
    monkeypatch.setattr(run, "RUN_LOCK_PATH", lock)
    held = _acquire(lock)
    monkeypatch.setattr(run, "_run_locked", lambda *a, **k: pytest.fail("ran without the lock"))

    assert run.main([_one_spec(), "--lock-timeout", "0"]) == run.EXIT_LOCK_TIMEOUT

    assert "still held by pid" in capsys.readouterr().err
    assert json.loads(lock.read_text(encoding="utf-8")) == held


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit])
def test_main_releases_the_lock_when_the_run_is_interrupted(tmp_path, monkeypatch, error):
    lock = tmp_path / ".run.lock"
    monkeypatch.setattr(run, "RUN_LOCK_PATH", lock)
    seen = []

    def interrupted(*_args, **_kwargs):
        seen.append(json.loads(lock.read_text(encoding="utf-8"))["pid"])
        raise error()

    monkeypatch.setattr(run, "_run_locked", interrupted)

    with pytest.raises(error):
        run.main([_one_spec()])

    assert seen == [os.getpid()]
    assert not lock.exists()
