import asyncio
from types import SimpleNamespace

import pytest

from src.features.elixirs.coordinator import Coordinator
from src.features.elixirs.limits import Limits
from src.features.elixirs.repository import ElixirRepository
from src.features.elixirs.run_repository import RunStore
from tests.features.elixirs.conftest import make_client, make_components

KREA = "4TK1KBQZ2XMB8ME0PTMXS1YJQP"


def library(item_id):
    return {"kind": "library", "item_id": item_id, "media_type": "image", "original_filename": f"{item_id}.png"}


def definition(snapshot=True, assets=("p1", "p2")):
    return {
        "schema_version": 1,
        "nodes": [
            {"id": "photos", "kind": "media", "title": "Photos", "media_type": "image", "assets": [library(item) for item in assets]},
            {
                "id": "sharpen",
                "kind": "preset",
                "title": "Sharpen",
                "preset_id": KREA,
                "mode": "enhance",
                "overrides": {},
                "session_source": {"id": "s1"} if snapshot else None,
                "session_snapshot": {"prompt": "crisp", "formData": {"quantity": 1}} if snapshot else None,
            },
        ],
        "edges": [{"id": "e1", "source": "photos", "source_handle": "images", "target": "sharpen", "target_handle": "source_image", "iteration": "each"}],
    }


TEMPLATES = {
    "sharpen": {
        "preset_id": KREA,
        "mode": "enhance",
        "prompts": [{"positive": "crisp", "negative": ""}],
        "form_data": {"seed": 5, "quantity": 1},
    }
}


@pytest.fixture
def harness(scratch_db, port, catalog, contracts):
    coordinator = Coordinator(store=RunStore(), port=port, limits_provider=Limits, boot_id="boot-1", random_below=lambda limit: 3)
    state = SimpleNamespace(user=port.users["alice"], port=port, coordinator=coordinator)
    components = make_components(port, catalog=catalog, contracts=contracts, coordinator=coordinator)
    state.client = make_client(components, lambda: state.user)
    state.pump = lambda: asyncio.run(coordinator.pump())
    return state


def elixir(run_mode="automatic", **kwargs):
    record = ElixirRepository().create("alice", "Campaign", run_mode, definition(**kwargs))
    return record


def brew(harness, record, key="brew-key-1", **body):
    payload = {"expected_revision": record.revision, "idempotency_key": key, "templates": TEMPLATES, **body}
    return harness.client.post(f"/api/elixirs/{record.id}/runs", json=payload)


def test_brewing_starts_a_run_and_its_progress_can_be_followed(harness):
    record = elixir()

    response = brew(harness, record)

    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "running"
    assert [item["status"] for item in run["invocations"]] == ["pending", "pending"]
    harness.pump()
    harness.port.generations["g1"]["status"] = "running"
    harness.port.generations["g1"]["progress"] = 0.5
    detail = harness.client.get(f"/api/elixirs/runs/{run['id']}").json()
    assert [item["status"] for item in detail["invocations"]] == ["submitted", "submitted"]
    assert all(item["submitted_at"] and item["finished_at"] is None for item in detail["invocations"])
    harness.port.finish("g1", ("f1", "IMAGE"))
    harness.port.finish("g2", ("f2", "IMAGE"))
    harness.pump()
    done = harness.client.get(f"/api/elixirs/runs/{run['id']}").json()
    assert done["status"] == "completed"
    assert all(item["finished_at"] for item in done["invocations"])
    assert done["invocations"][0]["outputs"][0]["preview_url"] == "/api/media/files/f1?size=medium"
    listed = harness.client.get(f"/api/elixirs/{record.id}/runs").json()["runs"]
    assert [item["id"] for item in listed] == [run["id"]]


def test_brewing_twice_with_the_same_key_returns_the_same_run(harness):
    record = elixir()

    first = brew(harness, record).json()
    second = brew(harness, record).json()

    assert second["id"] == first["id"]
    assert len(harness.client.get(f"/api/elixirs/{record.id}/runs").json()["runs"]) == 1


def test_a_stale_elixir_cannot_be_brewed(harness):
    record = elixir()

    response = brew(harness, record, expected_revision=record.revision + 1)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "revision_conflict"


def test_an_elixir_with_problems_is_not_brewed(harness):
    record = elixir(snapshot=False)

    response = brew(harness, record)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "elixir_not_ready"
    assert any(issue["code"] == "no_session" for issue in detail["issues"])
    assert harness.port.submits == []


def test_a_template_for_another_mode_is_refused(harness):
    record = elixir()
    templates = {"sharpen": {**TEMPLATES["sharpen"], "mode": "txt2img"}}

    response = brew(harness, record, templates=templates)

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "template_mismatch",
        "message": "Sharpen's prepared request is for another preset or mode.",
        "node_id": "sharpen",
    }


