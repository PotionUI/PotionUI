"""
App-level process-lifecycle operations.

Home of the in-place restart used by both POST /api/admin/restart (the admin
quick action) and the `restart-backend` automation action, so the two restart
the process exactly the same way.
"""

import asyncio
import os
import sys


def restart_argv() -> list:
    """The exec argv that faithfully re-runs the current process.

    `[sys.executable] + sys.argv` is only correct for a script launch
    (`python api.py`). Under `python -m uvicorn ...` (how `./potionui start`
    runs the backend), runpy rewrites `sys.argv[0]` to the module's
    `__main__.py` FILE PATH -- re-exec'ing that path as a script puts
    `site-packages/uvicorn/` at `sys.path[0]`, so stdlib imports inside the
    interpreter's own bootstrap (`import logging` via asyncio) resolve to
    `uvicorn/logging.py` and the new process dies on a circular import before
    it can serve anything. `__main__.__spec__` is the documented signal for a
    `-m` launch (PEP 451: None for a script, the module's spec under runpy) --
    rebuild the `-m <module>` form from it instead.
    """
    spec = getattr(sys.modules.get("__main__"), "__spec__", None)
    name = getattr(spec, "name", None)
    if name:
        if name.endswith(".__main__"):
            name = name[: -len(".__main__")]
        return [sys.executable, "-m", name] + sys.argv[1:]
    return [sys.executable] + sys.argv


def _restart_process(argv: list) -> None:
    """Replace this process with `argv`, or the closest Windows equivalent.

    `execv` replaces the process image and never returns on POSIX. Windows has
    no such syscall: Python's `os.execv` there is emulated by spawning a child
    and *blocking this process* until the child exits, so the old process would
    still hold the listening socket while the new one tries to bind it -- a
    guaranteed port conflict. So on Windows, spawn a detached child instead and
    terminate this process immediately with `os._exit` (skipping normal
    interpreter shutdown -- the same abruptness `execv` itself has) so the
    socket is released before the child starts serving.
    """
    if sys.platform == "win32":
        import subprocess

        # These flags only exist in the subprocess module built for Windows;
        # getattr keeps this branch importable (though never taken) when
        # exercised from a non-Windows test process.
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
        subprocess.Popen(argv, close_fds=True, creationflags=creationflags)
        os._exit(0)
        return
    os.execv(argv[0], argv)


def schedule_app_restart(delay: float = 0.5) -> None:
    """Replace the running process image in place after `delay` seconds.

    Scheduled on the running event loop (rather than executed inline) so the
    initiating HTTP response - or automation node result - is flushed before
    the process image is swapped. This works for `python api.py`,
    `python -m uvicorn api:app` (the `./potionui start` flow -- see
    :func:`restart_argv`), and under a container's PID 1 -- see
    :func:`_restart_process` for the Windows exception.

    Must be called from within a running event loop.
    """
    def _do_restart():
        _restart_process(restart_argv())

    asyncio.get_running_loop().call_later(delay, _do_restart)
