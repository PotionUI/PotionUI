import asyncio
from types import SimpleNamespace

import pytest

from src.features.elixirs.coordinator import Coordinator
from src.features.elixirs.limits import Limits
from src.features.elixirs.repository import ElixirRepository
from src.features.elixirs.run_repository import RunStore
from tests.features.elixirs.conftest import make_client, make_components

KREA = "4TK1KBQZ2XMB8ME0PTMXS1YJQP"
LIST = "/api/elixirs"


def library(item_id):
    return {"kind": "library", "item_id": item_id, "media_type": "image", "original_filename": f"{item_id}.png"}


def definition(assets=("p1", "p2")):
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
                "session_source": {"id": "s1"},
                "session_snapshot": {"prompt": "crisp", "formData": {"quantity": 1}},
            },
        ],
        "edges": [{"id": "e1", "source": "photos", "source_handle": "images", "target": "sharpen", "target_handle": "source_image", "iteration": "each"}],
    }


TEMPLATES = {
    "sharpen": {"preset_id": KREA, "mode": "enhance", "prompts": [{"positive": "crisp", "negative": ""}], "form_data": {"seed": 5, "quantity": 1}}
}


@pytest.fixture
def harness(scratch_db, port, catalog, contracts):
    coordinator = Coordinator(store=RunStore(), port=port, limits_provider=Limits, boot_id="boot-1", random_below=lambda limit: 3)
    state = SimpleNamespace(user=port.users["alice"], port=port, coordinator=coordinator)
    components = make_components(port, catalog=catalog, contracts=contracts, coordinator=coordinator)
    state.client = make_client(components, lambda: state.user)
    state.pump = lambda: asyncio.run(coordinator.pump())
    return state


def make(run_mode="automatic", name="Campaign", user="alice", assets=("p1", "p2")):
    return ElixirRepository().create(user, name, run_mode, definition(assets))


