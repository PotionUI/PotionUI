"""
App-level process-lifecycle operations.

Home of the in-place restart used by both POST /api/admin/restart (the admin
quick action) and the `restart-backend` automation action, so the two restart
the process exactly the same way.
"""

import asyncio
import os
import sys


def schedule_app_restart(delay: float = 0.5) -> None:
    """Replace the running process image in place after `delay` seconds.

    Scheduled on the running event loop (rather than executed inline) so the
    initiating HTTP response - or automation node result - is flushed before
    `os.execv` swaps the image. `execv` replaces the image rather than forking,
    so this works for `python api.py` and under a container's PID 1.

    Must be called from within a running event loop.
    """
    def _do_restart():
        os.execv(sys.executable, [sys.executable] + sys.argv)

    asyncio.get_running_loop().call_later(delay, _do_restart)
