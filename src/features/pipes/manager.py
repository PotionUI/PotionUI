"""Installing a pipe's requirements on request, and reporting how it went.

`PipeInstaller` is the machinery; this is what a request reaches. An install
runs `pip install` and `git clone`, which take minutes rather than
milliseconds, so `start_install` returns as soon as the run is under way and
the outcome arrives on the admin WebSocket (`/ws/admin`) as
`pipe_install_status` messages - the channel the admin panel already uses for
long-running admin operations. pip and git emit no structured progress, so what
is broadcast is the phase change plus, on failure, the output that explains it;
`describe()` serves the same facts to a client that reconnected or reloaded.
"""

import asyncio
import logging
from typing import Any, Dict, Optional

from src.pipelines.contracts import PipeStatus
from src.pipelines.installer import PipeInstaller, PipeNotAutoInstallableError
from src.platform.websocket.admin_connection_manager import admin_connection_manager

from .exceptions import PipeInstallInProgressException, PipeNotFoundException

logger = logging.getLogger(__name__)

MESSAGE_TYPE = "pipe_install_status"


class PipeInstallManager:
    def __init__(self, pipe_catalog, installer: PipeInstaller, connection_manager=None):
        self.pipe_catalog = pipe_catalog
        self.installer = installer
        self.connection_manager = connection_manager or admin_connection_manager
        # Strong references to the running installs: a task held only by the
        # event loop can be garbage-collected mid-run.
        self._tasks: Dict[str, asyncio.Task] = {}

    def describe(self, pipe_name: str) -> Dict[str, Any]:
        """The install-relevant facts about one pipe.

        Raises:
            PipeNotFoundException: if no pipe is registered under that name.
        """
        pipe_class = self.pipe_catalog.get_pipe(pipe_name)
        if pipe_class is None:
            raise PipeNotFoundException(f"Pipe '{pipe_name}' not found")

        status = self.pipe_catalog.get_pipe_status(pipe_name)
        return {
            "name": pipe_name,
            "status": status.value,
            "requirements": pipe_class.get_requirements(),
            "manual_install": pipe_class.manual_install_instructions(),
            "error": self.installer.last_error(pipe_name) if status == PipeStatus.ERROR else None,
        }

    async def start_install(self, pipe_name: str) -> Dict[str, Any]:
        """Begin installing `pipe_name`'s requirements, and return immediately.

        Raises:
            PipeNotFoundException: if no pipe is registered under that name.
            PipeInstallInProgressException: if one is already running.
            PipeNotAutoInstallableError: if the pipe declares that its
                requirements cannot be installed this way (raised by the
                installer, and carrying the commands that can).
        """
        state = self.describe(pipe_name)

        if state["status"] == PipeStatus.INSTALLING.value:
            raise PipeInstallInProgressException(
                f"An install is already running for pipe '{pipe_name}'"
            )

        if state["manual_install"]:
            # Refused before the status moves, so a pipe that cannot be
            # installed never sits in INSTALLING waiting for a run that never
            # started.
            raise PipeNotAutoInstallableError(pipe_name, state["manual_install"])

        self.pipe_catalog.pipe_status[pipe_name] = PipeStatus.INSTALLING
        await self._broadcast(pipe_name, PipeStatus.INSTALLING.value, self._starting_message(state))

        task = asyncio.create_task(self._run_install(pipe_name))
        self._tasks[pipe_name] = task
        task.add_done_callback(lambda _: self._tasks.pop(pipe_name, None))

        return {**state, "status": PipeStatus.INSTALLING.value}

    async def _run_install(self, pipe_name: str) -> None:
        try:
            installed = await self.installer.install_pipe(pipe_name)
        except Exception as e:
            logger.exception("Install of pipe %s raised: %s", pipe_name, e)
            self.pipe_catalog.pipe_status[pipe_name] = PipeStatus.ERROR
            self.installer.errors[pipe_name] = str(e)
            installed = False

        status = self.pipe_catalog.get_pipe_status(pipe_name).value
        if installed:
            message = f"Pipe '{pipe_name}' installed."
        else:
            reason = self.installer.last_error(pipe_name) or "the install did not complete"
            message = f"Pipe '{pipe_name}' failed to install: {reason}"

        await self._broadcast(pipe_name, status, message)

    def _starting_message(self, state: Dict[str, Any]) -> str:
        requirements = state.get("requirements") or {}
        parts = []
        pip_packages = requirements.get("pip") or []
        if pip_packages:
            parts.append(f"pip install {' '.join(pip_packages)}")
        for repo in requirements.get("git") or []:
            parts.append(f"git clone {repo.get('url')}")
        detail = "; ".join(parts) if parts else "nothing to install"
        return f"Installing '{state['name']}': {detail}"

    async def _broadcast(self, pipe_name: str, status: str, message: Optional[str]) -> None:
        try:
            await self.connection_manager.broadcast({
                "type": MESSAGE_TYPE,
                "pipe": pipe_name,
                "status": status,
                "message": message,
            })
        except Exception as e:
            # A dead admin socket must not turn a finished install into a
            # failed one.
            logger.warning("Failed to broadcast %s for pipe %s: %s", MESSAGE_TYPE, pipe_name, e)
