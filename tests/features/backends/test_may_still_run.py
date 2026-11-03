from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import Mock

from src.features.backends.backend_config import NativeRemoteBackendConfig
from src.features.backends.base_backend import BaseBackend
from src.features.backends.in_process_backend import InProcessBackend
from src.features.backends.native_remote_backend import RemoteNativeBackend
from src.features.cloud.backend import CloudBackend


class _Plain(BaseBackend):
    async def start_generation(self, pipeline_data, emit):
        return pipeline_data["generation_id"]

    async def cancel_generation(self, generation_id):
        return False

    async def health_check(self) -> Dict[str, Any]:
        return {}

    async def get_system_info(self) -> Dict[str, Any]:
        return {}


class _InProcess(InProcessBackend):
    async def health_check(self) -> Dict[str, Any]:
        return {}

    async def get_system_info(self) -> Dict[str, Any]:
        return {}


def _config():
    return SimpleNamespace(id="b1", name="B", engine="native")


def test_a_backend_that_cannot_tell_assumes_the_job_may_still_run():
    assert _Plain(_config()).may_still_run("gen_1") is True


def test_an_in_process_backend_runs_what_it_tracks_or_its_executor_executes():
    engine = Mock(running_generation_id=None)
    backend = _InProcess(_config(), engine)

    assert backend.may_still_run("gen_1") is False

    backend._active.add("gen_1")
    assert backend.may_still_run("gen_1") is True

    backend._active.discard("gen_1")
    engine.running_generation_id = "gen_1"
    assert backend.may_still_run("gen_1") is True
    assert backend.may_still_run("gen_2") is False


def _remote(row):
    backend = RemoteNativeBackend(NativeRemoteBackendConfig(
        id="remote-1", name="Worker", base_url="http://fake-worker", worker_token="tok", enabled=True,
    ))
    backend._repository = Mock(get_by_id=Mock(return_value=row))
    return backend


def test_a_remote_backend_runs_a_job_whose_journal_row_is_not_terminal():
    assert _remote(SimpleNamespace(is_terminal=False)).may_still_run("gen_1") is True
    assert _remote(SimpleNamespace(is_terminal=True)).may_still_run("gen_1") is False
    assert _remote(None).may_still_run("gen_1") is False


def test_a_remote_backend_runs_a_job_it_is_still_driving():
    backend = _remote(None)
    backend._active.add("gen_1")

    assert backend.may_still_run("gen_1") is True


def test_a_cloud_backend_runs_only_the_jobs_it_holds():
    backend = object.__new__(CloudBackend)
    backend._runs = {"gen_1": object()}

    assert backend.may_still_run("gen_1") is True
    assert backend.may_still_run("gen_2") is False
