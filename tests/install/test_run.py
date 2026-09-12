"""Unit tests for the install-matrix harness's pure logic (tests/install/run.py).

No real subprocess, network, or checkout is exercised here — that only
happens when a maintainer runs `python tests/install/run.py` directly (see
its docstring and docs/testing-notes.md's "Install matrix" section).
"""
import json
import os

import pytest

from tests.install import run as install_run


# ---------------------------------------------------------------------------
# potionui_launcher
# ---------------------------------------------------------------------------

def test_potionui_launcher_posix_uses_relative_bash_shim(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "name", "posix")
    assert install_run.potionui_launcher(tmp_path) == ["./potionui"]


def test_potionui_launcher_windows_routes_through_cmd_with_absolute_path(monkeypatch, tmp_path):
    # subprocess with shell=False can't launch a .cmd directly (WinError
    # 193 — CreateProcess only runs PE binaries), and a bare relative name
    # would resolve against the *parent* process's cwd, not the `cwd=`
    # given to subprocess — both are why this isn't just ["potionui.cmd"].
    monkeypatch.setattr(os, "name", "nt")
    checkout_dir = tmp_path / "checkout-remote"
    assert install_run.potionui_launcher(checkout_dir) == [
        "cmd", "/c", str(checkout_dir / "potionui.cmd"),
    ]


# ---------------------------------------------------------------------------
# pid_alive on Windows — os.kill(pid, 0) is not a liveness probe there
# ---------------------------------------------------------------------------

