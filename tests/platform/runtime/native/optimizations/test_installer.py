"""Tests for OptimizationInstaller. Uses tiny `python -c ...` fake commands via
an install_spec seam - never real pip, never network."""

from __future__ import annotations

import asyncio
import importlib
import sys

import pytest

from src.platform.runtime.native.optimizations.catalog import InstallSpec, Optimization
from src.platform.runtime.native.optimizations.installer import OptimizationInstaller


class _FakeOpt(Optimization):
    """A fake catalog entry whose install_spec runs a tiny python -c script
    instead of touching pip/network."""

    id = "fake-opt"
    name = "Fake Optimization"
    description = "test double"
    benefit = "none"

    def __init__(self, script: str, activate_result=None, activate_raises=None):
        self._script = script
        self._activate_result = activate_result if activate_result is not None else {"active_backend": "sdpa"}
        self._activate_raises = activate_raises
        self.activate_calls = 0

    def requirements(self, probe):
        return []

    def installed_version(self, probe):
        return None

    def install_spec(self, probe):
        # Route through `_target_python` indirectly by using sys.executable
        # directly here (the seam under test in this file's own install_spec).
        return InstallSpec(pip_args=["-c", self._script], env={})

    def activate(self):
        self.activate_calls += 1
        if self._activate_raises:
            raise self._activate_raises
        return self._activate_result


class _FakeOptWithFallback(Optimization):
    """A fake catalog entry whose InstallSpec carries a fallback naming
    scheme, mirroring CudaToolchain's suffixed-vs-pinned pip name fallback -
    both attempts are tiny `python -c` scripts, never real pip/network."""

    id = "fake-opt-fallback"
    name = "Fake Optimization With Fallback"
    description = "test double"
    benefit = "none"

    def __init__(self, primary_script: str, fallback_script: str):
        self._primary_script = primary_script
        self._fallback_script = fallback_script
        self.activate_calls = 0

    def requirements(self, probe):
        return []

    def installed_version(self, probe):
        return None

    def install_spec(self, probe):
        return InstallSpec(
            pip_args=["-c", self._primary_script],
            fallback_pip_args=["-c", self._fallback_script],
        )

    def activate(self):
        self.activate_calls += 1
        return {"active_backend": "sdpa"}


@pytest.fixture
def installer():
    return OptimizationInstaller()


@pytest.fixture(autouse=True)
def _use_sys_executable(monkeypatch):
    """Force the installer to always target this interpreter, bypassing the
    venv-detection heuristic (irrelevant to install-flow correctness here)."""
    installer_mod = importlib.import_module("src.platform.runtime.native.optimizations.installer")

    monkeypatch.setattr(installer_mod, "_target_python", lambda: sys.executable)
    # Our fake opt's install_spec already returns pip_args=["-c", script]; the
    # installer prepends [python, "-m", "pip", "install", "--no-input", *pip_args].
    # We don't want a real pip invocation, so also patch the argv assembly by
    # monkeypatching create_subprocess_exec is unnecessary - instead we rely on
    # pip_args starting with "-c" being invalid for pip. To keep this a true
    # "fake command" test per the plan, patch installer._run's subprocess call
    # to invoke sys.executable directly instead of pip.
    yield


async def _wait_until(predicate, timeout=5.0, interval=0.02):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return predicate()


