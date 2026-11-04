import asyncio

import pytest

from src.features.elixirs.coordinator import BrewRefused, Coordinator
from src.features.elixirs.identity import invocation_key
from src.features.elixirs.limits import Limits
from src.features.elixirs.repository import ElixirRepository
from src.features.elixirs.run_repository import RunStore

KREA = "4TK1KBQZ2XMB8ME0PTMXS1YJQP"
H3 = "01KX47H3MINIMAX000000000VA"


def library(item_id):
    return {"kind": "library", "item_id": item_id, "media_type": "image", "original_filename": f"{item_id}.png"}


def edge(edge_id, source, source_handle, target, target_handle, **extra):
    return {"id": edge_id, "source": source, "source_handle": source_handle, "target": target, "target_handle": target_handle, "iteration": "each", **extra}


def preset(node_id, preset_id, mode, title):
    return {"id": node_id, "kind": "preset", "title": title, "preset_id": preset_id, "mode": mode, "overrides": {}, "session_snapshot": {}}


def director_doc():
    return {
        "schema_version": 1,
        "mode": "i2v",
        "settings": {"fps": 24, "duration": 5, "seed": -1},
        "segments": [{"id": "shot-1", "prompt": "orbit"}],
        "media": [],
        "audio": [],
        "ic_lora": [],
    }


def template(preset_id, mode, **form):
    return {
        "preset_id": preset_id,
        "mode": mode,
        "prompts": [{"positive": f"{mode} prompt", "negative": ""}],
        "form_data": {"seed": -1, "quantity": 1, **form},
        "tab_id": "node-tab",
        "idempotency_key": "client-key",
    }


@pytest.fixture
def coordinator(scratch_db, port):
    return Coordinator(store=RunStore(), port=port, limits_provider=Limits, boot_id="boot-1", random_below=lambda limit: 7)


def saved(definition):
    record = ElixirRepository().create("alice", "Brew test", "review", definition)
    return {"id": record.id, "revision": record.revision, "definition": record.definition}


def media_to_enhance(assets=("p1", "p2")):
    definition = {
        "nodes": [
            {"id": "photos", "kind": "media", "title": "Photos", "media_type": "image", "assets": [library(item) for item in assets]},
            preset("sharpen", KREA, "enhance", "Sharpen"),
        ],
        "edges": [edge("e1", "photos", "images", "sharpen", "source_image")],
    }
    return saved(definition)


def photo_to_video():
    definition = {
        "nodes": [preset("photo", KREA, "txt2img", "Product photo"), preset("orbit", H3, "video", "Orbit left")],
        "edges": [edge("e1", "photo", "images", "orbit", "first_frame")],
    }
    return saved(definition)


def templates_for(elixir):
    out = {}
    for node in elixir["definition"]["nodes"]:
        if node["kind"] != "preset":
            continue
        if node["mode"] == "enhance":
            out[node["id"]] = template(KREA, "enhance", source_image="uploads/saved.png", source_image__origin={"generation_id": "old"})
        elif node["mode"] == "video":
            out[node["id"]] = template(H3, "video", video_director=director_doc())
        else:
            out[node["id"]] = template(node["preset_id"], node["mode"])
    return out


def start(coordinator, user, elixir, contracts, run_mode="automatic", key="brew-1"):
    return coordinator.start_graph_run(user, elixir, contracts, templates_for(elixir), run_mode, key)


def invocations(coordinator, run):
    return coordinator.store.invocations(run["id"])


def test_an_automatic_brew_sends_every_file_into_the_next_preset(coordinator, port, alice, contracts):
    run = start(coordinator, alice, media_to_enhance(), contracts)

    assert run["status"] == "running"
    assert run["checkpoints"]["e1"]["confirmed"] == ["library:p1", "library:p2"]
    asyncio.run(coordinator.pump())

    assert len(port.submits) == 2
    first = port.submits[0]
    assert first["form_data"]["source_image"] == "uploads/p1.png"
    assert "source_image__origin" not in first["form_data"]
    assert first["form_data"]["seed"] == 7
    assert first["tab_id"] is None
    assert first["idempotency_key"] == invocation_key(run["id"], "sharpen", "e1", "library:p1", 1)
    assert port.submits[1]["form_data"]["source_image"] == "uploads/p2.png"

    port.finish("g1", ("f1", "IMAGE"))
    port.finish("g2", ("f2", "IMAGE"))
    asyncio.run(coordinator.pump())

    done = coordinator.store.get_run(run["id"])
    assert done["status"] == "completed"
    assert done["finished_at"]
    assert done["node_states"]["sharpen"]["status"] == "done"
    assert [entry["file_id"] for entry in done["node_states"]["sharpen"]["manifest"]["images"]] == ["f1", "f2"]
    stored = invocations(coordinator, run)
    assert {item["effective_seed"] for item in stored} == {7}
    assert {item["submitted_boot"] for item in stored} == {"boot-1"}


def test_a_generated_photo_becomes_the_first_frame_of_a_video(coordinator, port, alice, contracts):
    port.media["history:g1:f1"] = {"value": "generations/g1/f1.png", "media_type": "IMAGE", "original_filename": "f1.png", "origin": {"generation_id": "g1", "file_index": 0}}
    run = start(coordinator, alice, photo_to_video(), contracts)

    asyncio.run(coordinator.pump())
    assert [submit["mode"] for submit in port.submits] == ["txt2img"]
    port.finish("g1", ("f1", "IMAGE"), ("f9", "VIDEO"))
    asyncio.run(coordinator.pump())

    video = port.submits[1]
    frames = video["form_data"]["video_director"]["media"]
    assert frames == [
        {
            "id": "bound-first-shot-1",
            "role": "first",
            "segment_id": "shot-1",
            "at": 0,
            "strength": 1.0,
            "media": {"path": "generations/g1/f1.png", "relative_path": "generations/g1/f1.png", "type": "image"},
        }
    ]
    child = next(item for item in invocations(coordinator, run) if item["node_id"] == "orbit")
    assert child["source_invocation_id"] and child["source_file_index"] == 0
    assert child["input_key"] == "history:g1:f1"


