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


def schedule_app_restart(delay: float = 0.5) -> None:
    """Replace the running process image in place after `delay` seconds.

    Scheduled on the running event loop (rather than executed inline) so the
    initiating HTTP response - or automation node result - is flushed before
    `os.execv` swaps the image. `execv` replaces the image rather than forking,
    so this works for `python api.py`, `python -m uvicorn api:app` (the
    `./potionui start` flow -- see :func:`restart_argv`), and under a
    container's PID 1.

    Must be called from within a running event loop.
    """
    def _do_restart():
        argv = restart_argv()
        os.execv(argv[0], argv)

    asyncio.get_running_loop().call_later(delay, _do_restart)