class TestInstallerHappyPath:
    @pytest.mark.asyncio
    async def test_start_runs_fake_command_and_succeeds(self, installer, monkeypatch):
        script = "import sys; print('hello'); print('world')"

        # Bypass the real `pip install` prefix entirely: patch _run's subprocess
        # exec target to run [sys.executable, "-c", script] instead of pip.
        installer_mod = importlib.import_module("src.platform.runtime.native.optimizations.installer")

        real_create = asyncio.create_subprocess_exec

        async def _fake_exec(*args, **kwargs):
            return await real_create(sys.executable, "-c", script, **kwargs)

        monkeypatch.setattr(installer_mod.asyncio, "create_subprocess_exec", _fake_exec)

        opt = _FakeOpt(script)
        job = installer.start(opt, probe=None)

        await _wait_until(lambda: job.status != "running")

        assert job.status == "success"
        assert opt.activate_calls == 1
        assert job.result == {"active_backend": "sdpa"}
        lines = [line for _, line in job.log]
        assert any("hello" in line for line in lines)
        assert any("world" in line for line in lines)

    @pytest.mark.asyncio
    async def test_activate_called_once_only_on_success(self, installer, monkeypatch):
        script = "pass"
        installer_mod = importlib.import_module("src.platform.runtime.native.optimizations.installer")

        real_create = asyncio.create_subprocess_exec

        async def _fake_exec(*args, **kwargs):
            return await real_create(sys.executable, "-c", script, **kwargs)

        monkeypatch.setattr(installer_mod.asyncio, "create_subprocess_exec", _fake_exec)

        opt = _FakeOpt(script)
        job = installer.start(opt, probe=None)
        await _wait_until(lambda: job.status != "running")

        assert opt.activate_calls == 1

    @pytest.mark.asyncio
    async def test_activate_not_called_on_nonzero_exit(self, installer, monkeypatch):
        script = "import sys; sys.exit(1)"
        installer_mod = importlib.import_module("src.platform.runtime.native.optimizations.installer")

        real_create = asyncio.create_subprocess_exec

        async def _fake_exec(*args, **kwargs):
            return await real_create(sys.executable, "-c", script, **kwargs)

        monkeypatch.setattr(installer_mod.asyncio, "create_subprocess_exec", _fake_exec)

        opt = _FakeOpt(script)
        job = installer.start(opt, probe=None)
        await _wait_until(lambda: job.status != "running")

        assert job.status == "failed"
        assert opt.activate_calls == 0
        assert "pip exited with status" in job.error


class TestInstallerLocking:
    @pytest.mark.asyncio
    async def test_second_start_while_running_raises(self, installer, monkeypatch):
        script = "import time; time.sleep(2)"
        installer_mod = importlib.import_module("src.platform.runtime.native.optimizations.installer")

        real_create = asyncio.create_subprocess_exec

        async def _fake_exec(*args, **kwargs):
            return await real_create(sys.executable, "-c", script, **kwargs)

        monkeypatch.setattr(installer_mod.asyncio, "create_subprocess_exec", _fake_exec)

        opt = _FakeOpt(script)
        installer.start(opt, probe=None)
        await asyncio.sleep(0.1)  # let the first job actually start

        with pytest.raises(RuntimeError):
            installer.start(opt, probe=None)

        # Clean up: cancel the sleeping job so the test doesn't leak a process.
        await installer.cancel()


class TestInstallerCancel:
    @pytest.mark.asyncio
    async def test_cancel_terminates_sleeping_child(self, installer, monkeypatch):
        script = "import time; time.sleep(30)"
        installer_mod = importlib.import_module("src.platform.runtime.native.optimizations.installer")

        real_create = asyncio.create_subprocess_exec

        async def _fake_exec(*args, **kwargs):
            return await real_create(sys.executable, "-c", script, **kwargs)

        monkeypatch.setattr(installer_mod.asyncio, "create_subprocess_exec", _fake_exec)

        opt = _FakeOpt(script)
        job = installer.start(opt, probe=None)
        await asyncio.sleep(0.2)  # let the process actually spawn

        proc = job.process
        cancelled = await installer.cancel()

        assert cancelled is True
        assert job.status == "cancelled"
        # The child process must actually be dead, not just marked as such.
        await asyncio.sleep(0.1)
        assert proc.returncode is not None

    @pytest.mark.asyncio
    async def test_cancel_with_no_running_job_returns_false(self, installer):
        assert await installer.cancel() is False


