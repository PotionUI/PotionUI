import asyncio

import pytest

from src.features.elixirs.brew import BindingFailed, bind_text, pick_text
from src.features.elixirs.coordinator import BrewRefused, Coordinator
from src.features.elixirs.dto import ElixirDefinition
from src.features.elixirs.errors import WriteRefused
from src.features.elixirs.graph import validate_graph
from src.features.elixirs.limits import Limits
from src.features.elixirs.repository import ElixirRepository
from src.features.elixirs.run_repository import RunStore

KREA = "4TK1KBQZ2XMB8ME0PTMXS1YJQP"
H3 = "01KX47H3MINIMAX000000000VA"
SEEDVR = "01KXB7C553THYMSMKY1QSYESFM"
INSTRUCTION = "For the provided image write a correct prompt"


def library(item_id):
    return {"kind": "library", "item_id": item_id, "media_type": "image", "original_filename": f"{item_id}.png"}


def edge(edge_id, source, source_handle, target, target_handle):
    return {"id": edge_id, "source": source, "source_handle": source_handle, "target": target, "target_handle": target_handle, "iteration": "each"}


def preset(node_id, preset_id, mode, title):
    return {"id": node_id, "kind": "preset", "title": title, "preset_id": preset_id, "mode": mode, "overrides": {}, "session_snapshot": {}}


def photos(assets=("p1", "p2"), node_id="photos"):
    return {"id": node_id, "kind": "media", "title": "Photos", "media_type": "image", "assets": [library(item) for item in assets]}


def writer(node_id="writer", instruction=INSTRUCTION, **extra):
    return {"id": node_id, "kind": "llm", "title": "Describe", "instruction": instruction, "target_node_id": None, "pinned": {}, **extra}


def template(preset_id, mode, **form):
    return {
        "preset_id": preset_id,
        "mode": mode,
        "prompts": [{"positive": "saved prompt", "negative": "blurry"}],
        "segments": [{"channel": "positive", "text": "saved prompt"}, {"channel": "negative", "text": "blurry"}],
        "form_data": {"seed": -1, "quantity": 1, "model": "model:flux", **form},
        "prompt_state": {"tabs": []},
    }


def describe_then_generate(assets=("p1", "p2"), **writer_extra):
    return {
        "nodes": [photos(assets), writer(**writer_extra), preset("make", KREA, "txt2img", "Make")],
        "edges": [edge("e1", "photos", "images", "writer", "images"), edge("e2", "writer", "text", "make", "prompt")],
    }


def paired(assets=("p1", "p2")):
    return {
        "nodes": [photos(assets), writer(), preset("restyle", KREA, "enhance", "Restyle")],
        "edges": [
            edge("e1", "photos", "images", "writer", "images"),
            edge("e2", "photos", "images", "restyle", "source_image"),
            edge("e3", "writer", "text", "restyle", "prompt"),
        ],
    }


def codes(report, severity=None):
    return [issue["code"] for issue in report["issues"] if severity is None or issue["severity"] == severity]


def validate(definition, catalog, contracts, writer_status=None, run_mode="review"):
    asked = None if writer_status is None else (writer_status if callable(writer_status) else (lambda llm_id=None: writer_status))
    return validate_graph(definition, run_mode=run_mode, catalog=catalog, contracts=contracts, limits=Limits(), writer=asked)


READY = {"available": True, "vision": True, "name": "Writer"}


def test_the_writer_node_round_trips_through_the_schema():
    parsed = ElixirDefinition.model_validate({"nodes": [writer(pinned={"library:p1": "kept"}, target_node_id="make")]}).model_dump()
    assert parsed["nodes"][0]["kind"] == "llm"
    assert parsed["nodes"][0]["pinned"] == {"library:p1": "kept"}
    assert parsed["nodes"][0]["llm_id"] is None
    chosen = ElixirDefinition.model_validate({"nodes": [writer(llm_id="llm-2")]}).model_dump()
    assert chosen["nodes"][0]["llm_id"] == "llm-2"


