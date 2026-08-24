"""Bringing a pipe's requirements into existence, and checking whether they are.

A pipe declares what it needs to run - pip packages, git checkouts, model files -
via `get_requirements()`. `requirements_satisfied` answers whether they are all
present (the catalog asks this of every pipe it finds, to give it a status);
`PipeInstaller` is what makes them present, and moves the pipe's status through
INSTALLING to INSTALLED or ERROR as it goes. Why an install failed is kept in
`errors`, because the status alone ("error") does not tell anyone what to do
next; the caller reads it back to report the pip/git output that caused it.

A pipe whose `manual_install_instructions()` returns text is refused rather
than attempted - its requirements are ones this vocabulary cannot install.

Uninstalling removes git checkouts only: models are left alone because another
pipe may well need the same weights.
"""

import asyncio
import importlib
import logging
import os
import shutil
import sys
from typing import TYPE_CHECKING, Any, Dict, Optional, Type

from src.pipelines.contracts import BasePipe, PipeStatus

if TYPE_CHECKING:
    from src.pipelines.catalog import PipeCatalog

logger = logging.getLogger(__name__)


class PipeNotAutoInstallableError(RuntimeError):
    """Raised when a pipe declares requirements the installer cannot satisfy.

    Carries the pipe's own instructions, so a caller can hand the user the
    exact commands instead of a generic failure.
    """

    def __init__(self, pipe_name: str, instructions: str):
        super().__init__(instructions)
        self.pipe_name = pipe_name
        self.instructions = instructions


def requirements_satisfied(pipe_class: Type[BasePipe]) -> bool:
    """Whether every requirement a pipe declares is already present."""
    try:
        requirements = pipe_class.get_requirements()

        # Check Python requirements
        if requirements.get('pip'):
            for req in requirements['pip']:
                try:
                    importlib.import_module(req.split('==')[0])
                except ImportError:
                    return False

        # Check git repositories
        if requirements.get('git'):
            for repo in requirements['git']:
                if not os.path.exists(repo['path']):
                    return False

        # Check models
        if requirements.get('models'):
            for model in requirements['models']:
                if not os.path.exists(model['path']):
                    return False

        return True
    except Exception:
        return False


class PipeInstaller:
    def __init__(self, catalog: "PipeCatalog"):
        self.catalog = catalog
        #: pipe name -> why its last install attempt failed.
        self.errors: Dict[str, str] = {}

    def last_error(self, pipe_name: str) -> Optional[str]:
        """Why `pipe_name`'s last install attempt failed, if one did."""
        return self.errors.get(pipe_name)

    async def install_pipe(self, pipe_name: str) -> bool:
        """Install a pipe and its requirements.

        Raises `PipeNotAutoInstallableError` if the pipe declares that its
        requirements are not installable this way - that is a caller error to
        report, not a failed install, so the pipe's status is left alone.
        """
        if pipe_name not in self.catalog.pipes:
            return False

        pipe_class = self.catalog.pipes[pipe_name]

        instructions = pipe_class.manual_install_instructions()
        if instructions:
            raise PipeNotAutoInstallableError(pipe_name, instructions)

        self.catalog.pipe_status[pipe_name] = PipeStatus.INSTALLING
        self.errors.pop(pipe_name, None)

        try:
            # Get requirements from pipe class
            requirements: Dict[str, Any] = pipe_class.get_requirements()

            # Install Python requirements
            if requirements.get('pip'):
                process = await asyncio.create_subprocess_exec(
                    sys.executable, '-m', 'pip', 'install', *requirements['pip'],
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                _, stderr = await process.communicate()
                if process.returncode != 0:
                    raise RuntimeError(
                        f"pip install failed (exit {process.returncode}): {stderr.decode(errors='replace').strip()}"
                    )

            # Install git repositories
            if requirements.get('git'):
                for repo in requirements['git']:
                    process = await asyncio.create_subprocess_exec(
                        'git', 'clone', repo['url'], repo['path'],
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    _, stderr = await process.communicate()
                    if process.returncode != 0:
                        raise RuntimeError(
                            f"git clone of {repo['url']} failed (exit {process.returncode}): "
                            f"{stderr.decode(errors='replace').strip()}"
                        )

            self.catalog.pipe_status[pipe_name] = PipeStatus.INSTALLED
            return True

        except Exception as e:
            logger.error(f"Error installing pipe {pipe_name}: {e}")
            self.errors[pipe_name] = str(e)
            self.catalog.pipe_status[pipe_name] = PipeStatus.ERROR
            return False

    async def uninstall_pipe(self, pipe_name: str) -> bool:
        """Uninstall a pipe"""
        if pipe_name not in self.catalog.pipes:
            return False

        try:
            pipe_class = self.catalog.pipes[pipe_name]
            requirements = pipe_class.get_requirements()

            # Remove git repositories
            if requirements.get('git'):
                for repo in requirements['git']:
                    if os.path.exists(repo['path']):
                        await asyncio.to_thread(self._remove_directory, repo['path'])

            # Models can be kept as they might be used by other pipes
            # Update status
            self.catalog.pipe_status[pipe_name] = PipeStatus.NOT_INSTALLED
            return True

        except Exception as e:
            logger.error(f"Error uninstalling pipe {pipe_name}: {e}")
            return False

    def _remove_directory(self, path: str):
        """Safely remove a directory"""
        if os.path.exists(path):
            shutil.rmtree(path)
