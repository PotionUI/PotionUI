"""Process liveness that is safe on every platform."""

from __future__ import annotations

import errno
import os


def _pid_alive_windows(pid: int) -> bool:
    import ctypes

    process_query_limited_information = 0x1000
    still_active = 259

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def pid_alive(pid: int) -> bool:
    """True when a process with this id exists (even one we may not signal).

    The POSIX signal-0 probe must never run on Windows: there signal 0 is
    CTRL_C_EVENT, so `os.kill(pid, 0)` delivers a Ctrl-C to the process
    group `pid` instead of probing it.
    """
    if os.name == "nt":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True