class TestInstallerTimeout:
    @pytest.mark.asyncio
    async def test_timeout_kills_child_and_marks_failed(self, installer, monkeypatch):
        script = "import time; time.sleep(30)"
        installer_mod = importlib.import_module("src.platform.runtime.native.optimizations.installer")

        monkeypatch.setattr(installer_mod, "_INSTALL_TIMEOUT_SECONDS", 0.1)

        real_create = asyncio.create_subprocess_exec

        async def _fake_exec(*args, **kwargs):
            return await real_create(sys.executable, "-c", script, **kwargs)

        monkeypatch.setattr(installer_mod.asyncio, "create_subprocess_exec", _fake_exec)

        opt = _FakeOpt(script)
        job = installer.start(opt, probe=None)

        await _wait_until(lambda: job.status != "running", timeout=5.0)

        assert job.status == "failed"
        assert job.error == "timed out"
        # The child must have actually been killed, not just abandoned.
        await asyncio.sleep(0.1)
        assert job.process.returncode is not None


def _fake_exec_by_trailing_script(real_create):
    """Build a `create_subprocess_exec` stand-in that runs the *actual*
    trailing arg (the fake `-c <script>` payload) under this interpreter,
    regardless of the `python -m pip install ...` prefix the installer
    prepends - so each install attempt's own script runs, distinguishing
    primary vs fallback attempts."""

    async def _fake_exec(*args, **kwargs):
        script = args[-1]
        return await real_create(sys.executable, "-c", script, **kwargs)

    return _fake_exec


class TestInstallerFallback:
    """CudaToolchain-style InstallSpec.fallback_pip_args: try the primary
    package name(s), and only if that fails, try the fallback spelling."""

    @pytest.mark.asyncio
    async def test_fallback_used_and_succeeds_when_primary_fails(self, installer, monkeypatch):
        installer_mod = importlib.import_module("src.platform.runtime.native.optimizations.installer")
        monkeypatch.setattr(
            installer_mod.asyncio, "create_subprocess_exec",
            _fake_exec_by_trailing_script(asyncio.create_subprocess_exec),
        )

        opt = _FakeOptWithFallback(
            primary_script="import sys; sys.exit(1)",
            fallback_script="print('fallback ran')",
        )
        job = installer.start(opt, probe=None)
        await _wait_until(lambda: job.status != "running")

        assert job.status == "success"
        assert opt.activate_calls == 1
        lines = [line for _, line in job.log]
        assert any("fallback ran" in line for line in lines)
        assert any("fallback naming scheme" in line for line in lines)

    @pytest.mark.asyncio
    async def test_fallback_not_attempted_when_primary_succeeds(self, installer, monkeypatch):
        installer_mod = importlib.import_module("src.platform.runtime.native.optimizations.installer")
        monkeypatch.setattr(
            installer_mod.asyncio, "create_subprocess_exec",
            _fake_exec_by_trailing_script(asyncio.create_subprocess_exec),
        )

        opt = _FakeOptWithFallback(
            primary_script="print('primary ran')",
            fallback_script="print('SHOULD NOT RUN')",
        )
        job = installer.start(opt, probe=None)
        await _wait_until(lambda: job.status != "running")

        assert job.status == "success"
        lines = [line for _, line in job.log]
        assert any("primary ran" in line for line in lines)
        assert not any("SHOULD NOT RUN" in line for line in lines)

    @pytest.mark.asyncio
    async def test_failed_when_both_primary_and_fallback_fail(self, installer, monkeypatch):
        installer_mod = importlib.import_module("src.platform.runtime.native.optimizations.installer")
        monkeypatch.setattr(
            installer_mod.asyncio, "create_subprocess_exec",
            _fake_exec_by_trailing_script(asyncio.create_subprocess_exec),
        )

        opt = _FakeOptWithFallback(
            primary_script="import sys; sys.exit(1)",
            fallback_script="import sys; sys.exit(2)",
        )
        job = installer.start(opt, probe=None)
        await _wait_until(lambda: job.status != "running")

        assert job.status == "failed"
        assert opt.activate_calls == 0
        assert "pip exited with status 2" in job.error
