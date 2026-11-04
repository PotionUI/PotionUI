import asyncio
import itertools

import pytest

from src.features.elixirs.brew import draw_files
from src.features.elixirs.coordinator import Coordinator
from src.features.elixirs.dto import ElixirDefinition
from src.features.elixirs.graph import validate_graph
from src.features.elixirs.limits import Limits
from src.features.elixirs.repository import ElixirRepository
from src.features.elixirs.routes import run_detail
from src.features.elixirs.run_repository import RunStore

KREA = "4TK1KBQZ2XMB8ME0PTMXS1YJQP"
FILES = ("p1", "p2", "p3", "p4", "p5", "p6")


def library(item_id):
    return {"kind": "library", "item_id": item_id, "media_type": "image", "original_filename": f"{item_id}.png"}


def edge(edge_id, source, source_handle, target, target_handle):
    return {"id": edge_id, "source": source, "source_handle": source_handle, "target": target, "target_handle": target_handle, "iteration": "each"}


def preset(node_id, mode, title):
    return {"id": node_id, "kind": "preset", "title": title, "preset_id": KREA, "mode": mode, "overrides": {}, "session_snapshot": {}}


def photos(**pick):
    return {"id": "photos", "kind": "media", "title": "Photos", "media_type": "image", "assets": [library(item) for item in FILES], **pick}


def template(mode, **form):
    return {
        "preset_id": KREA,
        "mode": mode,
        "prompts": [{"positive": "saved prompt", "negative": ""}],
        "form_data": {"seed": -1, "quantity": 1, **form},
    }


def save(definition, run_mode="automatic"):
    record = ElixirRepository().create("alice", "Pick test", run_mode, definition)
    return {"id": record.id, "revision": record.revision, "definition": record.definition, "run_mode": run_mode}


def two_steps(**pick):
    return {
        "nodes": [photos(**pick), preset("sharpen", "enhance", "Sharpen"), preset("polish", "enhance", "Polish")],
        "edges": [edge("e1", "photos", "images", "sharpen", "source_image"), edge("e2", "sharpen", "images", "polish", "source_image")],
    }


def sharpen_then_polish(**pick):
    return save(two_steps(**pick))


def describe_then_make(**pick):
    return save({
        "nodes": [
            photos(**pick),
            {"id": "writer", "kind": "llm", "title": "Describe", "instruction": "Describe it", "target_node_id": None, "pinned": {}},
            preset("make", "txt2img", "Make"),
        ],
        "edges": [edge("e1", "photos", "images", "writer", "images"), edge("e2", "writer", "text", "make", "prompt")],
    })


def templates_for(elixir):
    return {
        node["id"]: template(node["mode"], **({"source_image": "uploads/saved.png"} if node["mode"] == "enhance" else {}))
        for node in elixir["definition"]["nodes"]
        if node["kind"] == "preset"
    }


@pytest.fixture
def seeds():
    return itertools.count(7)


@pytest.fixture
def coordinator(scratch_db, port, seeds):
    for item_id in FILES:
        port.media[f"library:{item_id}"] = {"value": f"uploads/{item_id}.png", "media_type": "image", "original_filename": f"{item_id}.png", "origin": None}
    return Coordinator(store=RunStore(), port=port, limits_provider=Limits, boot_id="boot-1", random_below=lambda limit: next(seeds))


def brew(coordinator, alice, elixir, contracts, key="brew-1"):
    return coordinator.start_graph_run(alice, elixir, contracts, templates_for(elixir), elixir["run_mode"], key)


def finish_everything(coordinator, port):
    for _ in range(4):
        asyncio.run(coordinator.pump())
        for generation_id, generation in list(port.generations.items()):
            if generation["status"] in ("pending", "running"):
                port.media[f"history:{generation_id}:f-{generation_id}"] = {"value": f"generations/{generation_id}.png", "media_type": "image", "original_filename": "out.png", "origin": None}
                port.finish(generation_id, (f"f-{generation_id}", "IMAGE"))


def drawn(run):
    return [entry["item_id"] for entry in run["node_states"]["photos"]["manifest"]["images"]]


def test_a_files_step_keeps_all_files_unless_told_otherwise():
    node = ElixirDefinition.model_validate({"nodes": [photos()], "edges": []}).nodes[0]

    assert (node.pick, node.pick_count) == ("all", 1)


