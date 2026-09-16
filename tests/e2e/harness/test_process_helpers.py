"""Unit tests for e2e_harness's cross-platform process-group helpers
(popen_group_kwargs / kill_group), used by both this module and
tests/e2e/ui/run.py to spawn and tear down subprocess trees on POSIX and
Windows runners.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Same workaround as tests/scripts/test_onboarding_e2e.py: a dotted
# `tests.e2e.harness.*` import can resolve `tests` to the third-party
# `tests` package ultralytics ships in site-packages ahead of the repo root
# on PYTHONPATH. Import e2e_harness unqualified instead.
_HARNESS_DIR = Path(__file__).resolve().parent
if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))

import e2e_harness  # noqa: E402


class TestPopenGroupKwargs:
    def test_windows_returns_new_process_group_flag(self, monkeypatch):
        monkeypatch.setattr(e2e_harness, "is_windows", lambda: True)
        expected = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        assert e2e_harness.popen_group_kwargs() == {"creationflags": expected}

    def test_posix_returns_start_new_session(self, monkeypatch):
        monkeypatch.setattr(e2e_harness, "is_windows", lambda: False)
        assert e2e_harness.popen_group_kwargs() == {"start_new_session": True}


class TestKillGroup:
    def test_windows_sends_ctrl_break_event_without_force(self, monkeypatch):
        monkeypatch.setattr(e2e_harness, "is_windows", lambda: True)
        calls = []
        monkeypatch.setattr(e2e_harness.os, "kill", lambda pid, sig: calls.append((pid, sig)))
        run_calls = []
        monkeypatch.setattr(e2e_harness.subprocess, "run", lambda cmd, **kw: run_calls.append(cmd))

        e2e_harness.kill_group(MagicMock(pid=1234), force=False)

        expected_sig = getattr(e2e_harness.signal, "CTRL_BREAK_EVENT", 1)
        assert calls == [(1234, expected_sig)]
        assert run_calls == []

    def test_windows_swallows_oserror_from_ctrl_break_event(self, monkeypatch):
        monkeypatch.setattr(e2e_harness, "is_windows", lambda: True)

        def raise_oserror(pid, sig):
            raise OSError("no such process")

        monkeypatch.setattr(e2e_harness.os, "kill", raise_oserror)

        e2e_harness.kill_group(MagicMock(pid=1234), force=False)

    def test_windows_calls_taskkill_with_force(self, monkeypatch):
        monkeypatch.setattr(e2e_harness, "is_windows", lambda: True)
        calls = []
        monkeypatch.setattr(e2e_harness.subprocess, "run", lambda cmd, **kw: calls.append(cmd))

        e2e_harness.kill_group(MagicMock(pid=1234), force=True)

        assert calls == [["taskkill", "/PID", "1234", "/T", "/F"]]

    def test_posix_calls_killpg_with_sigterm(self, monkeypatch):
        monkeypatch.setattr(e2e_harness, "is_windows", lambda: False)
        calls = []
        monkeypatch.setattr(e2e_harness.os, "getpgid", lambda pid: pid, raising=False)
        monkeypatch.setattr(
            e2e_harness.os, "killpg", lambda pgid, sig: calls.append((pgid, sig)), raising=False
        )

        e2e_harness.kill_group(MagicMock(pid=5678), force=False)

        assert calls == [(5678, e2e_harness.signal.SIGTERM)]

    def test_posix_calls_killpg_with_sigkill_when_forced(self, monkeypatch):
        monkeypatch.setattr(e2e_harness, "is_windows", lambda: False)
        calls = []
        monkeypatch.setattr(e2e_harness.os, "getpgid", lambda pid: pid, raising=False)
        monkeypatch.setattr(
            e2e_harness.os, "killpg", lambda pgid, sig: calls.append((pgid, sig)), raising=False
        )
        monkeypatch.setattr(e2e_harness.signal, "SIGKILL", 9, raising=False)

        e2e_harness.kill_group(MagicMock(pid=5678), force=True)

        assert calls == [(5678, e2e_harness.signal.SIGKILL)]
