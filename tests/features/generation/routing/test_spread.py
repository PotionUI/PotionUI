import asyncio
from unittest.mock import Mock

import pytest

from src.features.backends.backend_registry import BackendRegistry
from src.features.generation.routing.contracts import NoEligibleBackendError, RoutingRequest
from src.features.generation.routing.health import BackendHealthTracker
from src.features.generation.routing.load import BackendLoadLedger
from src.features.generation.routing.registry import BUILTIN_RULES
from src.features.generation.routing.router import GenerationRouter


class Cluster:
    def __init__(self, capacities, default_id=None, driver=None):
        self.backends = []
        for index, capacity in enumerate(capacities):
            backend = Mock(backend_id=f"b{index}")
            backend.name = f"b{index}"
            backend.config.priority = 10 - index
            backend.execution_device = "this_host_gpu"
            backend.config.effective_driver = (driver or {}).get(f"b{index}", "native")
            self.backends.append(backend)
        self.capacity = {f"b{i}": c for i, c in enumerate(capacities)}
        self.queued = {}
        self.running = {}
        registry = object.__new__(BackendRegistry)
        registry.get_backends_for_engine = Mock(side_effect=lambda engine: list(self.backends))
        registry._backends_cache = {b.backend_id: b for b in self.backends}
        registry.backend_config_store = Mock()
        registry.backend_config_store.get_default_backend.return_value = Mock(id=default_id) if default_id else None
        self.health = BackendHealthTracker()
        self.ledger = BackendLoadLedger(
            counts=lambda bid: (self.queued.get(bid, 0), self.running.get(bid, 0)),
            capacity=lambda bid: self.capacity[bid],
        )
        self.router = GenerationRouter(list(BUILTIN_RULES), backend_registry=registry)
        self.router.bind_load(self.ledger)
        self.router.bind_health(self.health)

    async def pick(self, reserve=True, preset=None):
        decision = await self.router.route(
            RoutingRequest(engine="native", preset=preset or Mock(requirements=[], driver=None)), reserve=reserve
        )
        return decision

    async def sequence(self, count):
        return [(await self.pick()).chosen.backend_id for _ in range(count)]


@pytest.mark.asyncio
async def test_two_equal_backends_share_jobs_evenly():
    cluster = Cluster([1, 1])

    assert await cluster.sequence(4) == ["b0", "b1", "b0", "b1"]


@pytest.mark.asyncio
async def test_a_backend_with_twice_the_slots_takes_twice_the_jobs():
    cluster = Cluster([2, 1])

    assert await cluster.sequence(6) == ["b0", "b1", "b0", "b0", "b1", "b0"]


@pytest.mark.asyncio
async def test_three_backends_of_different_size_fill_by_weighted_load():
    cluster = Cluster([1, 2, 4])

    picks = await cluster.sequence(14)

    assert [picks.count(b) for b in ("b0", "b1", "b2")] == [2, 4, 8]


@pytest.mark.asyncio
async def test_existing_queue_and_running_work_counts_as_load():
    cluster = Cluster([1, 1])
    cluster.running["b0"] = 1
    cluster.queued["b0"] = 2

    assert (await cluster.pick(reserve=False)).chosen.backend_id == "b1"


@pytest.mark.asyncio
async def test_a_tie_goes_to_the_default_backend_then_to_priority_order():
    by_order = Cluster([1, 1, 1])
    by_default = Cluster([1, 1, 1], default_id="b2")

    assert (await by_order.pick(reserve=False)).chosen.backend_id == "b0"
    assert (await by_default.pick(reserve=False)).chosen.backend_id == "b2"


@pytest.mark.asyncio
async def test_the_least_loaded_backend_beats_the_default_one():
    cluster = Cluster([1, 1], default_id="b0")
    cluster.running["b0"] = 1

    assert (await cluster.pick(reserve=False)).chosen.backend_id == "b1"


@pytest.mark.asyncio
async def test_a_preset_pinned_to_a_driver_only_uses_backends_of_that_driver():
    cluster = Cluster([1, 1], driver={"b0": "native", "b1": "native.remote"})
    cluster.running["b1"] = 5

    decision = await cluster.pick(reserve=False, preset=Mock(requirements=[], driver="native.remote"))

    assert decision.chosen.backend_id == "b1"


@pytest.fixture
def model_only_on(monkeypatch):
    def apply(*backend_ids):
        monkeypatch.setattr("src.features.models.form_refs.collect_model_ids", lambda form: ["m1"])
        monkeypatch.setattr("src.features.models.availability.has_authoritative_listing", lambda backends: True)
        monkeypatch.setattr(
            "src.features.models.availability.require_candidate_backends",
            lambda engine, ids, registry: list(backend_ids),
        )

    return apply


