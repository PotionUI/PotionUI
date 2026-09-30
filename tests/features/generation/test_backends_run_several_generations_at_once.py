import pytest
from unittest.mock import AsyncMock, Mock, patch

from src.features.backends.base_backend import BaseBackend
from src.features.generation.queue import GenerationQueue, QueuedGeneration
from src.features.generation.status_tracker import GenerationState


def _item(gid, backend="cloud", user="u1"):
    return QueuedGeneration(generation_id=gid, backend_id=backend, user_id=user)


class TestQueueCapacity:
    @pytest.fixture
    def dispatched(self):
        return []

    @pytest.fixture
    def queue(self, dispatched):
        async def dispatch(item):
            dispatched.append(item.generation_id)

        capacities = {"cloud": 2}
        return GenerationQueue(dispatch=dispatch, capacity_for=lambda bid: capacities.get(bid, 1))

    async def test_two_start_and_the_third_waits(self, queue, dispatched):
        for gid in ("a", "b", "c"):
            await queue.enqueue(_item(gid))

        assert dispatched == ["a", "b"]
        assert queue.position("c") == 0
        assert queue.running_generation_ids("cloud") == ["a", "b"]

    async def test_the_third_starts_when_one_finishes(self, queue, dispatched):
        for gid in ("a", "b", "c"):
            await queue.enqueue(_item(gid))

        await queue.release("cloud", "b")

        assert dispatched == ["a", "b", "c"]
        assert queue.position("c") is None
        assert queue.running_generation_ids("cloud") == ["a", "c"]

    async def test_waiting_items_keep_arrival_order(self, queue, dispatched):
        for gid in ("a", "b", "c", "d"):
            await queue.enqueue(_item(gid))

        await queue.release("cloud", "a")
        await queue.release("cloud", "b")

        assert dispatched == ["a", "b", "c", "d"]

    async def test_a_backend_without_a_capacity_keeps_one_slot(self, queue, dispatched):
        for gid in ("a", "b"):
            await queue.enqueue(_item(gid, backend="native"))

        assert dispatched == ["a"]
        await queue.release("native", "a")
        assert dispatched == ["a", "b"]

    async def test_a_failed_dispatch_frees_only_its_own_slot(self):
        started = []

        async def dispatch(item):
            if item.generation_id == "bad":
                raise RuntimeError("boom")
            started.append(item.generation_id)

        queue = GenerationQueue(dispatch=dispatch, capacity_for=lambda bid: 2)
        await queue.enqueue(_item("a"))
        with pytest.raises(RuntimeError):
            await queue.enqueue(_item("bad"))
        await queue.enqueue(_item("c"))

        assert started == ["a", "c"]
        assert queue.running_generation_ids("cloud") == ["a", "c"]

    async def test_the_snapshot_lists_every_running_generation(self, queue):
        for gid in ("a", "b", "c"):
            await queue.enqueue(_item(gid))

        snapshot = queue.snapshot()

        assert snapshot["running"] == {"cloud": ["a", "b"]}
        assert [p["generation_id"] for p in snapshot["pending"]] == ["c"]


def test_a_backend_runs_one_generation_unless_it_says_otherwise():
    assert BaseBackend.max_concurrent_runs.fget(Mock()) == 1


@pytest.fixture(autouse=True)
def _bind_form_passthrough():
    from src.features.forms.binding import BoundForm

    def _passthrough(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None):
        return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or "custom", coercions=[], stripped=[])

    with patch("src.features.generation.orchestrator.bind_form", side_effect=_passthrough):
        yield


@pytest.fixture
def backends():
    def _backend(backend_id, engine, capacity=None):
        b = Mock()
        b.backend_id = backend_id
        b.name = backend_id
        b.engine = engine
        b.start_generation = AsyncMock()
        b.cancel_generation = AsyncMock(return_value=True)
        if capacity is not None:
            b.max_concurrent_runs = capacity
        return b

    return {
        "native": _backend("native_1", "native"),
        "cloud": _backend("cloud_1", "cloud", capacity=2),
    }