def test_choosing_outputs_between_presets_waits_and_sends_only_the_chosen_files(coordinator, port, alice, contracts):
    run = start(coordinator, alice, media_to_enhance(("p1", "p2", "p3")), contracts, run_mode="review")

    assert run["status"] == "awaiting_selection"
    assert invocations(coordinator, run) == []
    checkpoint = run["checkpoints"]["e1"]
    assert checkpoint["draft"] == ["library:p1", "library:p2", "library:p3"]

    coordinator.save_draft(alice, run["id"], "e1", ["library:p3"], expected_revision=1)
    confirmed = coordinator.confirm_edge(alice, run["id"], "e1", ["library:p3", "library:p1"], expected_revision=2)

    assert confirmed["checkpoints"]["e1"]["confirmed"] == ["library:p1", "library:p3"]
    assert [item["input_key"] for item in invocations(coordinator, run)] == ["library:p1", "library:p3"]
    again = coordinator.confirm_edge(alice, run["id"], "e1", ["library:p1", "library:p3"], expected_revision=99)
    assert again["checkpoints"]["e1"]["revision"] == 3
    assert len(invocations(coordinator, run)) == 2
    with pytest.raises(BrewRefused) as different:
        coordinator.confirm_edge(alice, run["id"], "e1", ["library:p2"], expected_revision=3)
    assert different.value.code == "already_confirmed"


@pytest.mark.parametrize(
    "selected, revision, code",
    [([], 1, "empty_selection"), (["library:p9"], 1, "unknown_files"), (["library:p1"], 7, "revision_conflict")],
)
def test_a_confirmation_that_does_not_fit_is_refused(coordinator, alice, contracts, selected, revision, code):
    run = start(coordinator, alice, media_to_enhance(), contracts, run_mode="review")

    with pytest.raises(BrewRefused) as refused:
        coordinator.confirm_edge(alice, run["id"], "e1", selected, expected_revision=revision)

    assert refused.value.code == code


def test_another_users_brew_cannot_be_continued(coordinator, port, alice, contracts):
    run = start(coordinator, alice, media_to_enhance(), contracts, run_mode="review")
    port.users["bob"] = port.make_user("bob")

    with pytest.raises(BrewRefused) as refused:
        coordinator.confirm_edge(port.users["bob"], run["id"], "e1", ["library:p1"], expected_revision=1)

    assert refused.value.status == 404


def test_pumping_again_never_submits_twice(coordinator, port, alice, contracts):
    start(coordinator, alice, media_to_enhance(), contracts)

    asyncio.run(coordinator.pump())
    asyncio.run(coordinator.pump())
    asyncio.run(coordinator.pump())

    assert len(port.submits) == 2


def test_two_coordinators_racing_claim_each_invocation_once(coordinator, port, alice, contracts):
    start(coordinator, alice, media_to_enhance(("p1", "p2", "p3")), contracts)
    other = Coordinator(store=RunStore(), port=port, limits_provider=Limits, boot_id="boot-2", random_below=lambda limit: 9)

    async def race():
        await asyncio.gather(coordinator.pump(), other.pump(), coordinator.pump())

    asyncio.run(race())

    keys = [submit["idempotency_key"] for submit in port.submits]
    assert len(keys) == 3 and len(set(keys)) == 3


def test_starting_the_same_brew_twice_returns_the_first(coordinator, port, alice, contracts):
    elixir = media_to_enhance()
    first = start(coordinator, alice, elixir, contracts, key="same")
    second = start(coordinator, alice, elixir, contracts, key="same")

    assert second["id"] == first["id"]
    with pytest.raises(BrewRefused) as refused:
        start(coordinator, alice, photo_to_video(), contracts, key="same")
    assert refused.value.code == "idempotency_conflict"


def test_a_missing_media_file_stops_the_brew_before_anything_runs(coordinator, port, alice, contracts):
    del port.media["library:p2"]

    with pytest.raises(BrewRefused) as refused:
        start(coordinator, alice, media_to_enhance(), contracts)

    assert refused.value.code == "unavailable_media"
    assert refused.value.node_id == "photos"
    assert "file 2" in refused.value.message
    assert port.submits == []


def test_a_file_deleted_after_confirmation_fails_only_its_invocation(coordinator, port, alice, contracts):
    run = start(coordinator, alice, media_to_enhance(), contracts)
    del port.media["library:p2"]

    asyncio.run(coordinator.pump())

    stored = {item["input_key"]: item for item in invocations(coordinator, run)}
    assert stored["library:p1"]["status"] == "submitted"
    assert stored["library:p2"]["status"] == "failed"
    assert "no longer available" in stored["library:p2"]["error"]
    assert len(port.submits) == 1


