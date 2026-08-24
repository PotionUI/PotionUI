"""What a user is told when installing a pipe's requirements does not work.

Driven through the real router over ASGI (not the controller directly), with a
real `PipeCatalog`/`PipeInstaller` behind it and only the subprocess boundary
stubbed - nothing here may reach a package index or a git remote. The stub is
what proves the argv, and its non-zero `returncode` is what proves a failed
install ends in ERROR *carrying the reason*, rather than in a silent success.

An httpx ASGI client rather than TestClient: the install runs as a background
task, and these tests have to await that task in the same event loop the
request created it on.
"""

import asyncio
import os
import shutil
import tempfile
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.features.pipes.manager import PipeInstallManager
from src.features.pipes.routes import build_router
from src.pipelines.catalog import PipeCatalog
from src.pipelines.contracts import (
    BasePipe,
    IOType,
    PipeConfigSpec,
    PipeInput,
    PipeInputSpec,
    PipeOutput,
    PipeOutputSpec,
    PipeStatus,
)
from src.pipelines.installer import PipeInstaller
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User

PIPE_NAME = "mock_pipe"
MISSING_PACKAGE = "potionui-nonexistent-package"
UNREACHABLE_REPO = "https://example.invalid/not-a-repo.git"


class MockPipe(BasePipe):
    """A pipe with no requirements at all."""

    name = PIPE_NAME
    description = "A mock pipe for testing"

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        return PipeOutput(output={"result": "processed"})

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {}

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [PipeInputSpec(name="input1", io_type=IOType.IMAGE)]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [PipeOutputSpec(name="result", io_type=IOType.IMAGE)]

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return []


class PipeNeedingMissingPackage(MockPipe):
    @classmethod
    def get_requirements(cls) -> Dict[str, Any]:
        return {"pip": [MISSING_PACKAGE], "git": [], "models": []}


class PipeNeedingUnreachableRepo(MockPipe):
    @classmethod
    def get_requirements(cls) -> Dict[str, Any]:
        return {"pip": [], "git": [{"url": UNREACHABLE_REPO, "path": "/tmp/never-created"}], "models": []}


class PipeBuiltFromSource(MockPipe):
    """The TRELLIS.2 shape: requirements no `pip install` can satisfy."""

    @classmethod
    def get_requirements(cls) -> Dict[str, Any]:
        return {"pip": ["cumesh"], "git": [], "models": []}

    @classmethod
    def manual_install_instructions(cls) -> str:
        return "cumesh is a CUDA extension. Build it from source:\n    . ./setup.sh --cumesh"


class RecordingAdminConnections:
    """Stands in for the admin WebSocket manager, keeping what it was sent."""

    def __init__(self):
        self.messages: List[dict] = []

    async def broadcast(self, message: dict) -> None:
        self.messages.append(message)


class Harness:
    def __init__(self, pipe_class, role: AccountType):
        self.temp_dir = tempfile.mkdtemp()
        core = os.path.join(self.temp_dir, "core")
        custom = os.path.join(self.temp_dir, "custom")
        os.makedirs(core)
        os.makedirs(custom)

        self.catalog = PipeCatalog(core, custom)
        self.catalog.pipes[PIPE_NAME] = pipe_class
        self.catalog.pipe_status[PIPE_NAME] = PipeStatus.NOT_INSTALLED

        self.installer = PipeInstaller(self.catalog)
        self.connections = RecordingAdminConnections()
        self.manager = PipeInstallManager(self.catalog, self.installer, self.connections)

        app = FastAPI()
        app.include_router(build_router(SimpleNamespace(pipe_install_manager=self.manager)))

        async def _user():
            return User(
                id="u1", username="u", email="u@example.com",
                password_hash="h", account_type=role,
            )

        app.dependency_overrides[get_current_active_user] = _user
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    async def finish_install(self) -> None:
        """Await the background install the POST kicked off."""
        task = self.manager._tasks.get(PIPE_NAME)
        assert task is not None, "no install task was started"
        await task

    def broadcasts(self, status: str) -> List[dict]:
        return [m for m in self.connections.messages if m.get("status") == status]

    async def aclose(self) -> None:
        await self.client.aclose()
        shutil.rmtree(self.temp_dir)


def failing_subprocess(stderr: bytes, returncode: int):
    """A subprocess stub that reports failure the way pip and git do."""
    process = Mock()
    process.communicate = AsyncMock(return_value=(b"", stderr))
    process.returncode = returncode
    return AsyncMock(return_value=process)


def succeeding_subprocess():
    process = Mock()
    process.communicate = AsyncMock(return_value=(b"", b""))
    process.returncode = 0
    return AsyncMock(return_value=process)


@pytest.fixture
async def harness(request):
    pipe_class, role = getattr(request, "param", (MockPipe, AccountType.ADMIN))
    h = Harness(pipe_class, role)
    try:
        yield h
    finally:
        await h.aclose()