def test_text_flows_from_a_writer_into_a_preset_prompt(catalog, contracts):
    report = validate(describe_then_generate(), catalog, contracts, READY)

    assert codes(report, "error") == []
    assert report["estimates"]["nodes"]["writer"] == {"invocations": 2, "outputs": 2}
    assert report["estimates"]["nodes"]["make"] == {"invocations": 2, "outputs": 2}
    assert report["estimates"]["total_invocations"] == 2
    assert report["order"] == ["photos", "writer", "make"]


def test_files_and_a_prompt_can_both_feed_one_preset(catalog, contracts):
    report = validate(paired(), catalog, contracts, READY)

    assert codes(report, "error") == []
    assert report["estimates"]["nodes"]["restyle"]["invocations"] == 2


@pytest.mark.parametrize(
    "bad_edge",
    [edge("bad", "writer", "text", "make", "image"), edge("bad", "photos", "images", "writer2", "images")],
)
def test_text_and_media_ports_do_not_mix(catalog, contracts, bad_edge):
    definition = describe_then_generate()
    definition["nodes"].append(writer("writer2"))
    if bad_edge["target"] == "make":
        definition["nodes"][2] = preset("make", KREA, "enhance", "Make")
        bad_edge["target_handle"] = "source_image"
    else:
        bad_edge.update(source="writer", source_handle="text")
    definition["edges"].append(bad_edge)

    report = validate(definition, catalog, contracts, READY)

    assert "incompatible_media" in codes(report, "error")


def test_a_preset_without_a_prompt_has_no_prompt_input(catalog, contracts):
    definition = {
        "nodes": [writer(), preset("up", SEEDVR, "upscale", "Upscale")],
        "edges": [edge("e1", "writer", "text", "up", "prompt")],
    }

    assert "unknown_input" in codes(validate(definition, catalog, contracts, READY), "error")


def test_one_prompt_input_takes_one_connection(catalog, contracts):
    definition = describe_then_generate()
    definition["nodes"].append(writer("writer2"))
    definition["edges"].append(edge("e3", "writer2", "text", "make", "prompt"))

    report = validate(definition, catalog, contracts, READY)

    message = next(issue["message"] for issue in report["issues"] if issue["code"] == "too_many_inputs")
    assert "takes its prompt from one connection only" in message


def test_a_prompt_written_from_other_files_is_not_paired(catalog, contracts):
    definition = paired()
    definition["nodes"].append(photos(("p3",), node_id="others"))
    definition["edges"][0] = edge("e1", "others", "images", "writer", "images")

    report = validate(definition, catalog, contracts, READY)

    assert codes(report, "error") == ["text_not_paired"]


def test_writer_setup_problems_are_reported(catalog, contracts):
    definition = {"nodes": [photos(), writer(instruction="  ", target_node_id="gone")], "edges": [edge("e1", "photos", "images", "writer", "images")]}

    report = validate(definition, catalog, contracts, {"available": False, "message": "No language model is set up on this server."})

    assert codes(report) == ["no_instruction", "unknown_target", "no_llm"]
    assert report["issues"][2]["message"] == "Describe cannot write: No language model is set up on this server."


def test_a_writer_without_a_target_only_warns(catalog, contracts):
    report = validate({"nodes": [writer()], "edges": []}, catalog, contracts, READY)

    assert codes(report, "warning") == ["no_target"]
    assert report["valid"]


def test_a_writer_that_gets_files_needs_vision(catalog, contracts):
    report = validate(describe_then_generate(), catalog, contracts, {"available": True, "vision": False, "name": "Tiny"})

    issue = next(issue for issue in report["issues"] if issue["code"] == "llm_cannot_see")
    assert "Tiny cannot look at images" in issue["message"]
    assert codes(validate({"nodes": [writer()], "edges": []}, catalog, contracts, {"available": True, "vision": False, "name": "Tiny"}), "error") == []


def test_bind_text_replaces_the_positive_prompt_and_keeps_the_negative():
    bound = bind_text(template(KREA, "txt2img"), "prompt", "a fox")

    assert bound["prompts"] == [{"positive": "a fox", "negative": "blurry"}]
    assert bound["batch_prompts"] is False
    assert bound["prompt_state"] is None
    assert {(segment["channel"], segment["text"]) for segment in bound["segments"]} == {("negative", "blurry"), ("positive", "a fox")}