@pytest.fixture
def orchestrator(backends):
    from src.features.backends.backend_registry import BackendRegistry
    from src.features.generation.orchestrator import GenerationOrchestrator
    from src.features.generation.pipeline_builder import BuiltPipeline, PipelineBuilder

    builder = Mock(spec=PipelineBuilder)
    builder.build_pipeline = Mock(side_effect=lambda **kw: BuiltPipeline(
        generation_id="x",
        preset_id="p",
        preset_template=Mock(version="1.0.0"),
        pipes=[{"name": "generator", "enabled": True, "config": {}}],
    ))

    registry = Mock(spec=BackendRegistry)
    registry.select_backend_for_generation = Mock(
        side_effect=lambda engine, **kw: backends["cloud"] if engine == "cloud" else backends["native"]
    )
    registry.get_backend = Mock(side_effect=lambda bid: next(
        (b for b in backends.values() if b.backend_id == bid), None
    ))

    loader = Mock()
    loader.load_preset_by_id = Mock(side_effect=lambda pid: Mock(engine=pid))

    processor = Mock()
    processor.process_output = AsyncMock(return_value={"processed": True})

    return GenerationOrchestrator(
        pipeline_builder=builder,
        backend_registry=registry,
        connection_hub=Mock(),
        settings=Mock(),
        output_processor=processor,
        preset_template_loader=loader,
    )


def _request(tab_id, preset_id):
    r = Mock()
    r.preset_id = preset_id
    r.form_data = {"steps": 20}
    r.prompt = "x"
    r.negative_prompt = ""
    r.prompts = None
    r.prompt_state = None
    r.mode = "txt2img"
    r.backend_id = None
    r.tag_ids = None
    r.segments = None
    r.tab_id = tab_id
    return r


@pytest.fixture
def repo():
    with patch("src.features.generation.orchestrator.generation_repo") as mock_repo, \
         patch("src.features.generation.status_tracker.generation_repo", mock_repo):
        mock_repo.create = Mock()
        mock_repo.update_status = Mock()
        mock_repo.get_by_id = Mock(return_value=None)
        yield mock_repo


async def _start(orchestrator, gen_id, preset="cloud"):
    with patch("src.features.generation.orchestrator.generate_ulid", return_value=gen_id):
        return await orchestrator.start_generation(_request(f"tab_{gen_id}", preset), "user_1")


class TestSeveralGenerationsThroughTheOrchestrator:
    async def test_two_run_at_once_and_the_third_waits_then_starts(self, orchestrator, backends, repo):
        first = await _start(orchestrator, "gen_1")
        second = await _start(orchestrator, "gen_2")
        third = await _start(orchestrator, "gen_3")

        assert first["queue_position"] is None
        assert second["queue_position"] is None
        assert third["queue_position"] == 0
        assert backends["cloud"].start_generation.await_count == 2

        await orchestrator._handle_generation_completion("gen_1", None)

        assert backends["cloud"].start_generation.await_count == 3
        assert orchestrator.status_tracker.get("gen_3").state == GenerationState.RUNNING
        assert orchestrator.status_tracker.get("gen_2").state == GenerationState.RUNNING

    async def test_every_run_keeps_its_own_backend_record(self, orchestrator, backends, repo):
        await _start(orchestrator, "gen_1")
        await _start(orchestrator, "gen_2")

        assert set(orchestrator._run_backends) == {"gen_1", "gen_2"}

        await orchestrator._handle_generation_completion("gen_1", None)

        assert set(orchestrator._run_backends) == {"gen_2"}

    async def test_cancelling_one_leaves_the_other_running(self, orchestrator, backends, repo):
        await _start(orchestrator, "gen_1")
        await _start(orchestrator, "gen_2")

        assert await orchestrator.cancel_generation("gen_1") is True

        backends["cloud"].cancel_generation.assert_awaited_once_with("gen_1")
        assert orchestrator.status_tracker.get("gen_1").state == GenerationState.CANCELLED
        assert orchestrator.status_tracker.get("gen_2").state == GenerationState.RUNNING

    async def test_the_admin_snapshot_lists_both_as_running(self, orchestrator, repo):
        await _start(orchestrator, "gen_1")
        await _start(orchestrator, "gen_2")
        await _start(orchestrator, "gen_3")

        snapshot = orchestrator.get_all_queue_snapshot()

        assert sorted(r["generation_id"] for r in snapshot["running"]) == ["gen_1", "gen_2"]
        assert [p["generation_id"] for p in snapshot["pending"]] == ["gen_3"]

    async def test_a_native_backend_keeps_one_slot(self, orchestrator, backends, repo):
        await _start(orchestrator, "gen_1", preset="native")
        second = await _start(orchestrator, "gen_2", preset="native")

        assert second["queue_position"] == 0
        assert backends["native"].start_generation.await_count == 1

    @pytest.mark.parametrize("bogus", [0, -3, "4", None, True])
    async def test_a_nonsense_capacity_falls_back_to_one(self, orchestrator, backends, repo, bogus):
        backends["cloud"].max_concurrent_runs = bogus

        await _start(orchestrator, "gen_1")
        second = await _start(orchestrator, "gen_2")

        assert second["queue_position"] == 0
