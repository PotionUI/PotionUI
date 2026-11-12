from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.features.backends.routes import BackendController
from src.features.generation.routes import GenerationController
from src.features.generation.routing.load import BackendLoad, BackendLoadLedger


class _Hub:
    def __init__(self):
        self.calls = []

    async def broadcast_to_generation(self, generation_id, message, privileged_message=None):
        self.calls.append((generation_id, message, privileged_message))


def _controller(queue_info, status_payload):
    controller = object.__new__(GenerationController)
    controller.connection_hub = _Hub()
    controller.generation_orchestrator = Mock()
    controller.generation_orchestrator.get_queue_info = Mock(return_value=queue_info)
    controller._resolve_generation_state = AsyncMock(
        return_value=SimpleNamespace(owner_id="u1", payload=status_payload)
    )
    return controller


def _user(admin):
    return SimpleNamespace(id="u1", account_type=SimpleNamespace(value="admin" if admin else "user"))


@pytest.mark.asyncio
async def test_a_queue_update_without_admin_detail_goes_to_everyone_unchanged():
    controller = _controller(None, {})
    message = {"type": "queue_update", "generation_id": "g", "status": "running", "queue_position": None}

    await controller._broadcast_queue_update("g", message)

    assert controller.connection_hub.calls == [("g", message, None)]


@pytest.mark.asyncio
async def test_a_queue_update_hides_the_backend_from_viewers_and_shows_it_to_admins():
    controller = _controller(None, {})
    message = {
        "type": "queue_update", "generation_id": "g", "status": "pending", "queue_position": 1,
        "queue_depth": 2, "waiting_reason": None, "admin_detail": {"backend_id": "A", "backend_name": "Rack A"},
    }

    await controller._broadcast_queue_update("g", message)

    _, viewer, privileged = controller.connection_hub.calls[0]
    assert "admin_detail" not in viewer
    assert "Rack A" not in str(viewer)
    assert viewer["queue_position"] == 1 and viewer["queue_depth"] == 2
    assert privileged["admin_detail"]["backend_name"] == "Rack A"


@pytest.fixture
def admin_check(monkeypatch):
    monkeypatch.setattr(
        "src.features.generation.routes.GenerationPolicy.is_admin", staticmethod(lambda user: user.account_type.value == "admin")
    )
    monkeypatch.setattr(
        "src.features.generation.routes.GenerationPolicy.can_access", staticmethod(lambda user, owner: True)
    )


def _info():
    return {
        "queue_position": 2, "queue_depth": 3, "waiting_reason": None,
        "admin_detail": {"backend_id": "A", "backend_name": "Rack A"},
    }


@pytest.mark.asyncio
async def test_the_status_route_adds_the_queue_place_for_a_waiting_job(admin_check):
    controller = _controller(_info(), {"id": "g", "status": "pending", "progress": None})

    response = await controller.get_generation_status("g", _user(admin=False))

    data = response.data
    assert (data["id"], data["status"], data["progress"]) == ("g", "pending", None)
    assert (data["queue_position"], data["queue_depth"], data["waiting_reason"]) == (2, 3, None)
    assert "backend_name" not in data and "backend_id" not in data and "admin_detail" not in data


@pytest.mark.asyncio
async def test_the_status_route_names_the_backend_to_admins_only(admin_check):
    controller = _controller(_info(), {"id": "g", "status": "pending"})

    response = await controller.get_generation_status("g", _user(admin=True))

    assert response.data["backend_name"] == "Rack A"
    assert response.data["queue_position"] == 2


@pytest.mark.asyncio
async def test_the_status_route_is_unchanged_for_a_job_that_is_not_waiting(admin_check):
    controller = _controller(_info(), {"id": "g", "status": "running", "progress": 0.5})

    response = await controller.get_generation_status("g", _user(admin=False))

    assert response.data == {"id": "g", "status": "running", "progress": 0.5}


@pytest.mark.asyncio
async def test_the_status_route_is_unchanged_when_the_queue_has_nothing_to_add(admin_check):
    controller = _controller(None, {"id": "g", "status": "pending"})

    response = await controller.get_generation_status("g", _user(admin=False))

    assert response.data == {"id": "g", "status": "pending"}


def test_the_ledger_weights_queued_plus_running_by_slots():
    ledger = BackendLoadLedger(counts=lambda b: (3, 1), capacity=lambda b: 2)

    load = ledger.load("x")

    assert (load.queued, load.running, load.capacity, load.total) == (3, 1, 2, 4)
    assert float(load.weighted) == 2.0
    assert isinstance(load, BackendLoad)


def _backend_controller(load_source):
    store = Mock()
    store.get_default_backend_ids.return_value = {}
    store.get_backends.return_value = [SimpleNamespace(id="A"), SimpleNamespace(id="B")]
    registry = Mock(backend_config_store=store)
    controller = BackendController(Mock(), registry, load_source=load_source)
    controller._serialize = lambda backend, default_ids=None: {"id": backend.id, "name": backend.id}
    return controller


@pytest.mark.asyncio
async def test_the_admin_backend_list_carries_queued_and_running_counts():
    snapshots = {
        "A": {"queued": 2, "running": 1, "max_concurrent_runs": 1, "unhealthy_reason": None},
        "B": {"queued": 0, "running": 0, "max_concurrent_runs": 2, "unhealthy_reason": "connection refused"},
    }
    controller = _backend_controller(lambda backend_id: snapshots[backend_id])

    response = await controller.list_backends(include_load=True)

    assert response.data[0] == {"id": "A", "name": "A", **snapshots["A"]}
    assert response.data[1]["unhealthy_reason"] == "connection refused"


@pytest.mark.asyncio
async def test_the_backend_list_for_everyone_else_is_unchanged():
    controller = _backend_controller(lambda backend_id: {"queued": 9})

    assert (await controller.list_backends()).data == [{"id": "A", "name": "A"}, {"id": "B", "name": "B"}]
    assert (await controller.list_backends(include_load=False)).data[0] == {"id": "A", "name": "A"}