def test_bind_text_sets_the_negative_and_a_single_shot_director():
    request = template(H3, "video", video_director={"mode": "t2v", "segments": [{"id": "s1", "prompt": "old"}]})

    positive = bind_text(request, "prompt", "orbit the fox")
    negative = bind_text(positive, "negative_prompt", "text, watermark")

    assert negative["form_data"]["video_director"]["segments"] == [{"id": "s1", "prompt": "orbit the fox", "negative_prompt": "text, watermark"}]
    assert negative["prompts"] == [{"positive": "orbit the fox", "negative": "text, watermark"}]


def test_bind_text_refuses_a_multi_shot_director():
    request = template(H3, "video", video_director={"mode": "director", "segments": [{"id": "a"}, {"id": "b"}]})

    with pytest.raises(BindingFailed) as refused:
        bind_text(request, "prompt", "x")
    assert "2 shots" in str(refused.value)


def test_pick_text_matches_the_file_or_falls_back_to_a_single_text():
    texts = [{"for_key": "library:p1", "text": "one"}, {"for_key": "library:p2", "text": "two"}]
    assert pick_text(texts, "library:p2")["text"] == "two"
    assert pick_text(texts, "library:p9") is None
    assert pick_text([{"for_key": "-", "text": "only"}], "library:p9")["text"] == "only"


@pytest.fixture
def coordinator(scratch_db, port):
    return Coordinator(store=RunStore(), port=port, limits_provider=Limits, boot_id="boot-1", random_below=lambda limit: 7)


def saved(definition, run_mode="automatic"):
    record = ElixirRepository().create("alice", "Writer test", run_mode, definition)
    return {"id": record.id, "revision": record.revision, "definition": record.definition}


def templates_for(elixir):
    out = {}
    for node in elixir["definition"]["nodes"]:
        if node["kind"] == "preset":
            extra = {"source_image": "uploads/saved.png"} if node["mode"] == "enhance" else {}
            out[node["id"]] = template(node["preset_id"], node["mode"], **extra)
    return out


def drain(coordinator, rounds=3):
    async def go():
        for _ in range(rounds):
            await coordinator.pump()
            await coordinator.wait_for_writers()

    asyncio.run(go())


def start(coordinator, alice, contracts, definition, run_mode="automatic", key="brew-1"):
    elixir = saved(definition, run_mode)
    return coordinator.start_graph_run(alice, elixir, contracts, templates_for(elixir), run_mode, key), elixir


def test_a_brew_writes_one_prompt_per_file_and_each_reaches_its_generation(coordinator, port, alice, contracts):
    run, _ = start(coordinator, alice, contracts, describe_then_generate())

    drain(coordinator)

    assert [write["media"] for write in port.writes] == [
        [{"value": "uploads/p1.png", "media_type": "image"}],
        [{"value": "uploads/p2.png", "media_type": "image"}],
    ]
    assert {(write["preset_id"], write["mode"], write["instruction"]) for write in port.writes} == {(KREA, "txt2img", INSTRUCTION)}
    assert port.writes[0]["form_data"]["model"] == "model:flux"
    assert [submit["prompts"] for submit in port.submits] == [
        [{"positive": f"written for uploads/p1.png in {KREA}", "negative": "blurry"}],
        [{"positive": f"written for uploads/p2.png in {KREA}", "negative": "blurry"}],
    ]
    stored = coordinator.store.get_run(run["id"])
    texts = stored["node_states"]["writer"]["manifest"]["text"]
    assert [entry["for_key"] for entry in texts] == ["library:p1", "library:p2"]
    assert stored["node_states"]["writer"]["status"] == "done"
    history = ElixirRepository().get(stored["elixir_id"], "alice").results["writer"]["manifest"]["text"]
    assert [entry["text"] for entry in history] == [entry["text"] for entry in texts]


