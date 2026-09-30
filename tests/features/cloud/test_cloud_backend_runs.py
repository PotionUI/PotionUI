import asyncio
import threading
import time

import pytest

from src.features.cloud.backend import CloudGenerationUnavailable
from src.features.cloud.clock import MonotonicClock
from src.features.cloud.testing.fake import FakeBehaviour
from src.features.generation.status_tracker import GenerationState
from tests.features.cloud.cloud_generation_harness import SECRET, USER_ID, CloudGeneration


def add_user(db):
    with db.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (id, username, email, password_hash, account_type) VALUES (?, ?, ?, 'x', 'USER')",
            (USER_ID, USER_ID, f"{USER_ID}@example.test"),
        )


@pytest.fixture
async def run(mock_db, tmp_path):
    add_user(mock_db)
    started = await CloudGeneration(
        tmp_path, FakeBehaviour(mode="async", duration_s=100_000, poll_after_s=300), max_parallel=2
    ).start()
    started.backend.clock = MonotonicClock()
    return started




async def test_the_cloud_pipe_is_told_its_backend_and_nothing_secret(fake_backend):
    backend = fake_backend.backend()
    pipes = [
        {"name": "seed_generator", "config": {"seed": 1}},
        {"name": "cloud_generate", "config": {"model": "m"}},
    ]

    prepared = backend.prepare_pipes(pipes)

    assert prepared[0] is pipes[0]
    assert prepared[1]["config"] == {"model": "m", "cloud": {"backend_id": "cloud-1", "driver": "cloud.fake"}}
    assert "cloud" not in pipes[1]["config"]
    assert SECRET not in repr(prepared)


async def test_the_registry_hands_every_cloud_backend_the_executor_factory(cloud_env):
    await cloud_env.add_backend("cloud-1")
    sentinel = object()
    cloud_env.registry.generation_engine_factory = lambda: sentinel
    await cloud_env.add_backend("cloud-2")

    assert cloud_env.backend("cloud-2")._executor_factory() is sentinel


async def test_a_backend_without_an_executor_factory_cannot_start_a_run(fake_backend):
    with pytest.raises(CloudGenerationUnavailable):
        await fake_backend.backend().start_generation({"generation_id": "g", "pipes": [{"name": "gallery"}]}, lambda _: None)


async def test_starting_a_run_needs_a_pipeline(fake_backend):
    fake_backend.backend().bind_executor_factory(lambda: object())

    with pytest.raises(ValueError):
        await fake_backend.backend().start_generation({"generation_id": "g", "pipes": []}, lambda _: None)


async def test_cancelling_a_run_that_is_not_active_reports_false(fake_backend):
    assert await fake_backend.backend().cancel_generation("nothing") is False


async def test_each_run_gets_its_own_executor_so_two_run_at_once(run):
    await run.submit("gen-1")
    await run.submit("gen-2")
    while run.behaviour.calls.count("submit") < 2:
        await asyncio.sleep(0)

    tracker = run.orchestrator.status_tracker
    assert tracker.get("gen-1").state == GenerationState.RUNNING
    assert tracker.get("gen-2").state == GenerationState.RUNNING
    per_run = run.executors[run.baseline:]
    assert len(per_run) == 2 and per_run[0] is not per_run[1]
    assert sorted(run.backend._runs) == ["gen-1", "gen-2"]

    await run.orchestrator.cancel_generation("gen-1")
    await run.orchestrator.cancel_generation("gen-2")
    await run.drained()


async def test_cancelling_one_of_two_runs_leaves_the_other_running(run):
    await run.submit("gen-1")
    await run.submit("gen-2")
    while run.behaviour.calls.count("submit") < 2:
        await asyncio.sleep(0)

    await run.orchestrator.cancel_generation("gen-1")
    while "gen-1" in run.backend._runs:
        await asyncio.sleep(0)

    tracker = run.orchestrator.status_tracker
    assert tracker.get("gen-1").state == GenerationState.CANCELLED
    assert tracker.get("gen-2").state == GenerationState.RUNNING
    assert list(run.backend._runs) == ["gen-2"]

    await run.orchestrator.cancel_generation("gen-2")
    await run.drained()


