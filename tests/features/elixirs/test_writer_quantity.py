import asyncio
import itertools

import pytest

from src.features.elixirs.brew import bind_texts, media_rows, text_rows, texts_for_input
from src.features.elixirs.coordinator import Coordinator
from src.features.elixirs.dto import ElixirDefinition
from src.features.elixirs.generation_port import GenerationPort
from src.features.elixirs.limits import Limits
from src.features.elixirs.repository import ElixirRepository
from src.features.elixirs.run_repository import RunStore
from tests.features.elixirs.test_writer import (
    KREA,
    describe_then_generate,
    edge,
    paired,
    preset,
    template,
    writer,
)

ROLLED = "Describe it in {red|green|blue|gold|white|grey|teal|pink} light"


@pytest.fixture
def coordinator(scratch_db, port):
    seeds = itertools.count(11)
    return Coordinator(store=RunStore(), port=port, limits_provider=Limits, boot_id="boot-1", random_below=lambda limit: next(seeds))


@pytest.fixture
def slotted(port):
    original = port.write_prompt

    async def write(user, request):
        result = await original(user, request)
        return {**result, "text": f"for {request.get('slot_key') or request['input_key']}"}

    port.write_prompt = write
    return port


def batching(catalog, preset_id=KREA):
    catalog[preset_id]["prompt_batch"] = True
    return catalog


def brew(coordinator, alice, contracts, definition, catalog=None, quantity=1, **form):
    record = ElixirRepository().create("alice", "Quantity test", "automatic", definition)
    elixir = {"id": record.id, "revision": record.revision, "definition": record.definition}
    templates = {}
    for node in definition["nodes"]:
        if node["kind"] == "preset":
            extra = {"source_image": "uploads/saved.png"} if node["mode"] == "enhance" else {}
            templates[node["id"]] = template(node["preset_id"], node["mode"], quantity=quantity, **extra, **form)
    return coordinator.start_graph_run(alice, elixir, contracts, templates, "automatic", "brew-q", catalog=catalog), elixir


def drain(coordinator, rounds=4):
    async def go():
        for _ in range(rounds):
            await coordinator.pump()
            await coordinator.wait_for_writers()

    asyncio.run(go())


def positives(submit):
    return [pair["positive"] for pair in submit["prompts"]]


def test_the_writer_quantity_round_trips_and_is_capped():
    parsed = ElixirDefinition.model_validate({"nodes": [writer(prompts_per_input=4)]}).model_dump()
    assert parsed["nodes"][0]["prompts_per_input"] == 4
    assert ElixirDefinition.model_validate({"nodes": [writer()]}).model_dump()["nodes"][0]["prompts_per_input"] == 1
    with pytest.raises(ValueError):
        ElixirDefinition.model_validate({"nodes": [writer(prompts_per_input=21)]})
    with pytest.raises(ValueError):
        ElixirDefinition.model_validate({"nodes": [writer(prompts_per_input=0)]})


def test_each_prompt_per_input_is_its_own_write_with_its_own_roll(coordinator, slotted, alice, contracts):
    run, _ = brew(coordinator, alice, contracts, describe_then_generate(prompts_per_input=3, instruction=ROLLED))

    drain(coordinator)

    assert len(slotted.writes) == 6
    seeds = [write["instruction_seed"] for write in slotted.writes]
    assert len(set(seeds)) == 6
    assert all(write["instruction"] != ROLLED for write in slotted.writes)
    texts = coordinator.store.get_run(run["id"])["node_states"]["writer"]["manifest"]["text"]
    assert [(entry["for_key"], entry["prompt_number"], entry["prompt_count"]) for entry in texts] == [
        ("library:p1", 1, 3), ("library:p1", 2, 3), ("library:p1", 3, 3),
        ("library:p2", 1, 3), ("library:p2", 2, 3), ("library:p2", 3, 3),
    ]
    assert [entry["slot_key"] for entry in texts[:3]] == ["library:p1", "library:p1#2", "library:p1#3"]
    assert len({entry["instruction_seed"] for entry in texts}) == 6


def test_one_prompt_per_input_keeps_the_plain_text_entry(coordinator, port, alice, contracts):
    run, _ = brew(coordinator, alice, contracts, describe_then_generate(assets=("p1",)))

    drain(coordinator)

    entry = coordinator.store.get_run(run["id"])["node_states"]["writer"]["manifest"]["text"][0]
    assert "prompt_number" not in entry and "slot_key" not in entry


