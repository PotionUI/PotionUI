import errno
import os

from src.platform.util import process


def test_self_is_alive():
    assert process.pid_alive(os.getpid()) is True


def test_nonexistent_pid_is_dead():
    assert process.pid_alive(2**30) is False


def test_windows_never_reaches_os_kill(monkeypatch):
    def boom(*_args):
        raise AssertionError("os.kill must never be called on Windows")

    monkeypatch.setattr(os, "kill", boom)
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(process, "_pid_alive_windows", lambda pid: pid == 4242)
    assert process.pid_alive(4242) is True
    assert process.pid_alive(4243) is False


def test_posix_permission_error_means_alive(monkeypatch):
    def denied(_pid, _sig):
        raise PermissionError(errno.EPERM, "denied")

    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(os, "kill", denied)
    assert process.pid_alive(1) is True