def test_a_paired_prompt_waits_for_the_writer_and_matches_each_file(coordinator, port, alice, contracts):
    port.write_gate = asyncio.Event()
    run, _ = start(coordinator, alice, contracts, paired())

    async def first_pass():
        await coordinator.pump()
        await asyncio.sleep(0)

    asyncio.run(first_pass())
    assert port.submits == []
    assert coordinator.store.get_run(run["id"])["checkpoints"]["e2"]["confirmed"] == ["library:p1", "library:p2"]
    assert "e3" not in coordinator.store.get_run(run["id"])["checkpoints"]

    port.write_gate = None
    for item in coordinator.store.invocations(run["id"]):
        if item["status"] == "running":
            coordinator.store.update_invocation(item["id"], {"status": "pending"})
    drain(coordinator)

    assert [(submit["form_data"]["source_image"], submit["prompts"][0]["positive"]) for submit in port.submits] == [
        ("uploads/p1.png", f"written for uploads/p1.png in {KREA}"),
        ("uploads/p2.png", f"written for uploads/p2.png in {KREA}"),
    ]


def test_a_pinned_text_is_used_without_calling_the_language_model(coordinator, port, alice, contracts):
    run, _ = start(coordinator, alice, contracts, describe_then_generate(pinned={"library:p1": "my own words"}))

    drain(coordinator)

    assert len(port.writes) == 1
    texts = coordinator.store.get_run(run["id"])["node_states"]["writer"]["manifest"]["text"]
    assert texts[0]["text"] == "my own words" and texts[0]["pinned"] is True
    assert port.submits[0]["prompts"][0]["positive"] == "my own words"


def test_a_root_writer_writes_once_for_its_target(coordinator, port, alice, contracts):
    definition = {
        "nodes": [writer(), preset("make", KREA, "txt2img", "Make")],
        "edges": [edge("e1", "writer", "text", "make", "prompt")],
    }
    start(coordinator, alice, contracts, definition)

    drain(coordinator)

    assert port.writes[0]["media"] == [] and port.writes[0]["input_key"] == "-"
    assert port.submits[0]["prompts"][0]["positive"] == f"written for nothing in {KREA}"


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (WriteRefused("The language model Tiny cannot look at images. Choose one with vision support."), "The language model Tiny cannot look at images. Choose one with vision support."),
        (RuntimeError("connection refused"), "The language model could not write a prompt: connection refused"),
    ],
)
def test_a_failed_write_is_shown_on_the_writer(coordinator, port, alice, contracts, error, message):
    port.write_error = error
    run, _ = start(coordinator, alice, contracts, describe_then_generate(assets=("p1",)))

    drain(coordinator)

    stored = coordinator.store.get_run(run["id"])
    assert stored["node_states"]["writer"]["status"] == "failed"
    assert stored["status"] == "needs_attention"
    assert [item["error"] for item in coordinator.store.invocations(run["id"])] == [message]
    assert port.submits == []


def test_a_failed_write_can_be_retried(coordinator, port, alice, contracts):
    port.write_error = RuntimeError("timeout")
    run, _ = start(coordinator, alice, contracts, describe_then_generate(assets=("p1",)))
    drain(coordinator)
    port.write_error = None

    coordinator.retry_failed(alice, run["id"], "writer")
    drain(coordinator)

    assert len(port.writes) == 2
    assert port.submits[0]["prompts"][0]["positive"] == f"written for uploads/p1.png in {KREA}"


def test_stopping_a_brew_stops_a_prompt_being_written(coordinator, port, alice, contracts):
    port.write_gate = asyncio.Event()
    run, _ = start(coordinator, alice, contracts, describe_then_generate(assets=("p1",)))

    async def go():
        await coordinator.pump()
        await asyncio.sleep(0)
        assert [item["status"] for item in coordinator.store.invocations(run["id"])] == ["running"]
        coordinator.request_cancel(alice, run["id"])
        await coordinator.pump()
        await coordinator.wait_for_writers()
        await coordinator.pump()

    asyncio.run(go())

    stored = coordinator.store.get_run(run["id"])
    assert [item["status"] for item in coordinator.store.invocations(run["id"])] == ["cancelled"]
    assert stored["status"] == "cancelled"
    assert port.submits == []