def test_choosing_files_then_continuing(harness):
    record = elixir(run_mode="review")
    run = brew(harness, record).json()
    base = f"/api/elixirs/runs/{run['id']}/edges/e1"

    assert run["status"] == "awaiting_selection"
    drafted = harness.client.put(f"{base}/selection", json={"expected_revision": 1, "selected": ["library:p2"]}).json()
    assert drafted["checkpoints"]["e1"]["draft"] == ["library:p2"]
    stale = harness.client.post(f"{base}/continue", json={"expected_revision": 1, "selected": ["library:p2"]})
    continued = harness.client.post(f"{base}/continue", json={"expected_revision": 2, "selected": ["library:p2"]})

    assert stale.status_code == 409
    assert continued.status_code == 200
    assert [item["input_key"] for item in continued.json()["invocations"]] == ["library:p2"]
    empty = harness.client.post(f"{base}/continue", json={"expected_revision": 3, "selected": []})
    assert empty.status_code == 409


def test_stopping_a_brew(harness):
    record = elixir()
    run = brew(harness, record).json()
    harness.pump()

    stopped = harness.client.post(f"/api/elixirs/runs/{run['id']}/cancel").json()
    harness.pump()

    assert stopped["status"] == "cancelling"
    assert harness.client.get(f"/api/elixirs/runs/{run['id']}").json()["status"] == "cancelled"
    assert sorted(harness.port.cancels) == ["g1", "g2"]


def test_retrying_failed_work(harness):
    record = elixir(assets=("p1",))
    run = brew(harness, record).json()
    harness.pump()
    harness.port.fail("g1")
    harness.pump()

    nothing = harness.client.post(f"/api/elixirs/runs/{run['id']}/nodes/photos/retry-failed")
    retried = harness.client.post(f"/api/elixirs/runs/{run['id']}/nodes/sharpen/retry-failed")

    assert nothing.status_code == 409
    assert retried.status_code == 200
    assert [item["attempt"] for item in retried.json()["invocations"]] == [1, 2]


def test_another_users_brews_are_not_found(harness):
    record = elixir()
    run = brew(harness, record).json()
    harness.user = harness.port.make_user("bob")

    responses = [
        brew(harness, record, key="brew-key-2"),
        harness.client.get(f"/api/elixirs/{record.id}/runs"),
        harness.client.get(f"/api/elixirs/runs/{run['id']}"),
        harness.client.put(f"/api/elixirs/runs/{run['id']}/edges/e1/selection", json={"expected_revision": 1, "selected": []}),
        harness.client.post(f"/api/elixirs/runs/{run['id']}/edges/e1/continue", json={"expected_revision": 1, "selected": ["library:p1"]}),
        harness.client.post(f"/api/elixirs/runs/{run['id']}/cancel"),
        harness.client.post(f"/api/elixirs/runs/{run['id']}/nodes/sharpen/retry-failed"),
    ]

    assert [response.status_code for response in responses] == [404] * 7


def test_an_elixir_cannot_be_deleted_while_it_brews(harness):
    record = elixir()
    brew(harness, record)

    response = harness.client.delete(f"/api/elixirs/{record.id}")

    assert response.status_code == 409


def test_running_one_node_again(harness):
    record = elixir(assets=("p1",))
    brew(harness, record)
    harness.pump()
    harness.port.finish("g1", ("f1", "IMAGE"))
    harness.pump()
    url = f"/api/elixirs/{record.id}/nodes/sharpen/runs"
    body = {"expected_revision": record.revision, "idempotency_key": "node-run-key", "template": TEMPLATES["sharpen"]}

    response = harness.client.post(url, json=body)
    stale = harness.client.post(url, json={**body, "expected_revision": record.revision + 1, "idempotency_key": "node-run-key-2"})
    harness.user = harness.port.make_user("bob")
    foreign = harness.client.post(url, json={**body, "idempotency_key": "node-run-key-3"})

    assert response.status_code == 201, response.text
    assert response.json()["scope"] == "node"
    assert [item["input_key"] for item in response.json()["invocations"]] == ["library:p1"]
    assert stale.status_code == 409
    assert foreign.status_code == 404


def test_a_node_with_problems_cannot_run_alone(harness):
    record = elixir(snapshot=False)

    response = harness.client.post(
        f"/api/elixirs/{record.id}/nodes/sharpen/runs",
        json={"expected_revision": record.revision, "idempotency_key": "node-run-key", "template": TEMPLATES["sharpen"]},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "node_not_ready"