def test_all_files_feed_every_brew_without_a_draw(coordinator, port, alice, contracts):
    run = brew(coordinator, alice, sharpen_then_polish(), contracts)

    assert drawn(run) == list(FILES)
    assert "draw" not in run["node_states"]["photos"]
    assert len(run["checkpoints"]["e1"]["confirmed"]) == 6


def test_a_random_pick_draws_that_many_distinct_files_with_a_recorded_seed(coordinator, port, alice, contracts):
    run = brew(coordinator, alice, sharpen_then_polish(pick="random", pick_count=2), contracts)
    asyncio.run(coordinator.pump())

    draw = run["node_states"]["photos"]["draw"]
    assert draw == {"seed": 7, "count": 2, "of": 6}
    picked = drawn(run)
    assert len(set(picked)) == 2 and set(picked) <= set(FILES)
    assets = [library(item) for item in FILES]
    assert [entry["item_id"] for entry in draw_files(assets, 2, draw["seed"])] == picked
    assert run["pinned_inputs"]["photos"] == run["node_states"]["photos"]["manifest"]["images"]
    assert run["checkpoints"]["e1"]["confirmed"] == [f"library:{item}" for item in picked]
    assert [submit["form_data"]["source_image"] for submit in port.submits] == [f"uploads/{item}.png" for item in picked]


def test_each_brew_draws_again(coordinator, port, alice, contracts):
    elixir = sharpen_then_polish(pick="random", pick_count=2)

    first = brew(coordinator, alice, elixir, contracts, key="brew-1")
    second = brew(coordinator, alice, elixir, contracts, key="brew-2")

    assert first["node_states"]["photos"]["draw"]["seed"] != second["node_states"]["photos"]["draw"]["seed"]
    assert drawn(first) != drawn(second)


def test_a_count_above_the_files_held_draws_them_all(coordinator, port, alice, contracts):
    run = brew(coordinator, alice, sharpen_then_polish(pick="random", pick_count=40), contracts)

    assert run["node_states"]["photos"]["draw"]["count"] == 6
    assert sorted(drawn(run)) == sorted(FILES)


def test_a_writer_looks_only_at_the_drawn_files(coordinator, port, alice, contracts):
    run = brew(coordinator, alice, describe_then_make(pick="random", pick_count=2), contracts)

    async def go():
        for _ in range(3):
            await coordinator.pump()
            await coordinator.wait_for_writers()

    asyncio.run(go())

    assert len(drawn(run)) == 2
    assert [write["media"][0]["value"] for write in port.writes] == [f"uploads/{item}.png" for item in drawn(run)]


def test_running_a_step_again_reuses_its_brews_draw(coordinator, port, alice, contracts):
    elixir = sharpen_then_polish(pick="random", pick_count=2)
    run = brew(coordinator, alice, elixir, contracts)
    finish_everything(coordinator, port)
    picked = drawn(run)
    submitted = len(port.submits)

    again = coordinator.start_node_run(alice, elixir, contracts, "sharpen", templates_for(elixir)["sharpen"], "node-key-1")
    asyncio.run(coordinator.pump())

    assert [entry["item_id"] for entry in again["pinned_inputs"]["selection"]] == picked
    assert [submit["form_data"]["source_image"] for submit in port.submits[submitted:]] == [f"uploads/{item}.png" for item in picked]
    detail = run_detail(coordinator, alice, again)
    assert detail["node_states"]["photos"]["draw"] == run["node_states"]["photos"]["draw"]


def test_estimates_count_the_drawn_files(catalog, contracts):
    report = validate_graph(two_steps(pick="random", pick_count=2), run_mode="automatic", catalog=catalog, contracts=contracts, limits=Limits())

    assert report["estimates"]["nodes"]["photos"]["outputs"] == 2
    assert report["estimates"]["edges"]["e1"] == 2
    assert report["estimates"]["total_invocations"] == 4


def test_a_drawn_number_within_the_limits_passes_where_all_files_would_not(catalog, contracts):
    tight = Limits(max_invocations=5)

    drawn_report = validate_graph(two_steps(pick="random", pick_count=2), run_mode="automatic", catalog=catalog, contracts=contracts, limits=tight)
    all_report = validate_graph(two_steps(), run_mode="automatic", catalog=catalog, contracts=contracts, limits=tight)

    assert drawn_report["valid"] is True
    assert "too_many_invocations" in [issue["code"] for issue in all_report["issues"]]