def test_a_preset_without_batch_support_gets_one_generation_per_prompt(coordinator, slotted, alice, contracts, catalog):
    brew(coordinator, alice, contracts, describe_then_generate(prompts_per_input=2), catalog=catalog)

    drain(coordinator)

    assert [positives(submit) for submit in slotted.submits] == [
        ["for library:p1"], ["for library:p1#2"], ["for library:p2"], ["for library:p2#2"],
    ]
    assert not any(submit.get("batch_prompts") for submit in slotted.submits)


def test_prompts_written_for_one_input_run_as_one_batch(coordinator, slotted, alice, contracts, catalog):
    brew(coordinator, alice, contracts, describe_then_generate(prompts_per_input=3), catalog=batching(catalog))

    drain(coordinator)

    assert [positives(submit) for submit in slotted.submits] == [
        ["for library:p1", "for library:p1#2", "for library:p1#3"],
        ["for library:p2", "for library:p2#2", "for library:p2#3"],
    ]
    assert all(submit["batch_prompts"] is True for submit in slotted.submits)
    assert slotted.submits[0]["prompt_state"]["prompts"] == [{"prompt": text} for text in positives(slotted.submits[0])]
    assert [segment["prompt_index"] for segment in slotted.submits[0]["segments"] if segment["channel"] == "positive"] == [0, 1, 2]


def test_a_paired_batch_shares_only_its_own_file(coordinator, slotted, alice, contracts, catalog):
    definition = paired()
    definition["nodes"][1]["prompts_per_input"] = 2

    brew(coordinator, alice, contracts, definition, catalog=batching(catalog))
    drain(coordinator)

    assert [(submit["form_data"]["source_image"], positives(submit)) for submit in slotted.submits] == [
        ("uploads/p1.png", ["for library:p1", "for library:p1#2"]),
        ("uploads/p2.png", ["for library:p2", "for library:p2#2"]),
    ]


def test_a_paired_preset_without_batch_support_runs_each_prompt_with_its_file(coordinator, slotted, alice, contracts, catalog):
    definition = paired(("p1",))
    definition["nodes"][1]["prompts_per_input"] = 2

    brew(coordinator, alice, contracts, definition, catalog=catalog)
    drain(coordinator)

    assert [(submit["form_data"]["source_image"], positives(submit)) for submit in slotted.submits] == [
        ("uploads/p1.png", ["for library:p1"]),
        ("uploads/p1.png", ["for library:p1#2"]),
    ]


def test_a_director_session_never_batches(coordinator, slotted, alice, contracts, catalog):
    director = {"mode": "t2v", "segments": [{"id": "s1", "prompt": ""}]}
    brew(coordinator, alice, contracts, describe_then_generate(assets=("p1",), prompts_per_input=2), catalog=batching(catalog), video_director=director)

    drain(coordinator)

    assert [positives(submit) for submit in slotted.submits] == [["for library:p1"], ["for library:p1#2"]]


def test_a_batch_over_the_image_cap_is_split(coordinator, slotted, alice, contracts, catalog):
    brew(coordinator, alice, contracts, describe_then_generate(assets=("p1",), prompts_per_input=5), catalog=batching(catalog), quantity=8)

    drain(coordinator)

    assert [len(submit["prompts"]) for submit in slotted.submits] == [2, 2, 1]
    assert all(len(submit["prompts"]) * submit["form_data"]["quantity"] <= 20 for submit in slotted.submits)
    assert [submit.get("batch_prompts") for submit in slotted.submits] == [True, True, False]


def test_batch_outputs_map_back_to_their_prompts_once_the_batch_is_split(coordinator, slotted, alice, contracts, catalog):
    run, _ = brew(coordinator, alice, contracts, describe_then_generate(assets=("p1",), prompts_per_input=2), catalog=batching(catalog))
    drain(coordinator)
    slotted.generations["g1"]["status"] = "completed"
    slotted.generations["g1"]["batch_files"] = [
        {"id": "f1", "file_type": "IMAGE", "file_path": "generations/g1/f1.png", "index": 0, "generation_id": "g1", "prompt_index": 0},
        {"id": "f2", "file_type": "IMAGE", "file_path": "generations/g9/f2.png", "index": 0, "generation_id": "g9", "prompt_index": 1},
    ]

    drain(coordinator)
    assert [item["status"] for item in coordinator.store.invocations(run["id"]) if item["node_id"] == "make"] == ["submitted"]

    coordinator.on_generation_finished("g1")
    drain(coordinator)

    stored = coordinator.store.get_run(run["id"])
    texts = stored["node_states"]["writer"]["manifest"]["text"]
    images = stored["node_states"]["make"]["manifest"]["images"]
    assert [(entry["generation_id"], entry["file_id"], entry["prompt_index"]) for entry in images] == [("g1", "f1", 0), ("g9", "f2", 1)]
    assert [entry["prompt_key"] for entry in images] == [f"text:{entry['text_id']}" for entry in texts]