def test_a_prompt_left_running_by_a_restart_is_interrupted(coordinator, port, alice, contracts, scratch_db):
    run, _ = start(coordinator, alice, contracts, describe_then_generate(assets=("p1",)))
    item = coordinator.store.invocations(run["id"])[0]
    coordinator.store.update_invocation(item["id"], {"status": "running", "submitted_boot": "old-boot", "request": {"kind": "llm"}})
    restarted = Coordinator(store=RunStore(), port=port, limits_provider=Limits, boot_id="boot-2")

    asyncio.run(restarted.pump())

    stored = restarted.store.invocations(run["id"])[0]
    assert stored["status"] == "interrupted"
    assert stored["error"] == "The server restarted while this prompt was being written."


def test_a_written_prompt_can_be_edited_before_it_is_sent(coordinator, port, alice, contracts):
    run, _ = start(coordinator, alice, contracts, describe_then_generate(assets=("p1",)), run_mode="review")
    coordinator.confirm_edge(alice, run["id"], "e1", ["library:p1"], expected_revision=1)
    drain(coordinator)
    waiting = coordinator.store.get_run(run["id"])
    text_id = waiting["checkpoints"]["e2"]["eligible"][0]["text_id"]

    edited = coordinator.edit_text(alice, run["id"], text_id, "a calmer fox")

    assert edited["node_states"]["writer"]["manifest"]["text"][0]["text"] == "a calmer fox"
    assert edited["checkpoints"]["e2"]["eligible"][0]["edited"] is True
    coordinator.confirm_edge(alice, run["id"], "e2", [f"text:{text_id}"], expected_revision=1)
    drain(coordinator)
    assert port.submits[0]["prompts"][0]["positive"] == "a calmer fox"
    with pytest.raises(BrewRefused) as sent:
        coordinator.edit_text(alice, run["id"], text_id, "too late")
    assert sent.value.code == "text_sent"


def test_editing_an_unknown_text_is_refused(coordinator, alice, contracts):
    run, _ = start(coordinator, alice, contracts, describe_then_generate(assets=("p1",)), run_mode="review")

    with pytest.raises(BrewRefused) as missing:
        coordinator.edit_text(alice, run["id"], "nope", "x")
    assert missing.value.code == "text_not_found"


def test_a_preset_run_alone_reuses_the_last_written_prompts(coordinator, port, alice, contracts):
    run, elixir = start(coordinator, alice, contracts, paired(("p1",)))
    drain(coordinator)
    port.finish("g1", ("f1", "IMAGE"))
    drain(coordinator)
    port.submits.clear()

    node_run = coordinator.start_node_run(alice, elixir, contracts, "restyle", template(KREA, "enhance"), "node-1")
    drain(coordinator)

    assert node_run["pinned_inputs"]["sides"]["e3"][0]["for_key"] == "library:p1"
    assert port.submits[0]["prompts"][0]["positive"] == f"written for uploads/p1.png in {KREA}"
    assert len(port.writes) == 1


def test_a_writer_run_alone_writes_for_its_target(coordinator, port, alice, contracts):
    _, elixir = start(coordinator, alice, contracts, describe_then_generate(assets=("p1",)))
    drain(coordinator)
    port.finish("g1", ("f1", "IMAGE"))
    drain(coordinator)

    coordinator.start_node_run(alice, elixir, contracts, "writer", template(KREA, "txt2img"), "node-2")
    drain(coordinator)

    assert len(port.writes) == 2
    assert (port.writes[1]["preset_id"], port.writes[1]["mode"]) == (KREA, "txt2img")


@pytest.fixture
def api_harness(scratch_db, port, catalog, contracts, coordinator):
    from tests.features.elixirs.conftest import make_client, make_components

    components = make_components(port, catalog=catalog, contracts=contracts, coordinator=coordinator)
    return make_client(components, lambda: port.users["alice"])


def test_validation_asks_for_the_writer_only_when_the_canvas_has_one(api_harness, port):
    port.writer = {"available": True, "vision": False, "name": "Tiny"}
    with_writer = ElixirRepository().create("alice", "With", "review", describe_then_generate())
    without = ElixirRepository().create("alice", "Without", "review", {"nodes": [photos()], "edges": []})
    asked = []
    original = port.writer_status
    port.writer_status = lambda user, llm_id=None: asked.append(user.id) or original(user, llm_id)

    report = api_harness.post(f"/api/elixirs/{with_writer.id}/validate").json()
    api_harness.post(f"/api/elixirs/{without.id}/validate")

    assert "llm_cannot_see" in [issue["code"] for issue in report["issues"]]
    assert asked == ["alice"]