def test_pid_alive_windows_dispatches_to_ctypes_probe_never_os_kill(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")

    def boom(*_a, **_k):
        raise AssertionError("os.kill must never be called on Windows")

    monkeypatch.setattr(os, "kill", boom)
    monkeypatch.setattr(install_run, "_pid_alive_windows", lambda pid: True)
    assert install_run.pid_alive(1234) is True


def test_pid_alive_posix_still_uses_os_kill(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    assert install_run.pid_alive(2**30) is False  # astronomically unlikely to exist


# ---------------------------------------------------------------------------
# make_copytree_ignore
# ---------------------------------------------------------------------------

def test_copytree_ignore_drops_root_only_names_at_root():
    ignore = install_run.make_copytree_ignore("/repo")
    names = ["src", "venv", "frontend", "node_modules", ".git", "__pycache__", "storage", ".runtime", "models", "outputs", "README.md"]
    ignored = ignore("/repo", names)
    assert ignored == {"venv", "node_modules", ".git", "__pycache__", "storage", ".runtime", "models", "outputs"}


def test_copytree_ignore_keeps_nested_models_package():
    """`src/features/models/` is a real feature package, not the top-level
    `./models` downloaded-checkpoint directory - it must never be excluded."""
    ignore = install_run.make_copytree_ignore("/repo")
    ignored = ignore("/repo/src/features", ["models", "model_library", "generation"])
    assert ignored == set()


def test_copytree_ignore_drops_node_modules_at_any_depth():
    ignore = install_run.make_copytree_ignore("/repo")
    assert ignore("/repo/frontend", ["src", "build", "node_modules", "package.json"]) == {"node_modules"}
    assert ignore("/repo/content/plugins/some-plugin/frontend", ["node_modules", "src"]) == {"node_modules"}


def test_copytree_ignore_drops_pycache_and_git_at_any_depth():
    ignore = install_run.make_copytree_ignore("/repo")
    assert ignore("/repo/src/features/models", ["__pycache__", "records.py"]) == {"__pycache__"}
    assert ignore("/repo", [".git", "src"]) == {".git"}


def test_copytree_ignore_keeps_frontend_build():
    ignore = install_run.make_copytree_ignore("/repo")
    ignored = ignore("/repo/frontend", ["src", "build", "node_modules", "package.json"])
    assert "build" not in ignored


def test_copytree_ignore_empty_when_nothing_excluded():
    ignore = install_run.make_copytree_ignore("/repo")
    assert ignore("/repo/src", ["a.py", "b.py"]) == set()


# ---------------------------------------------------------------------------
# pick_free_ports
# ---------------------------------------------------------------------------

def test_pick_free_ports_skips_busy_ports():
    busy = {8055, 8056, 8058}
    is_free = lambda p: p not in busy
    ports = install_run.pick_free_ports(2, start=8055, is_free=is_free)
    assert ports == [8057, 8059]


def test_pick_free_ports_returns_distinct_consecutive_when_all_free():
    ports = install_run.pick_free_ports(3, start=9000, is_free=lambda p: True)
    assert ports == [9000, 9001, 9002]


def test_pick_free_ports_zero_returns_empty():
    assert install_run.pick_free_ports(0, start=9000, is_free=lambda p: True) == []


# ---------------------------------------------------------------------------
# run_phases: the phase state machine
# ---------------------------------------------------------------------------

def test_run_phases_all_pass():
    calls = []
    phases = [
        install_run.Phase("a", lambda: calls.append("a")),
        install_run.Phase("b", lambda: calls.append("b")),
    ]
    result = install_run.run_phases(phases, clock=_fake_clock())
    assert result.passed is True
    assert result.phase_reached == "b"
    assert result.reason == "ok"
    assert calls == ["a", "b"]
    assert set(result.phase_seconds) == {"a", "b"}


def test_run_phases_stops_at_first_failure_and_skips_later_phases():
    calls = []

    def fail():
        calls.append("b")
        raise install_run.PhaseError("boom")

    phases = [
        install_run.Phase("a", lambda: calls.append("a")),
        install_run.Phase("b", fail),
        install_run.Phase("c", lambda: calls.append("c")),
    ]
    result = install_run.run_phases(phases, clock=_fake_clock())
    assert result.passed is False
    assert result.phase_reached == "b"
    assert result.reason == "boom"
    assert calls == ["a", "b"]  # "c" never ran


def test_run_phases_unexpected_exception_is_captured_not_raised():
    phases = [
        install_run.Phase("a", lambda: (_ for _ in ()).throw(ValueError("nope"))),
    ]
    result = install_run.run_phases(phases, clock=_fake_clock())
    assert result.passed is False
    assert result.phase_reached == "a"
    assert "nope" in result.reason


def test_run_phases_empty_list():
    result = install_run.run_phases([], clock=_fake_clock())
    assert result.passed is True
    assert result.phase_reached == ""


def _fake_clock():
    state = {"t": 0.0}

    def clock():
        state["t"] += 1.0
        return state["t"]

    return clock


# ---------------------------------------------------------------------------
# ProfileReport / table / JSON shape
# ---------------------------------------------------------------------------

def test_profile_report_to_row_shape():
    report = install_run.ProfileReport(
        profile="remote", phase_reached="stop", passed=True, reason="ok",
        seconds=12.345, checkout_dir="/tmp/x", log_dir="/tmp/x/logs",
    )
    row = report.to_row()
    assert row == {
        "profile": "remote",
        "phase_reached": "stop",
        "passed": True,
        "reason": "ok",
        "seconds": 12.3,
        "checkout_dir": "/tmp/x",
        "log_dir": "/tmp/x/logs",
    }
    json.dumps(row)  # must be JSON-serializable


def test_print_table_handles_pass_and_fail_rows(capsys):
    reports = [
        install_run.ProfileReport("remote", "stop", True, "ok", 5.0, "/a", "/a/logs"),
        install_run.ProfileReport("worker", "worker_doctor", False, "GPU required", 1.0, "/b", "/b/logs"),
    ]
    install_run.print_table(reports)
    out = capsys.readouterr().out
    assert "remote" in out and "PASS" in out
    assert "worker" in out and "FAIL" in out and "GPU required" in out


def test_print_table_handles_empty_list(capsys):
    install_run.print_table([])
    out = capsys.readouterr().out
    assert "PROFILE" in out  # header still prints


# ---------------------------------------------------------------------------
# parse_args guards
# ---------------------------------------------------------------------------

def test_parse_args_rejects_root_inside_repo():
    with pytest.raises(SystemExit):
        install_run.parse_args(["--root", str(install_run.REPO_ROOT / "scratch")])


def test_parse_args_rejects_root_equal_to_repo():
    with pytest.raises(SystemExit):
        install_run.parse_args(["--root", str(install_run.REPO_ROOT)])


def test_parse_args_accepts_outside_root(tmp_path):
    args = install_run.parse_args(["--root", str(tmp_path)])
    assert args.root == str(tmp_path)
    assert args.profiles == "remote"  # default


def test_parse_args_unknown_profile_is_not_validated_here(tmp_path):
    # profile validity is checked in main(), not parse_args - this only
    # documents that parse_args itself doesn't reject it.
    args = install_run.parse_args(["--root", str(tmp_path), "--profiles", "bogus"])
    assert args.profiles == "bogus"