async def test_a_third_run_waits_for_a_free_slot(run):
    await run.submit("gen-1")
    await run.submit("gen-2")
    third = await run.submit("gen-3")

    assert third["queue_position"] == 0
    assert run.orchestrator.status_tracker.get("gen-3").state == GenerationState.PENDING

    for generation_id in ("gen-1", "gen-2", "gen-3"):
        await run.orchestrator.cancel_generation(generation_id)
    await run.drained()


@pytest.fixture
def temp_root(tmp_path, monkeypatch):
    import tempfile

    root = tmp_path / "systmp"
    root.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(root))
    return root / "potionui-cloud" / "cloud-1"


async def test_a_finished_run_leaves_no_scratch_folder_behind(run, temp_root):
    run.behaviour.mode = "sync"
    run.backend.clock = run.clock

    await run.run()
    await run.drained()

    assert not temp_root.exists() or list(temp_root.iterdir()) == []


async def test_the_sweep_removes_empty_folders_but_keeps_files_still_being_consumed(fake_backend, temp_root):
    backend = fake_backend.backend()
    import os

    (temp_root / "empty" / "run-1").mkdir(parents=True)
    os.utime(temp_root / "empty", (time.time() - 7200, time.time() - 7200))
    (temp_root / "busy" / "run-1").mkdir(parents=True)
    (temp_root / "busy" / "run-1" / "0.mp4").write_bytes(b"v")

    backend._sweep_scratch()

    assert not (temp_root / "empty").exists()
    assert (temp_root / "busy" / "run-1" / "0.mp4").exists()


async def test_the_sweep_removes_folders_abandoned_for_a_day(fake_backend, temp_root):
    import os

    backend = fake_backend.backend()
    old = temp_root / "old"
    (old / "run-1").mkdir(parents=True)
    (old / "run-1" / "0.mp4").write_bytes(b"v")
    aged = 0
    os.utime(old, (aged, aged))

    backend._sweep_scratch()

    assert not old.exists()


async def test_the_sweep_never_touches_a_running_generation(fake_backend, temp_root):
    backend = fake_backend.backend()
    (temp_root / "live" / "run-1").mkdir(parents=True)
    backend._runs["live"] = object()

    backend._sweep_scratch()

    assert (temp_root / "live").exists()
    backend._runs.clear()


async def test_a_fresh_empty_folder_survives_the_sweep(fake_backend, temp_root):
    backend = fake_backend.backend()
    (temp_root / "starting" / "run-1").mkdir(parents=True)

    backend._sweep_scratch()

    assert (temp_root / "starting" / "run-1").exists()


async def test_another_backends_sweep_never_touches_this_backends_folders(cloud_env, temp_root):
    import os

    first = await cloud_env.add_backend("cloud-1")
    second = await cloud_env.add_backend("cloud-2")
    fresh = first._scratch_root() / "fresh" / "run-1"
    aged = first._scratch_root() / "aged" / "run-1"
    fresh.mkdir(parents=True)
    aged.mkdir(parents=True)
    os.utime(aged.parent, (time.time() - 7200, time.time() - 7200))

    second._sweep_scratch()

    assert first._scratch_root() != second._scratch_root()
    assert fresh.exists() and aged.exists()
    first._sweep_scratch()
    assert fresh.exists() and not aged.parent.exists()


async def test_the_sweep_runs_off_the_event_loop(fake_backend, temp_root, monkeypatch):
    backend = fake_backend.backend()
    seen = []
    monkeypatch.setattr(backend, "_sweep_scratch", lambda finished=None: seen.append(threading.get_ident()))

    await backend._sweep_scratch_off_loop()

    assert seen and seen[0] != threading.get_ident()