def test_the_edit_route_changes_a_waiting_prompt(api_harness, coordinator, port, alice, contracts):
    run, _ = start(coordinator, alice, contracts, describe_then_generate(assets=("p1",)), run_mode="review")
    coordinator.confirm_edge(alice, run["id"], "e1", ["library:p1"], expected_revision=1)
    drain(coordinator)
    text_id = coordinator.store.get_run(run["id"])["checkpoints"]["e2"]["eligible"][0]["text_id"]

    edited = api_harness.put(f"/api/elixirs/runs/{run['id']}/texts/{text_id}", json={"text": "night fox"})
    missing = api_harness.put(f"/api/elixirs/runs/{run['id']}/texts/nope", json={"text": "x"})
    blank = api_harness.put(f"/api/elixirs/runs/{run['id']}/texts/{text_id}", json={"text": ""})

    assert edited.status_code == 200
    writer_outputs = [item["outputs"] for item in edited.json()["invocations"] if item["node_id"] == "writer"]
    assert writer_outputs[0][0]["text"] == "night fox"
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "text_not_found"
    assert blank.status_code == 422


def test_each_writer_is_checked_against_its_own_model(catalog, contracts):
    asked = []
    statuses = {None: READY, "tiny": {"available": True, "vision": False, "name": "Tiny"}, "gone": {"available": False, "message": "That language model is not available to you. Choose another one."}}

    def status(llm_id=None):
        asked.append(llm_id)
        return statuses[llm_id]

    definition = {
        "nodes": [photos(), writer("a"), writer("b", llm_id="tiny"), writer("c", llm_id="gone")],
        "edges": [edge("e1", "photos", "images", "a", "images"), edge("e2", "photos", "images", "b", "images")],
    }

    report = validate(definition, catalog, contracts, status)

    problems = {(issue["node_id"], issue["code"]) for issue in report["issues"] if issue["code"] in ("llm_cannot_see", "no_llm")}
    assert problems == {("b", "llm_cannot_see"), ("c", "no_llm")}
    assert {"tiny", "gone"} <= set(asked)


def test_the_report_lists_the_models_the_picker_offers(catalog, contracts):
    models = [{"id": "llm-1", "name": "Writer", "vision": True}]
    report = validate({"nodes": [writer()], "edges": []}, catalog, contracts, {**READY, "models": models})
    assert report["writer"] == {"models": models}
    assert validate({"nodes": [photos()], "edges": []}, catalog, contracts)["writer"] is None


def test_the_chosen_model_reaches_the_language_model_call(coordinator, port, alice, contracts):
    start(coordinator, alice, contracts, describe_then_generate(llm_id="llm-2"))

    drain(coordinator)

    assert {write["llm_id"] for write in port.writes} == {"llm-2"}


def test_a_writer_without_a_choice_leaves_the_model_to_core(coordinator, port, alice, contracts):
    start(coordinator, alice, contracts, describe_then_generate())

    drain(coordinator)

    assert {write["llm_id"] for write in port.writes} == {None}


def test_the_reasoning_is_kept_on_the_written_prompt(coordinator, port, alice, contracts):
    port.reasoning = "The photo shows a fox."
    run, _ = start(coordinator, alice, contracts, describe_then_generate(assets=("p1",)))

    drain(coordinator)

    texts = coordinator.store.get_run(run["id"])["node_states"]["writer"]["manifest"]["text"]
    assert texts[0]["reasoning"] == "The photo shows a fox."


def test_no_reasoning_leaves_the_prompt_plain(coordinator, port, alice, contracts):
    run, _ = start(coordinator, alice, contracts, describe_then_generate(assets=("p1",)))

    drain(coordinator)

    texts = coordinator.store.get_run(run["id"])["node_states"]["writer"]["manifest"]["text"]
    assert "reasoning" not in texts[0]