def test_a_preset_run_alone_batches_the_last_written_prompts(coordinator, slotted, alice, contracts, catalog):
    definition = paired(("p1",))
    definition["nodes"][1]["prompts_per_input"] = 2
    _, elixir = brew(coordinator, alice, contracts, definition, catalog=catalog)
    drain(coordinator)
    slotted.finish("g1", ("f1", "IMAGE"))
    slotted.finish("g2", ("f2", "IMAGE"))
    drain(coordinator)
    slotted.submits.clear()

    coordinator.start_node_run(alice, elixir, contracts, "restyle", template(KREA, "enhance"), "node-q", catalog=batching(catalog))
    drain(coordinator)

    assert [positives(submit) for submit in slotted.submits] == [["for library:p1", "for library:p1#2"]]


def test_writer_estimates_count_every_prompt(catalog, contracts):
    from tests.features.elixirs.test_writer import READY, validate

    report = validate(describe_then_generate(prompts_per_input=3), catalog, contracts, READY)

    assert report["estimates"]["nodes"]["writer"] == {"invocations": 6, "outputs": 6}
    assert report["estimates"]["nodes"]["make"]["invocations"] == 6


def test_text_rows_group_by_input_and_chunk():
    texts = [{"kind": "text", "text_id": f"t{index}", "for_key": key} for index, key in enumerate(["a", "a", "b", "a"])]

    rows = text_rows(texts, 2)

    assert [(key, entry.get("batch")) for key, entry in rows] == [
        ("text:t0", ["text:t0", "text:t1"]),
        ("text:t3", None),
        ("text:t2", None),
    ]


def test_media_rows_follow_the_texts_written_for_that_file():
    texts = [{"kind": "text", "text_id": f"t{index}", "for_key": "library:p1", "prompt_number": index + 1} for index in range(3)]

    assert media_rows("library:p1", {"kind": "library", "item_id": "p1"}, texts_for_input(texts, "library:p1"), 2) == [
        ("library:p1", {"kind": "library", "item_id": "p1", "batch": ["text:t0", "text:t1"]}),
        ("library:p1#2", {"kind": "library", "item_id": "p1", "batch": ["text:t2"]}),
    ]
    assert texts_for_input(texts + [{"kind": "text", "text_id": "x", "for_key": "library:p2"}], "library:p3") == []
    assert len(texts_for_input(texts, "library:p3")) == 3


def test_bind_texts_keeps_the_negative_for_every_prompt():
    bound = bind_texts(template(KREA, "txt2img"), ["one", "two"])

    assert bound["prompts"] == [{"positive": "one", "negative": "blurry"}, {"positive": "two", "negative": "blurry"}]
    assert bound["batch_prompts"] is True


class FakeGeneration:
    def __init__(self, prompt):
        self.prompt_state = {"prompt": prompt}


class FakeGenerations:
    def __init__(self, members):
        self.members = members

    def batch_member_ids(self, batch_id, user_id):
        return [batch_id, *self.members]

    def get_by_id(self, generation_id, user_id=None, include_files=False):
        return FakeGeneration(self.members[generation_id])


def port_with(members, files):
    port = GenerationPort(None, None, None, FakeGenerations(members), None, None, None, None, None, lambda: "")
    port.files = lambda user, generation_id: files[generation_id]
    return port


def test_batch_files_follow_each_split_entry_even_when_a_prompt_made_nothing(alice):
    port = port_with({"m3": "three"}, {"g1": [{"id": "f1"}], "m3": [{"id": "f3"}]})

    files = port.batch_files(alice, "g1", ["one", "two", "three"], 1)

    assert [(entry["id"], entry["generation_id"], entry["prompt_index"]) for entry in files] == [("f1", "g1", 0), ("f3", "m3", 2)]


def test_batch_files_left_together_are_split_by_position(alice):
    port = port_with({}, {"g1": [{"id": f"f{index}"} for index in range(4)]})

    files = port.batch_files(alice, "g1", ["one", "two"], 2)

    assert [entry["prompt_index"] for entry in files] == [0, 0, 1, 1]