@pytest.mark.parametrize(
    "harness", [(PipeNeedingMissingPackage, AccountType.ADMIN)], indirect=True
)
async def test_a_pip_package_that_does_not_exist_ends_in_error_with_the_reason(harness):
    stderr = f"ERROR: No matching distribution found for {MISSING_PACKAGE}".encode()

    with patch("asyncio.create_subprocess_exec", failing_subprocess(stderr, 1)) as spawn:
        response = await harness.client.post(f"/api/pipes/{PIPE_NAME}/install")
        assert response.status_code == 200
        assert response.json()["data"]["status"] == "installing"
        await harness.finish_install()

    argv = spawn.await_args.args
    assert argv[1:4] == ("-m", "pip", "install")
    assert MISSING_PACKAGE in argv

    assert harness.catalog.pipe_status[PIPE_NAME] == PipeStatus.ERROR

    state = await harness.client.get(f"/api/pipes/{PIPE_NAME}")
    assert state.json()["data"]["status"] == "error"
    assert MISSING_PACKAGE in state.json()["data"]["error"]

    errors = harness.broadcasts("error")
    assert len(errors) == 1
    assert errors[0]["pipe"] == PIPE_NAME
    assert "No matching distribution" in errors[0]["message"]


@pytest.mark.parametrize(
    "harness", [(PipeNeedingUnreachableRepo, AccountType.ADMIN)], indirect=True
)
async def test_a_git_url_that_cannot_be_reached_ends_in_error_with_the_reason(harness):
    stderr = f"fatal: repository '{UNREACHABLE_REPO}' not found".encode()

    with patch("asyncio.create_subprocess_exec", failing_subprocess(stderr, 128)) as spawn:
        response = await harness.client.post(f"/api/pipes/{PIPE_NAME}/install")
        assert response.status_code == 200
        await harness.finish_install()

    argv = spawn.await_args.args
    assert argv[:3] == ("git", "clone", UNREACHABLE_REPO)

    assert harness.catalog.pipe_status[PIPE_NAME] == PipeStatus.ERROR

    state = await harness.client.get(f"/api/pipes/{PIPE_NAME}")
    assert state.json()["data"]["status"] == "error"
    assert UNREACHABLE_REPO in state.json()["data"]["error"]
    assert "not found" in state.json()["data"]["error"]

    assert "not found" in harness.broadcasts("error")[0]["message"]


@pytest.mark.parametrize(
    "harness", [(PipeBuiltFromSource, AccountType.ADMIN)], indirect=True
)
async def test_a_pipe_built_from_source_is_refused_and_names_its_commands(harness):
    with patch("asyncio.create_subprocess_exec", succeeding_subprocess()) as spawn:
        response = await harness.client.post(f"/api/pipes/{PIPE_NAME}/install")

    assert response.status_code == 422
    assert "./setup.sh --cumesh" in response.json()["detail"]
    spawn.assert_not_awaited()

    # An install that was never attempted must not leave the pipe looking like
    # one that failed - or worse, stuck in INSTALLING.
    assert harness.catalog.pipe_status[PIPE_NAME] == PipeStatus.NOT_INSTALLED
    assert harness.connections.messages == []

    state = await harness.client.get(f"/api/pipes/{PIPE_NAME}")
    assert "./setup.sh --cumesh" in state.json()["data"]["manual_install"]


async def test_a_successful_install_reports_installed(harness):
    with patch("asyncio.create_subprocess_exec", succeeding_subprocess()):
        response = await harness.client.post(f"/api/pipes/{PIPE_NAME}/install")
        assert response.status_code == 200
        await harness.finish_install()

    assert harness.catalog.pipe_status[PIPE_NAME] == PipeStatus.INSTALLED
    assert [m["status"] for m in harness.connections.messages] == ["installing", "installed"]


async def test_a_second_install_of_a_running_pipe_conflicts(harness):
    harness.catalog.pipe_status[PIPE_NAME] = PipeStatus.INSTALLING

    with patch("asyncio.create_subprocess_exec", succeeding_subprocess()) as spawn:
        response = await harness.client.post(f"/api/pipes/{PIPE_NAME}/install")

    assert response.status_code == 409
    spawn.assert_not_awaited()


async def test_a_variant_key_with_a_slash_is_addressable(harness):
    """A variant's registry key is `<dir>/<variant>`. A plain path segment
    cannot carry that, and encoding it does not help - the server decodes
    `%2F` back to a slash before the router sees it.
    """
    harness.catalog.pipes["generator/trellis2"] = MockPipe
    harness.catalog.pipe_status["generator/trellis2"] = PipeStatus.NOT_INSTALLED

    response = await harness.client.get("/api/pipes/generator/trellis2")

    assert response.status_code == 200
    assert response.json()["data"]["name"] == "generator/trellis2"

    with patch("asyncio.create_subprocess_exec", succeeding_subprocess()):
        install = await harness.client.post("/api/pipes/generator/trellis2/install")
        assert install.status_code == 200
        await harness.manager._tasks["generator/trellis2"]

    assert harness.catalog.pipe_status["generator/trellis2"] == PipeStatus.INSTALLED


async def test_an_unknown_pipe_is_404(harness):
    response = await harness.client.post("/api/pipes/no-such-pipe/install")
    assert response.status_code == 404


@pytest.mark.parametrize("harness", [(MockPipe, AccountType.USER)], indirect=True)
async def test_a_regular_user_cannot_install_anything(harness):
    with patch("asyncio.create_subprocess_exec", succeeding_subprocess()) as spawn:
        install = await harness.client.post(f"/api/pipes/{PIPE_NAME}/install")
        state = await harness.client.get(f"/api/pipes/{PIPE_NAME}")

    assert install.status_code == 403
    assert state.status_code == 403
    spawn.assert_not_awaited()
    assert harness.catalog.pipe_status[PIPE_NAME] == PipeStatus.NOT_INSTALLED