def test_the_validate_route_checks_the_chosen_model(api_harness, port):
    port.writer_by_model["tiny"] = {"available": True, "vision": False, "name": "Tiny"}
    port.writer = {**port.writer, "models": [{"id": "llm-1", "name": "Writer", "vision": True}]}
    record = ElixirRepository().create("alice", "Pick", "review", describe_then_generate(llm_id="tiny"))

    report = api_harness.post(f"/api/elixirs/{record.id}/validate").json()

    assert "llm_cannot_see" in [issue["code"] for issue in report["issues"]]
    assert report["writer"]["models"][0]["id"] == "llm-1"



def generated_then_described_and_restyled():
    return {
        "nodes": [preset("gen", KREA, "txt2img", "Gen"), writer(), preset("restyle", KREA, "enhance", "Restyle")],
        "edges": [
            edge("e1", "gen", "images", "writer", "images"),
            edge("e2", "gen", "images", "restyle", "source_image"),
            edge("e3", "writer", "text", "restyle", "prompt"),
        ],
    }


def finished_four(coordinator, port, alice, contracts):
    run, _ = start(coordinator, alice, contracts, generated_then_described_and_restyled(), run_mode="review")
    drain(coordinator)
    port.finish("g1", *[(f"f{n}", "IMAGE") for n in range(1, 5)])
    for n in range(1, 5):
        port.media[f"history:g1:f{n}"] = {"value": f"outputs/f{n}.png", "media_type": "image", "original_filename": f"f{n}.png", "origin": None}
    drain(coordinator)
    return run


def test_continuing_the_writer_lane_continues_its_paired_preset_lane_with_the_same_files(coordinator, port, alice, contracts):
    run = finished_four(coordinator, port, alice, contracts)
    waiting = coordinator.store.get_run(run["id"])
    assert waiting["checkpoints"]["e1"]["linked"] == ["e2"]
    assert waiting["checkpoints"]["e2"]["linked"] == ["e1"]
    chosen = ["history:g1:f2", "history:g1:f4"]

    coordinator.confirm_edge(alice, run["id"], "e1", chosen, expected_revision=1)
    drain(coordinator)

    stored = coordinator.store.get_run(run["id"])
    assert stored["checkpoints"]["e1"]["confirmed"] == chosen
    assert stored["checkpoints"]["e2"]["confirmed"] == chosen
    assert [write["media"] for write in port.writes] == [
        [{"value": "outputs/f2.png", "media_type": "image"}],
        [{"value": "outputs/f4.png", "media_type": "image"}],
    ]
    restyled = [submit for submit in port.submits if submit["mode"] == "enhance"]
    assert [(submit["form_data"]["source_image"], submit["prompts"][0]["positive"]) for submit in restyled] == [
        ("outputs/f2.png", f"written for outputs/f2.png in {KREA}"),
        ("outputs/f4.png", f"written for outputs/f4.png in {KREA}"),
    ]


def test_a_choice_ticked_on_the_preset_lane_is_the_writer_lanes_choice_too(coordinator, port, alice, contracts):
    run = finished_four(coordinator, port, alice, contracts)
    chosen = ["history:g1:f1", "history:g1:f3"]

    coordinator.save_draft(alice, run["id"], "e2", chosen, expected_revision=1)

    checkpoints = coordinator.store.get_run(run["id"])["checkpoints"]
    assert checkpoints["e1"]["draft"] == chosen
    assert checkpoints["e2"]["draft"] == chosen

    coordinator.confirm_edge(alice, run["id"], "e2", chosen, expected_revision=2)
    drain(coordinator)

    assert [write["media"][0]["value"] for write in port.writes] == ["outputs/f1.png", "outputs/f3.png"]


def test_a_media_node_feeding_a_writer_and_its_preset_links_both_lanes(coordinator, port, alice, contracts):
    run, _ = start(coordinator, alice, contracts, paired(), run_mode="review")
    drain(coordinator)

    checkpoints = coordinator.store.get_run(run["id"])["checkpoints"]
    assert checkpoints["e1"]["linked"] == ["e2"]