def test_a_failed_generation_needs_attention_and_retry_reuses_the_frozen_request(coordinator, port, alice, contracts):
    run = start(coordinator, alice, media_to_enhance(("p1",)), contracts)
    asyncio.run(coordinator.pump())
    port.fail("g1")
    asyncio.run(coordinator.pump())

    failed = coordinator.store.get_run(run["id"])
    assert failed["status"] == "needs_attention"
    assert failed["node_states"]["sharpen"]["status"] == "failed"
    assert invocations(coordinator, run)[0]["error"] == "CUDA out of memory"

    coordinator.random_below = lambda limit: 11
    coordinator.retry_failed(alice, run["id"], "sharpen")
    asyncio.run(coordinator.pump())

    retry = port.submits[1]
    assert retry["form_data"]["seed"] == 7
    assert retry["form_data"] == port.submits[0]["form_data"]
    assert retry["idempotency_key"] == invocation_key(run["id"], "sharpen", "e1", "library:p1", 2)
    port.finish("g2", ("f1", "IMAGE"))
    asyncio.run(coordinator.pump())
    assert coordinator.store.get_run(run["id"])["status"] == "completed"


def test_a_failed_submit_is_recorded_on_the_invocation(coordinator, port, alice, contracts, caplog):
    port.fail_submit = "The preset form rejected the request"
    run = start(coordinator, alice, media_to_enhance(("p1",)), contracts)

    asyncio.run(coordinator.pump())

    assert invocations(coordinator, run)[0]["error"] == "The generation could not be started."
    assert "The preset form rejected the request" in caplog.text
    assert coordinator.store.get_run(run["id"])["status"] == "needs_attention"


def test_a_restart_marks_lost_work_interrupted_and_resubmits_unsent_claims(coordinator, port, alice, contracts):
    run = start(coordinator, alice, media_to_enhance(("p1", "p2")), contracts)
    asyncio.run(coordinator.pump())
    port.fail("g1", "Generation interrupted by a restart")
    second = invocations(coordinator, run)[1]
    coordinator.store.update_invocation(second["id"], {"status": "claimed", "generation_id": None})
    port.by_key.pop(second["idempotency_key"], None)

    restarted = Coordinator(store=RunStore(), port=port, limits_provider=Limits, boot_id="boot-2", random_below=lambda limit: 9)
    asyncio.run(restarted.recover())
    asyncio.run(restarted.pump())

    stored = {item["input_key"]: item for item in invocations(coordinator, run)}
    assert stored["library:p1"]["status"] == "interrupted"
    assert stored["library:p2"]["status"] == "submitted"
    assert stored["library:p2"]["submitted_boot"] == "boot-2"
    assert port.submits[-1]["idempotency_key"] == second["idempotency_key"]
    assert port.submits[-1]["form_data"]["seed"] == 7
    assert coordinator.store.get_run(run["id"])["status"] == "running"


def test_work_from_the_current_boot_is_not_called_interrupted(coordinator, port, alice, contracts):
    run = start(coordinator, alice, media_to_enhance(("p1",)), contracts)
    asyncio.run(coordinator.pump())
    port.fail("g1")

    asyncio.run(coordinator.recover())
    asyncio.run(coordinator.pump())

    assert invocations(coordinator, run)[0]["status"] == "failed"