def brew(harness, record, key="brew-key-1"):
    body = {"expected_revision": record.revision, "idempotency_key": key, "templates": TEMPLATES}
    response = harness.client.post(f"{LIST}/{record.id}/runs", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def entry(harness, record):
    return next(item for item in harness.client.get(LIST).json()["elixirs"] if item["id"] == record.id)


def test_an_elixir_that_was_never_brewed(harness):
    record = make()

    item = entry(harness, record)

    assert item["status"] == "not_brewed"
    assert item["latest_outputs"] == [] and item["output_count"] == 0
    assert item["last_brewed_at"] is None and item["latest_run_id"] is None
    assert item["node_count"] == 2


def test_a_brewing_elixir_reports_progress(harness):
    record = make()
    run = brew(harness, record)
    harness.pump()

    item = entry(harness, record)

    assert item["status"] == "brewing"
    assert item["reason"] == "0 of 1 nodes"
    assert item["latest_run_id"] == run["id"]
    assert item["last_brewed_at"] == run["created_at"]


def test_a_review_pause_names_the_node_to_choose_from(harness):
    record = make(run_mode="review")
    brew(harness, record)

    item = entry(harness, record)

    assert item["status"] == "awaiting"
    assert item["reason"] == "Choose outputs from Photos"


def test_a_finished_brew_lists_its_latest_outputs(harness):
    record = make(name="One")
    brew(harness, record)
    harness.pump()
    harness.port.finish("g1", ("f1", "IMAGE"))
    harness.port.finish("g2", ("f2", "IMAGE"), ("f3", "VIDEO"))
    harness.pump()

    item = entry(harness, record)

    assert item["status"] == "done"
    assert item["reason"] == "3 files"
    assert item["status_at"]
    assert item["output_count"] == 3
    assert {output["file_id"] for output in item["latest_outputs"]} == {"f1", "f2", "f3"}
    video = next(output for output in item["latest_outputs"] if output["file_id"] == "f3")
    assert video["media_type"] == "video"
    assert video["preview_url"] == "/api/media/files/f3?size=medium"
    assert video["node_id"] == "sharpen"


def test_only_the_last_step_outputs_are_shown_when_there_are_several(harness):
    chain = definition(("p1",))
    chain["nodes"].append({**chain["nodes"][1], "id": "polish", "title": "Polish"})
    chain["edges"].append(
        {"id": "e2", "source": "sharpen", "source_handle": "images", "target": "polish", "target_handle": "source_image", "iteration": "each"}
    )
    record = ElixirRepository().create("alice", "Chain", "automatic", chain)
    body = {
        "expected_revision": record.revision,
        "idempotency_key": "brew-chain-1",
        "templates": {"sharpen": TEMPLATES["sharpen"], "polish": TEMPLATES["sharpen"]},
    }
    assert harness.client.post(f"{LIST}/{record.id}/runs", json=body).status_code == 201
    harness.pump()
    harness.port.media["history:g1:f1"] = {"value": "generations/g1/f1.png", "media_type": "image", "original_filename": "f1.png", "origin": None}
    harness.port.finish("g1", ("f1", "IMAGE"))
    for _ in range(4):
        harness.pump()
    harness.port.finish("g2", ("f2", "IMAGE"), ("f3", "IMAGE"))
    harness.pump()

    item = entry(harness, record)

    assert item["status"] == "done"
    assert {output["node_id"] for output in item["latest_outputs"]} == {"polish"}
    assert item["output_count"] == 2


def test_outputs_are_capped_but_counted(harness):
    record = make(assets=("p1",))
    brew(harness, record)
    harness.pump()
    harness.port.finish("g1", *((f"f{index}", "IMAGE") for index in range(9)))
    harness.pump()

    item = entry(harness, record)

    assert len(item["latest_outputs"]) == 6
    assert item["output_count"] == 9


def test_a_failed_node_needs_attention(harness):
    record = make(assets=("p1",))
    brew(harness, record)
    harness.pump()
    harness.port.fail("g1")
    harness.pump()

    item = entry(harness, record)

    assert item["status"] == "attention"
    assert item["reason"] == "Sharpen failed"


def test_a_stopped_brew_is_reported_as_stopped(harness):
    record = make()
    run = brew(harness, record)
    harness.pump()
    harness.client.post(f"/api/elixirs/runs/{run['id']}/cancel")
    harness.pump()

    assert entry(harness, record)["status"] == "stopped"


def test_each_elixir_gets_its_own_latest_brew_and_outputs(harness):
    first = make(name="First", assets=("p1",))
    second = make(name="Second", assets=("p2",))
    older = brew(harness, first, key="brew-first-1")
    harness.pump()
    harness.port.finish("g1", ("f1", "IMAGE"))
    harness.pump()
    brew(harness, first, key="brew-first-again")
    brew(harness, second, key="brew-second-1")
    harness.pump()
    harness.port.finish("g3", ("f3", "IMAGE"))
    harness.pump()

    one, two = entry(harness, first), entry(harness, second)

    assert one["latest_run_id"] != older["id"]
    assert [output["file_id"] for output in two["latest_outputs"]] == ["f3"]
    assert one["status"] == "brewing"
    assert two["status"] == "done"


def test_other_users_brews_never_show_up(harness, port):
    mine = make(assets=("p1",))
    brew(harness, mine)
    harness.pump()
    harness.port.finish("g1", ("f1", "IMAGE"))
    harness.pump()
    port.users["bob"] = port.make_user("bob")
    theirs = make(name="Bobs", user="bob")

    harness.user = port.users["bob"]
    listed = harness.client.get(LIST).json()["elixirs"]

    assert [item["id"] for item in listed] == [theirs.id]
    assert listed[0]["status"] == "not_brewed" and listed[0]["latest_outputs"] == []


def test_outputs_of_an_older_brew_are_marked_while_the_new_one_has_none(harness):
    record = make(assets=("p1",))
    first = brew(harness, record, key="brew-key-1")
    harness.pump()
    harness.port.finish("g1", ("f1", "IMAGE"))
    harness.pump()
    done = entry(harness, record)
    second = brew(harness, record, key="brew-key-2")

    item = entry(harness, record)

    assert done["outputs_current"] is True and done["outputs_run_id"] == first["id"]
    assert item["latest_run_id"] == second["id"]
    assert item["outputs_run_id"] == first["id"]
    assert item["outputs_current"] is False
    assert [output["file_id"] for output in item["latest_outputs"]] == ["f1"]