@pytest.mark.asyncio
async def test_a_model_present_on_one_backend_only_is_never_routed_elsewhere(model_only_on):
    cluster = Cluster([1, 1])
    model_only_on("b1")
    cluster.running["b1"] = 1
    cluster.queued["b1"] = 3

    for _ in range(3):
        decision = await cluster.router.route(
            RoutingRequest(engine="native", preset=Mock(requirements=[], driver=None), form_data={"m": "model:m1"})
        )
        assert decision.chosen.backend_id == "b1"
        assert decision.candidates[0].dropped


@pytest.mark.asyncio
async def test_an_unhealthy_backend_is_skipped_even_when_it_is_the_least_loaded():
    cluster = Cluster([1, 1])
    cluster.running["b1"] = 1
    cluster.health.record("b0", False, "connection refused")

    decision = await cluster.pick(reserve=False)

    assert decision.chosen.backend_id == "b1"
    assert "connection refused" in decision.candidates[0].reasons[0]


@pytest.mark.asyncio
async def test_a_recovered_backend_is_used_again():
    cluster = Cluster([1, 1])
    cluster.running["b1"] = 1
    cluster.health.record("b0", False, "down")
    cluster.health.record("b0", True)

    assert (await cluster.pick(reserve=False)).chosen.backend_id == "b0"


@pytest.mark.asyncio
async def test_when_every_backend_is_down_the_job_is_still_routed_and_says_it_waits():
    cluster = Cluster([1, 1])
    cluster.health.record("b0", False, "down")
    cluster.health.record("b1", False, "down")

    decision = await cluster.pick(reserve=False)

    assert decision.chosen is not None
    assert all("waits for it to recover" in c.reasons[0] for c in decision.candidates)


@pytest.mark.asyncio
async def test_a_disabled_backend_is_not_a_candidate():
    cluster = Cluster([1, 1])
    cluster.backends.pop(0)

    assert (await cluster.pick(reserve=False)).chosen.backend_id == "b1"


@pytest.mark.asyncio
async def test_no_backend_at_all_still_raises_a_clear_error():
    cluster = Cluster([1])
    cluster.backends.clear()

    with pytest.raises(NoEligibleBackendError):
        await cluster.pick(reserve=False)


@pytest.mark.asyncio
async def test_a_reservation_counts_until_released():
    cluster = Cluster([1, 1])
    first = await cluster.pick()

    assert cluster.ledger.load("b0").reserved == 1
    cluster.router.release(first.chosen.backend_id)

    assert cluster.ledger.load("b0").reserved == 0


@pytest.mark.asyncio
async def test_a_route_that_does_not_reserve_leaves_the_load_alone():
    cluster = Cluster([1, 1])

    await cluster.pick(reserve=False)

    assert cluster.ledger.load("b0").total == 0


@pytest.mark.asyncio
async def test_two_jobs_arriving_at_once_do_not_both_take_the_last_slot():
    cluster = Cluster([1, 1])

    class Yielding:
        name = "yielding"

        async def apply(self, candidates, request, ctx):
            await asyncio.sleep(0)
            return candidates

    cluster.router._rules.insert(1, Yielding())

    decisions = await asyncio.gather(*(cluster.pick() for _ in range(2)))

    assert sorted(d.chosen.backend_id for d in decisions) == ["b0", "b1"]


@pytest.mark.asyncio
async def test_many_jobs_arriving_at_once_spread_evenly():
    cluster = Cluster([1, 1, 1])

    class Yielding:
        name = "yielding"

        async def apply(self, candidates, request, ctx):
            await asyncio.sleep(0)
            return candidates

    cluster.router._rules.insert(1, Yielding())

    decisions = await asyncio.gather(*(cluster.pick() for _ in range(9)))

    picks = [d.chosen.backend_id for d in decisions]
    assert [picks.count(b) for b in ("b0", "b1", "b2")] == [3, 3, 3]


@pytest.mark.asyncio
async def test_without_a_load_source_the_old_default_then_priority_rule_applies():
    cluster = Cluster([1, 1], default_id="b1")
    cluster.router.bind_load(None)
    cluster.running["b1"] = 9

    assert (await cluster.pick(reserve=False)).chosen.backend_id == "b1"


@pytest.mark.asyncio
async def test_routing_probes_a_remote_backend_in_the_background_and_a_failed_probe_takes_it_out():
    cluster = Cluster([1, 1])
    remote, local = cluster.backends
    remote.execution_device = "remote"
    probes = []

    async def health_check():
        probes.append(1)
        return {"status": "offline", "error": "refused"}

    remote.health_check = health_check

    first = await cluster.pick(reserve=False)
    for _ in range(3):
        await asyncio.sleep(0)
    second = await cluster.pick(reserve=False)

    assert first.chosen is remote
    assert second.chosen is local
    assert probes == [1]
    assert not cluster.health.is_healthy("b0")
    assert cluster.health.is_healthy("b1")


@pytest.mark.asyncio
async def test_a_backend_on_this_hosts_gpu_is_never_probed():
    cluster = Cluster([1])
    cluster.backends[0].health_check = Mock(side_effect=AssertionError("must not be probed"))

    await cluster.pick(reserve=False)
    await asyncio.sleep(0)

    cluster.backends[0].health_check.assert_not_called()