def test_cancelling_stops_new_work_and_keeps_finished_files(coordinator, port, alice, contracts):
    port.media["history:g1:f1"] = {"value": "generations/g1/f1.png", "media_type": "image", "original_filename": "f1.png", "origin": None}
    run = start(coordinator, alice, photo_to_video(), contracts)
    asyncio.run(coordinator.pump())

    coordinator.request_cancel(alice, run["id"])
    port.finish("g1", ("f1", "IMAGE"))
    asyncio.run(coordinator.pump())

    cancelled = coordinator.store.get_run(run["id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancel_requested_at"]
    assert [item["node_id"] for item in invocations(coordinator, run)] == ["photo"]
    assert invocations(coordinator, run)[0]["outputs"][0]["file_id"] == "f1"
    assert len(port.submits) == 1


def test_cancelling_cancels_running_generations(coordinator, port, alice, contracts):
    run = start(coordinator, alice, media_to_enhance(("p1", "p2")), contracts)
    asyncio.run(coordinator.pump())

    coordinator.request_cancel(alice, run["id"])
    asyncio.run(coordinator.pump())
    asyncio.run(coordinator.pump())

    assert sorted(port.cancels) == ["g1", "g2"]
    assert {item["status"] for item in invocations(coordinator, run)} == {"cancelled"}
    assert coordinator.store.get_run(run["id"])["status"] == "cancelled"
    with pytest.raises(BrewRefused):
        coordinator.retry_failed(alice, run["id"], "sharpen")


def test_a_node_without_matching_outputs_blocks_its_branch(coordinator, port, alice, contracts):
    run = start(coordinator, alice, photo_to_video(), contracts)
    asyncio.run(coordinator.pump())
    port.finish("g1", ("v1", "VIDEO"))
    asyncio.run(coordinator.pump())

    blocked = coordinator.store.get_run(run["id"])
    assert blocked["node_states"]["photo"]["status"] == "no_output"
    assert blocked["status"] == "needs_attention"
    assert len(port.submits) == 1


def test_too_many_files_for_one_connection_wait_for_a_smaller_choice(coordinator, port, alice, contracts):
    coordinator.limits_provider = lambda: Limits(max_outputs_per_connection=2)

    run = start(coordinator, alice, media_to_enhance(("p1", "p2", "p3")), contracts)

    checkpoint = run["checkpoints"]["e1"]
    assert checkpoint["confirmed"] is None and "at most 2" in checkpoint["error"]
    assert run["status"] == "needs_attention"
    with pytest.raises(BrewRefused) as refused:
        coordinator.confirm_edge(alice, run["id"], "e1", ["library:p1", "library:p2", "library:p3"], expected_revision=1)
    assert refused.value.code == "too_many_files"
    coordinator.confirm_edge(alice, run["id"], "e1", ["library:p1", "library:p2"], expected_revision=1)
    assert len(invocations(coordinator, run)) == 2


def test_templates_must_match_their_nodes(coordinator, alice, contracts):
    elixir = media_to_enhance()
    templates = templates_for(elixir)
    templates["sharpen"]["mode"] = "txt2img"

    with pytest.raises(BrewRefused) as refused:
        coordinator.start_graph_run(alice, elixir, contracts, templates, "automatic", "k")

    assert refused.value.code == "template_mismatch"
    assert refused.value.node_id == "sharpen"


def test_a_deleted_generation_fails_its_invocation(coordinator, port, alice, contracts):
    run = start(coordinator, alice, media_to_enhance(("p1",)), contracts)
    asyncio.run(coordinator.pump())
    del port.generations["g1"]

    asyncio.run(coordinator.pump())

    assert invocations(coordinator, run)[0]["error"] == "The generation was deleted."


def test_a_confirmation_racing_a_cancel_starts_nothing(coordinator, port, alice, contracts):
    run = start(coordinator, alice, media_to_enhance(), contracts, run_mode="review")
    with coordinator.store.transaction() as cursor:
        stored = coordinator.store.load_run(cursor, run["id"])
        stored["checkpoints"]["e1"]["confirmed"] = ["library:p1"]
        coordinator.store.save_run(cursor, run["id"], {"checkpoints": stored["checkpoints"]})

    coordinator.request_cancel(alice, run["id"])
    asyncio.run(coordinator.pump())

    assert invocations(coordinator, run) == []
    assert port.submits == []
    assert coordinator.store.get_run(run["id"])["status"] == "cancelled"


def three_step():
    definition = {
        "nodes": [
            {"id": "photos", "kind": "media", "title": "Photos", "media_type": "image", "assets": [library("p1")]},
            preset("sharpen", KREA, "enhance", "Sharpen"),
            preset("polish", KREA, "enhance", "Polish"),
        ],
        "edges": [edge("e1", "photos", "images", "sharpen", "source_image"), edge("e2", "sharpen", "images", "polish", "source_image")],
    }
    record = ElixirRepository().create("alice", "Three steps", "automatic", definition)
    return {"id": record.id, "revision": record.revision, "definition": record.definition, "run_mode": "automatic"}


def brew_through(coordinator, port, alice, contracts, elixir):
    run = start(coordinator, alice, elixir, contracts)
    asyncio.run(coordinator.pump())
    port.media["history:g1:f1"] = {"value": "generations/g1/f1.png", "media_type": "image", "original_filename": "f1.png", "origin": {"generation_id": "g1", "file_index": 0}}
    port.finish("g1", ("f1", "IMAGE"))
    asyncio.run(coordinator.pump())
    port.finish("g2", ("f2", "IMAGE"))
    asyncio.run(coordinator.pump())
    return coordinator.store.get_run(run["id"])


def results_of(elixir):
    return ElixirRepository().get(elixir["id"], "alice").results


def test_a_brew_records_each_node_result_on_the_elixir(coordinator, port, alice, contracts):
    elixir = three_step()

    run = brew_through(coordinator, port, alice, contracts, elixir)

    assert run["status"] == "completed"
    results = results_of(elixir)
    assert {node: entry["run_id"] for node, entry in results.items()} == {"sharpen": run["id"], "polish": run["id"]}
    assert results["polish"]["manifest"]["images"][0]["file_id"] == "f2"
    assert not any(entry["stale"] for entry in results.values())


def test_running_one_node_again_reuses_its_chosen_inputs_and_marks_what_follows_stale(coordinator, port, alice, contracts):
    elixir = three_step()
    graph_run = brew_through(coordinator, port, alice, contracts, elixir)

    node_run = coordinator.start_node_run(alice, elixir, contracts, "sharpen", templates_for(elixir)["sharpen"], "node-key-1")
    asyncio.run(coordinator.pump())

    assert node_run["scope"] == "node" and node_run["base_run_id"] == graph_run["id"]
    rerun = port.submits[-1]
    assert rerun["form_data"]["source_image"] == "uploads/p1.png"
    assert len(port.submits) == 3
    port.finish("g3", ("f3", "IMAGE"))
    asyncio.run(coordinator.pump())

    assert coordinator.store.get_run(node_run["id"])["status"] == "completed"
    results = results_of(elixir)
    assert results["sharpen"]["run_id"] == node_run["id"] and results["sharpen"]["stale"] is False
    assert results["polish"]["stale"] is True
    with pytest.raises(BrewRefused) as refused:
        coordinator.start_node_run(alice, elixir, contracts, "polish", templates_for(elixir)["polish"], "node-key-2")
    assert refused.value.code == "inputs_changed"
    assert "Sharpen has newer outputs" in refused.value.message


def test_running_one_node_again_in_choose_mode_waits_for_a_choice_that_feeds_the_next_step(coordinator, port, alice, contracts):
    elixir = three_step()
    elixir["run_mode"] = "review"
    run = start(coordinator, alice, elixir, contracts, run_mode="review")
    coordinator.confirm_edge(alice, run["id"], "e1", ["library:p1"], expected_revision=1)
    asyncio.run(coordinator.pump())
    port.media["history:g1:f1"] = {"value": "generations/g1/f1.png", "media_type": "image", "original_filename": "f1.png", "origin": {"generation_id": "g1", "file_index": 0}}
    port.finish("g1", ("f1", "IMAGE"))
    asyncio.run(coordinator.pump())
    coordinator.confirm_edge(alice, run["id"], "e2", ["history:g1:f1"], expected_revision=1)
    asyncio.run(coordinator.pump())
    port.finish("g2", ("f2", "IMAGE"))
    asyncio.run(coordinator.pump())

    node_run = coordinator.start_node_run(alice, elixir, contracts, "sharpen", templates_for(elixir)["sharpen"], "node-key-choose")
    asyncio.run(coordinator.pump())
    port.finish("g3", ("f3", "IMAGE"))
    asyncio.run(coordinator.pump())

    waiting = coordinator.store.get_run(node_run["id"])
    assert waiting["status"] == "awaiting_selection"
    checkpoint = waiting["checkpoints"]["e2"]
    assert checkpoint["confirmed"] is None
    assert [entry["file_id"] for entry in checkpoint["eligible"]] == ["f3"]
    assert any(edge["id"] == "e2" for edge in waiting["graph"]["edges"])

    port.media["history:g3:f3"] = {"value": "generations/g3/f3.png", "media_type": "image", "original_filename": "f3.png", "origin": {"generation_id": "g3", "file_index": 0}}
    done = coordinator.confirm_edge(alice, node_run["id"], "e2", ["history:g3:f3"], expected_revision=checkpoint["revision"])

    assert done["status"] == "completed"
    assert invocations(coordinator, done)[-1]["node_id"] == "sharpen"
    assert len(port.submits) == 3
    coordinator.start_node_run(alice, elixir, contracts, "polish", templates_for(elixir)["polish"], "node-key-polish")
    asyncio.run(coordinator.pump())
    assert port.submits[-1]["form_data"]["source_image"] == "generations/g3/f3.png"


def test_a_node_cannot_run_while_connected_work_is_generating(coordinator, port, alice, contracts):
    elixir = three_step()
    start(coordinator, alice, elixir, contracts)
    asyncio.run(coordinator.pump())

    with pytest.raises(BrewRefused) as refused:
        coordinator.start_node_run(alice, elixir, contracts, "polish", templates_for(elixir)["polish"], "node-key-3")

    assert refused.value.code == "related_work_running"
    assert refused.value.status == 409


def test_a_node_without_chosen_inputs_cannot_run_alone(coordinator, alice, contracts):
    elixir = three_step()

    with pytest.raises(BrewRefused) as refused:
        coordinator.start_node_run(alice, elixir, contracts, "sharpen", templates_for(elixir)["sharpen"], "node-key-4")

    assert refused.value.code == "no_inputs"


def test_a_root_preset_runs_alone_with_a_fresh_seed(coordinator, port, alice, contracts):
    elixir = photo_to_video()
    elixir["run_mode"] = "review"
    coordinator.random_below = lambda limit: 21

    run = coordinator.start_node_run(alice, elixir, contracts, "photo", templates_for(elixir)["photo"], "node-key-5")
    again = coordinator.start_node_run(alice, elixir, contracts, "photo", templates_for(elixir)["photo"], "node-key-5")
    asyncio.run(coordinator.pump())

    assert again["id"] == run["id"]
    assert [submit["form_data"]["seed"] for submit in port.submits] == [21]
    with pytest.raises(BrewRefused) as media:
        coordinator.start_node_run(alice, media_to_enhance(), contracts, "photos", {}, "node-key-6")
    assert media.value.code == "not_a_preset_node"


def test_files_a_brew_still_needs_cannot_be_deleted(coordinator, port, alice, contracts):
    from types import SimpleNamespace

    from src.features.elixirs.runtime import ElixirRuntime

    hooks = ElixirRuntime(coordinator, follows=None)
    elixir = photo_to_video()
    run = coordinator.start_graph_run(alice, elixir, contracts, templates_for(elixir), "review", "hold-key")
    asyncio.run(coordinator.pump())
    port.finish("g1", ("f1", "IMAGE"))
    asyncio.run(coordinator.pump())
    single = SimpleNamespace(data={"generation_id": "g1"})
    hooks.on_before_delete(single)
    bulk = SimpleNamespace(data={"generation_ids": ["g1", "other"]})
    hooks.on_before_bulk_delete(bulk)
    free = SimpleNamespace(data={"generation_id": "other"})
    hooks.on_before_delete(free)
    assert single.data["blocked"] is True and "still needs files" in single.data["block_reason"]
    assert bulk.data["blocked"] is True and "(1 of the selected generations)" in bulk.data["block_reason"]
    assert "blocked" not in free.data
    coordinator.request_cancel(alice, run["id"])
    asyncio.run(coordinator.pump())
    released = SimpleNamespace(data={"generation_id": "g1"})
    hooks.on_before_delete(released)
    assert "blocked" not in released.data


def test_a_cancel_that_raises_is_sent_again_on_the_next_pass(coordinator, port, alice, contracts, caplog):
    run = start(coordinator, alice, media_to_enhance(("p1",)), contracts)
    asyncio.run(coordinator.pump())
    real_cancel = port.cancel
    calls = []

    async def flaky_cancel(user, generation_id):
        calls.append(generation_id)
        if len(calls) == 1:
            raise RuntimeError("queue is busy")
        return await real_cancel(user, generation_id)

    port.cancel = flaky_cancel
    coordinator.request_cancel(alice, run["id"])
    asyncio.run(coordinator.pump())

    assert invocations(coordinator, run)[0]["status"] == "submitted"
    assert "could not be stopped" in caplog.text
    asyncio.run(coordinator.pump())

    assert calls == ["g1", "g1"]
    assert invocations(coordinator, run)[0]["status"] == "cancelled"
    assert coordinator.store.get_run(run["id"])["status"] == "cancelled"
    assert coordinator._cancel_sent == {"g1"}
    asyncio.run(coordinator.pump())
    assert coordinator._cancel_sent == set()


def test_a_cancel_the_queue_did_not_take_is_sent_again(coordinator, port, alice, contracts):
    run = start(coordinator, alice, media_to_enhance(("p1",)), contracts)
    asyncio.run(coordinator.pump())
    answers = [False, True]
    real_cancel = port.cancel

    async def refusing_cancel(user, generation_id):
        if answers.pop(0):
            return await real_cancel(user, generation_id)
        return False

    port.cancel = refusing_cancel
    coordinator.request_cancel(alice, run["id"])
    asyncio.run(coordinator.pump())
    assert invocations(coordinator, run)[0]["status"] == "submitted"
    asyncio.run(coordinator.pump())

    assert answers == []
    assert invocations(coordinator, run)[0]["status"] == "cancelled"


def test_an_error_while_preparing_a_request_fails_only_that_invocation(coordinator, port, alice, contracts, caplog):
    run = start(coordinator, alice, media_to_enhance(("p1", "p2")), contracts)
    real_resolve = port.resolve

    def broken_resolve(user, ref):
        if ref.get("item_id") == "p2":
            raise RuntimeError("disk unplugged")
        return real_resolve(user, ref)

    port.resolve = broken_resolve
    asyncio.run(coordinator.pump())

    stored = {item["input_key"]: item for item in invocations(coordinator, run)}
    assert stored["library:p1"]["status"] == "submitted"
    assert stored["library:p2"]["status"] == "failed"
    assert stored["library:p2"]["error"] == "The generation could not be prepared."
    assert "disk unplugged" in caplog.text


def test_an_owner_who_is_gone_fails_the_claimed_invocation(coordinator, port, alice, contracts):
    run = start(coordinator, alice, media_to_enhance(("p1",)), contracts)
    del port.users["alice"]

    asyncio.run(coordinator.pump())

    stored = invocations(coordinator, run)[0]
    assert stored["status"] == "failed"
    assert stored["error"] == "The owner of this brew can no longer run it."
    assert port.submits == []


def test_one_broken_generation_does_not_hold_up_other_brews(coordinator, port, alice, contracts, caplog):
    stuck = start(coordinator, alice, media_to_enhance(("p1",)), contracts, key="stuck")
    asyncio.run(coordinator.pump())
    healthy = start(coordinator, alice, media_to_enhance(("p2",)), contracts, key="healthy")
    asyncio.run(coordinator.pump())
    real_state = port.state

    def broken_state(user, generation_id):
        if generation_id == "g1":
            raise RuntimeError("status store unavailable")
        return real_state(user, generation_id)

    port.state = broken_state
    port.finish("g2", ("f2", "IMAGE"))
    asyncio.run(coordinator.pump())

    assert coordinator.store.get_run(healthy["id"])["status"] == "completed"
    assert invocations(coordinator, stuck)[0]["status"] == "submitted"
    assert "could not be checked" in caplog.text


def test_cancelling_also_drops_claimed_work(coordinator, port, alice, contracts):
    run = start(coordinator, alice, media_to_enhance(("p1",)), contracts)
    item = invocations(coordinator, run)[0]
    coordinator.store.update_invocation(item["id"], {"status": "claimed"})

    coordinator.request_cancel(alice, run["id"])

    assert invocations(coordinator, run)[0]["status"] == "cancelled"
    assert coordinator.store.get_run(run["id"])["status"] == "cancelled"


def test_a_generation_started_after_its_brew_stopped_is_stopped_too(coordinator, port, alice, contracts):
    run = start(coordinator, alice, media_to_enhance(("p1",)), contracts)
    real_submit = port.submit

    async def submit_then_cancel(user, request):
        submitted = await real_submit(user, request)
        coordinator.request_cancel(alice, run["id"])
        return submitted

    port.submit = submit_then_cancel
    asyncio.run(coordinator.pump())

    stored = invocations(coordinator, run)[0]
    assert stored["status"] == "cancelled"
    assert stored["generation_id"] == "g1"
    assert port.cancels == ["g1"]
    assert port.generations["g1"]["status"] == "cancelled"


def test_a_restart_drops_pending_and_claimed_work_of_a_stopping_brew(coordinator, port, alice, contracts):
    run = start(coordinator, alice, media_to_enhance(("p1", "p2", "p3")), contracts)
    first, second, third = invocations(coordinator, run)
    coordinator.store.update_invocation(first["id"], {"status": "claimed"})
    with coordinator.store.transaction() as cursor:
        coordinator.store.save_run(cursor, run["id"], {"cancel_requested_at": "2026-10-01T00:00:00Z", "status": "cancelling"})
    other = start(coordinator, alice, media_to_enhance(("p1",)), contracts, key="other")
    kept = invocations(coordinator, other)[0]
    coordinator.store.update_invocation(kept["id"], {"status": "claimed"})

    restarted = Coordinator(store=RunStore(), port=port, limits_provider=Limits, boot_id="boot-2", random_below=lambda limit: 9)
    asyncio.run(restarted.recover())

    assert {item["status"] for item in invocations(coordinator, run)} == {"cancelled"}
    assert invocations(coordinator, other)[0]["status"] == "pending"
    asyncio.run(restarted.pump())
    assert coordinator.store.get_run(run["id"])["status"] == "cancelled"
    assert [submit["idempotency_key"] for submit in port.submits] == [kept["idempotency_key"]]


def test_pending_work_of_a_stopping_brew_is_never_submitted(coordinator, port, alice, contracts):
    run = start(coordinator, alice, media_to_enhance(("p1",)), contracts)
    with coordinator.store.transaction() as cursor:
        coordinator.store.save_run(cursor, run["id"], {"cancel_requested_at": "2026-10-01T00:00:00Z", "status": "cancelling"})

    asyncio.run(coordinator.pump())

    assert port.submits == []
    assert invocations(coordinator, run)[0]["status"] == "cancelled"
    assert coordinator.store.get_run(run["id"])["status"] == "cancelled"


def test_too_many_generations_are_refused_when_continuing(coordinator, alice, contracts):
    coordinator.limits_provider = lambda: Limits(max_invocations=2)
    run = start(coordinator, alice, media_to_enhance(("p1", "p2", "p3")), contracts, run_mode="review")

    with pytest.raises(BrewRefused) as refused:
        coordinator.confirm_edge(alice, run["id"], "e1", ["library:p1", "library:p2", "library:p3"], expected_revision=1)

    assert refused.value.code == "too_many_generations"
    assert invocations(coordinator, run) == []


def test_too_many_generations_are_refused_when_starting(coordinator, port, alice, contracts):
    coordinator.limits_provider = lambda: Limits(max_invocations=1)
    elixir = saved({
        "nodes": [preset("one", KREA, "txt2img", "One"), preset("two", KREA, "txt2img", "Two")],
        "edges": [],
    })

    with pytest.raises(BrewRefused) as refused:
        start(coordinator, alice, elixir, contracts)

    assert refused.value.code == "too_many_generations"
    assert coordinator.store.list_runs(elixir["id"], "alice") == []


def test_too_many_generations_are_refused_when_running_one_node(coordinator, port, alice, contracts):
    elixir = three_step()
    brew_through(coordinator, port, alice, contracts, elixir)
    coordinator.limits_provider = lambda: Limits(max_invocations=0)

    with pytest.raises(BrewRefused) as refused:
        coordinator.start_node_run(alice, elixir, contracts, "sharpen", templates_for(elixir)["sharpen"], "node-key-limit")

    assert refused.value.code == "too_many_generations"
    assert len(coordinator.store.list_runs(elixir["id"], "alice")) == 1


def test_retrying_past_the_generation_limit_is_refused(coordinator, port, alice, contracts):
    run = start(coordinator, alice, media_to_enhance(("p1", "p2")), contracts)
    asyncio.run(coordinator.pump())
    port.fail("g1")
    port.fail("g2")
    asyncio.run(coordinator.pump())
    coordinator.limits_provider = lambda: Limits(max_invocations=3)

    with pytest.raises(BrewRefused) as refused:
        coordinator.retry_failed(alice, run["id"], "sharpen")

    assert refused.value.code == "too_many_generations"
    assert len(invocations(coordinator, run)) == 2


def test_a_brew_waiting_for_a_choice_is_left_alone_by_the_pump(coordinator, port, alice, contracts):
    run = start(coordinator, alice, media_to_enhance(("p1",)), contracts, run_mode="review")
    before = coordinator.store.get_run(run["id"])["updated_at"]
    advanced = []
    real_advance = coordinator.advance

    def watching_advance(run_id):
        advanced.append(run_id)
        return real_advance(run_id)

    coordinator.advance = watching_advance
    asyncio.run(coordinator.pump())
    asyncio.run(coordinator.pump())

    assert advanced == []
    assert coordinator.store.get_run(run["id"])["updated_at"] == before


def test_moving_a_brew_on_without_changes_writes_nothing(coordinator, port, alice, contracts):
    run = start(coordinator, alice, media_to_enhance(("p1",)), contracts)
    asyncio.run(coordinator.pump())
    saves = []
    real_save = coordinator.store.save_run
    coordinator.store.save_run = lambda cursor, run_id, fields: saves.append(fields) or real_save(cursor, run_id, fields)

    coordinator.advance(run["id"])
    port.finish("g1", ("f1", "IMAGE"))
    asyncio.run(coordinator.pump())

    assert [fields["status"] for fields in saves] == ["completed"]


def test_a_failed_move_on_is_retried_by_the_next_pump(coordinator, port, alice, contracts):
    run = start(coordinator, alice, media_to_enhance(("p1",)), contracts, run_mode="review")
    real_advance = coordinator.advance
    failures = [RuntimeError("database is locked")]

    def flaky_advance(run_id):
        if failures:
            raise failures.pop()
        return real_advance(run_id)

    coordinator.advance = flaky_advance
    with pytest.raises(RuntimeError):
        coordinator.confirm_edge(alice, run["id"], "e1", ["library:p1"], expected_revision=1)
    assert invocations(coordinator, run) == []

    asyncio.run(coordinator.pump())

    assert len(port.submits) == 1
    assert coordinator.store.get_run(run["id"])["status"] == "running"


def video_template(doc):
    return {**template(H3, "video", video_director=doc)}


def media_to_video(doc, handle="first_frame"):
    elixir = saved({
        "nodes": [
            {"id": "photos", "kind": "media", "title": "Photos", "media_type": "image", "assets": [library("p1")]},
            preset("orbit", H3, "video", "Orbit left"),
        ],
        "edges": [edge("e1", "photos", "images", "orbit", handle)],
    })
    return elixir, {"orbit": video_template(doc)}


def test_a_text_only_video_session_starts_from_the_connected_image(coordinator, port, alice, contracts):
    doc = {**director_doc(), "mode": "t2v"}
    elixir, templates = media_to_video(doc)

    coordinator.start_graph_run(alice, elixir, contracts, templates, "automatic", "t2v-key")
    asyncio.run(coordinator.pump())

    sent = port.submits[0]["form_data"]["video_director"]
    assert sent["mode"] == "i2v"
    assert [entry["role"] for entry in sent["media"]] == ["first"]
    assert sent["media"][0]["media"]["path"] == "uploads/p1.png"
    assert templates["orbit"]["form_data"]["video_director"]["mode"] == "t2v"


def test_a_multi_shot_video_session_is_refused_before_anything_runs(coordinator, port, alice, contracts):
    doc = {**director_doc(), "mode": "director", "segments": [{"id": "a", "prompt": "a"}, {"id": "b", "prompt": "b"}, {"id": "c", "prompt": "c"}]}
    elixir, templates = media_to_video(doc)

    with pytest.raises(BrewRefused) as refused:
        coordinator.start_graph_run(alice, elixir, contracts, templates, "automatic", "multi-key")

    assert refused.value.code == "director_unsupported"
    assert refused.value.node_id == "orbit"
    assert refused.value.message == "Orbit left's session has 3 shots. Elixirs can feed an image into a single-shot session only."
    assert "t2v" not in refused.value.message and "director" not in refused.value.message
    assert coordinator.store.list_runs(elixir["id"], "alice") == []
    assert port.submits == []


def test_a_last_frame_without_a_first_frame_is_refused_before_anything_runs(coordinator, alice, contracts):
    elixir, templates = media_to_video({**director_doc(), "mode": "t2v"}, handle="last_frame")

    with pytest.raises(BrewRefused) as refused:
        coordinator.start_graph_run(alice, elixir, contracts, templates, "automatic", "last-key")

    assert refused.value.message == "Orbit left's session has no first frame. A last frame needs one, so add a first frame in the session."


def test_a_refused_frame_shows_a_plain_message_and_logs_the_details(coordinator, port, alice, contracts, caplog):
    elixir, templates = media_to_video({**director_doc(), "mode": "i2v", "media": []}, handle="last_frame")

    run = coordinator.start_graph_run(alice, elixir, contracts, templates, "automatic", "plain-key")
    asyncio.run(coordinator.pump())

    stored = invocations(coordinator, run)[0]
    assert stored["status"] == "failed"
    assert stored["error"] == "This session has no first frame. A last frame needs one, so add a first frame in the session."
    assert "first_frame_required" in caplog.text
    assert port.submits == []


def test_a_multi_item_field_gets_a_list_like_generate_sends(coordinator, port, alice, contracts, catalog):
    port.media["history:g1:f1"] = {"value": "generations/g1/f1.png", "media_type": "IMAGE", "original_filename": "f1.png", "origin": {"generation_id": "g1", "file_index": 0}}
    for form in catalog[KREA]["modes"]["enhance"]["forms"].values():
        form["source_image"]["multi"] = True
    elixir = media_to_enhance(("p1",))
    templates = templates_for(elixir)

    run = coordinator.start_graph_run(alice, elixir, contracts, templates, "automatic", "multi-key", catalog=catalog)
    asyncio.run(coordinator.pump())

    form = port.submits[0]["form_data"]
    assert form["source_image"] == [{"path": "uploads/p1.png", "relative_path": "uploads/p1.png", "name": "p1.png", "type": "image"}]
    assert "source_image__origin" not in form
    assert run["graph"]["edges"][0]["binding"]["multi"] is True


def test_a_single_item_field_keeps_the_path_and_its_origin(coordinator, port, alice, contracts, catalog):
    elixir = three_step()

    brew_through(coordinator, port, alice, contracts, elixir)

    second = port.submits[1]["form_data"]
    assert second["source_image"] == "generations/g1/f1.png"
    assert second["source_image__origin"] == {"generation_id": "g1", "file_index": 0}


def test_rerunning_the_second_step_still_shows_the_first_steps_outputs(coordinator, port, alice, contracts):
    from src.features.elixirs.routes import run_detail

    elixir = three_step()
    brew_through(coordinator, port, alice, contracts, elixir)

    node_run = coordinator.start_node_run(alice, elixir, contracts, "polish", templates_for(elixir)["polish"], "node-key-polish-again")
    detail = run_detail(coordinator, alice, node_run)

    assert detail["scope"] == "node" and detail["scope_node_id"] == "polish"
    assert set(detail["node_states"]) == {"polish", "sharpen"}
    assert detail["node_states"]["sharpen"]["manifest"]["images"][0]["file_id"] == "f1"
    assert {item["node_id"] for item in detail["invocations"]} == {"polish", "sharpen"}
    assert all(item["status"] == "completed" for item in detail["invocations"] if item["node_id"] == "sharpen")
    assert set(coordinator.store.get_run(node_run["id"])["node_states"]) == {"polish"}


def test_rerunning_a_step_keeps_the_steps_after_it_in_view(coordinator, port, alice, contracts):
    from src.features.elixirs.routes import run_detail

    elixir = three_step()
    brew_through(coordinator, port, alice, contracts, elixir)

    node_run = coordinator.start_node_run(alice, elixir, contracts, "sharpen", templates_for(elixir)["sharpen"], "node-key-sharpen-again")
    detail = run_detail(coordinator, alice, node_run)

    assert set(detail["node_states"]) == {"sharpen", "polish"}
    assert detail["node_states"]["polish"]["manifest"]["images"][0]["file_id"] == "f2"
    assert detail["node_states"]["sharpen"]["status"] != "done"
