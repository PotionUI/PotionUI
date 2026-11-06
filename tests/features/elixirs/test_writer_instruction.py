import itertools
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.features.elixirs.coordinator import Coordinator
from src.features.elixirs.dto import ElixirDefinition
from src.features.elixirs.generation_port import GenerationPort
from src.features.elixirs.instruction import roll_instruction
from src.features.elixirs.limits import Limits
from src.features.elixirs.run_repository import RunStore
from tests.features.elixirs.test_writer import KREA, describe_then_generate, drain, saved, template

MOOD = "Write a {noir|sunlit|misty} prompt"
STYLED = "{noir|sunlit|misty|rainy} ${subject} in #colors"


def chip(chip_id, path, value, shuffle=False, all_values=()):
    return {
        "id": chip_id,
        "categoryPath": path,
        "valueId": f"v-{value}",
        "label": value,
        "value": value,
        "allValues": [{"id": f"v-{item}", "label": item, "value": item} for item in all_values],
        "shuffle": shuffle,
        "autoRegen": False,
    }


def text(value):
    return {"type": "text", "value": value}


def choice(*options, mode="shuffle", pinned=None):
    return {"type": "choice", "options": list(options), "mode": mode, "pinnedIndex": pinned}


def no_phrasebook(path):
    raise AssertionError(f"the phrasebook was asked for {path}")


def test_a_plain_instruction_is_used_exactly_as_written():

    plain = "  Describe the photo, 100% faithful #5 $10  "
    rolled = roll_instruction(plain, {}, {"mood": text("noir")}, 11, no_phrasebook)

    assert (rolled.text, rolled.seed, rolled.rolled) == (plain, None, False)


def test_choices_roll_from_the_seed_and_reproduce_with_it():
    rolls = {roll_instruction(MOOD, {}, None, seed, no_phrasebook).text for seed in range(40)}

    assert rolls == {"Write a noir prompt", "Write a sunlit prompt", "Write a misty prompt"}
    assert roll_instruction(MOOD, {}, None, 5, no_phrasebook) == roll_instruction(MOOD, {}, None, 5, no_phrasebook)


def test_the_writers_own_variables_fill_the_instruction():
    variables = {"subject": text("fox"), "time": choice("dawn", "dusk")}

    rolls = {roll_instruction("A ${subject} at ${time}, ${missing}.", {}, variables, seed, no_phrasebook).text for seed in range(30)}

    assert rolls == {"A fox at dawn, .", "A fox at dusk, ."}
    rolled = roll_instruction("A ${subject} at ${time}.", {}, variables, 3, no_phrasebook)
    assert rolled.rolled and rolled.seed == 3
    assert rolled == roll_instruction("A ${subject} at ${time}.", {}, variables, 3, no_phrasebook)


def test_a_pinned_choice_variable_always_uses_its_pin_and_a_per_image_one_rolls():
    pinned = {"light": choice("candles", "neon", "sun", mode="pin", pinned=1)}
    per_image = {"light": choice("candles", "neon", mode="per-image")}

    assert {roll_instruction("Lit by ${light}", {}, pinned, seed, no_phrasebook).text for seed in range(20)} == {"Lit by neon"}
    assert {roll_instruction("Lit by ${light}", {}, per_image, seed, no_phrasebook).text for seed in range(30)} == {"Lit by candles", "Lit by neon"}


def test_a_number_variable_rolls_inside_its_range():
    variables = {"age": {"type": "number", "mode": "shuffle", "min": 20, "max": 22, "step": 1, "value": 20}}

    rolls = {roll_instruction("Aged ${age}", {}, variables, seed, no_phrasebook).text for seed in range(40)}

    assert rolls == {"Aged 20", "Aged 21", "Aged 22"}


def test_a_fixed_phrasebook_chip_keeps_its_value_and_a_shuffled_one_rolls_from_the_phrasebook():
    asked = []

    def values_for(path):
        asked.append(path)
        return ["red", "green", "blue"]

    chips = {"c1": chip("c1", "style", "watercolor"), "c2": chip("c2", "colors", "red", shuffle=True)}
    rolls = {roll_instruction("In #style with #colors tones", chips, None, seed, values_for).text for seed in range(30)}

    assert rolls == {"In watercolor with red tones", "In watercolor with green tones", "In watercolor with blue tones"}
    assert set(asked) == {"colors"}


def test_a_phrasebook_value_is_itself_a_template_and_paths_do_not_bleed_into_longer_ones():
    chips = {
        "c1": chip("c1", "light", "{soft|hard} light"),
        "c2": chip("c2", "light.warm", "golden"),
        "c3": chip("c3", "camera angles", "low angle"),
    }

    rolled = roll_instruction("#light, #light.warm, #[camera angles]", chips, None, 2, no_phrasebook)

    assert rolled.text in {"soft light, golden, low angle", "hard light, golden, low angle"}


def test_a_shuffled_chip_falls_back_to_its_saved_values_when_the_phrasebook_has_none():
    chips = {"c1": chip("c1", "gone", "a", shuffle=True, all_values=("a", "b"))}

    rolls = {roll_instruction("#gone", chips, None, seed, lambda path: []).text for seed in range(20)}

    assert rolls == {"a", "b"}


def test_the_port_reads_active_phrasebook_values_of_the_owner():
    values = Mock()
    values.get_by_path.return_value = [
        SimpleNamespace(value="red", is_active=True),
        SimpleNamespace(value="grey", is_active=False),
        SimpleNamespace(value="", is_active=True),
    ]
    port = GenerationPort(
        users=None, orchestrator=None, broadcaster=None, generations=None, generation_files=None, history=None,
        file_service=None, media_store=None, prompt_writer=None, storage_root=lambda: "", phrasebook_values=values,
    )

    assert port.phrasebook_texts(SimpleNamespace(id="alice"), "colors") == ["red"]
    values.get_by_path.assert_called_once_with("colors", "alice")


def test_instruction_chips_round_trip_through_the_schema():
    chips = {"c1": chip("c1", "colors", "red", shuffle=True, all_values=("red",))}
    definition = describe_then_generate(instruction="Use #colors", instruction_chips=chips)

    node = ElixirDefinition.model_validate(definition).model_dump()["nodes"][1]

    assert node["instruction_chips"]["c1"]["categoryPath"] == "colors"
    assert node["instruction_chips"]["c1"]["shuffle"] is True


def test_writer_variables_round_trip_through_the_schema_and_bad_ones_are_refused():
    variables = {"light": choice("candles", "neon", mode="pin", pinned=0), "subject": text("fox")}

    node = ElixirDefinition.model_validate(describe_then_generate(instruction="${light}", variables=variables)).model_dump()["nodes"][1]

    assert node["variables"] == variables
    with pytest.raises(ValueError, match="pinnedIndex"):
        ElixirDefinition.model_validate(describe_then_generate(variables={"light": choice("a", mode="pin", pinned=4)}))


@pytest.fixture
def counting(scratch_db, port):
    seeds = itertools.count(100)
    return Coordinator(store=RunStore(), port=port, limits_provider=Limits, boot_id="boot-1", random_below=lambda limit: next(seeds))


def brew(coordinator, alice, contracts, definition, target_variables=None):
    elixir = saved(definition)
    made = template(KREA, "txt2img")
    if target_variables is not None:
        made["variables"] = target_variables
    return coordinator.start_graph_run(alice, elixir, contracts, {"make": made}, "automatic", "brew-1")


def written(coordinator, run):
    return coordinator.store.get_run(run["id"])["node_states"]["writer"]["manifest"]["text"]


def test_each_write_gets_its_own_roll_with_variables_and_phrasebook(counting, port, alice, contracts):
    port.phrasebook["colors"] = ["red", "green", "blue", "amber"]
    for index in range(8):
        port.media[f"library:p{index}"] = {"value": f"uploads/p{index}.png", "media_type": "image", "original_filename": f"p{index}.png", "origin": None}
    chips = {"c1": chip("c1", "colors", "red", shuffle=True)}
    variables = {"subject": text("a fox")}
    definition = describe_then_generate(assets=tuple(f"p{index}" for index in range(8)), instruction=STYLED, instruction_chips=chips, variables=variables)
    run = brew(counting, alice, contracts, definition)

    drain(counting)

    instructions = [write["instruction"] for write in port.writes]
    assert len(instructions) == 8 and len(set(instructions)) > 1
    assert all(" a fox in " in text and "{" not in text and "#" not in text for text in instructions)
    seeds = sorted(write["instruction_seed"] for write in port.writes)
    assert len(set(seeds)) == 8
    for write in port.writes:
        expected = roll_instruction(STYLED, chips, variables, write["instruction_seed"], lambda path: port.phrasebook[path])
        assert write["instruction"] == expected.text
    texts = written(counting, run)
    assert sorted(entry["rolled_instruction"] for entry in texts) == sorted(instructions)
    assert sorted(entry["instruction_seed"] for entry in texts) == seeds
    assert sorted(item["effective_seed"] for item in counting.store.invocations(run["id"]) if item["node_id"] == "writer") == seeds


def test_a_pinned_writer_variable_reaches_the_instruction_as_its_pin(counting, port, alice, contracts):
    variables = {"light": choice("candles", "neon", "sun", mode="pin", pinned=0)}
    definition = describe_then_generate(assets=("p1", "p2", "p3"), instruction="Lit by ${light}", variables=variables)
    run = brew(counting, alice, contracts, definition)

    drain(counting)

    assert {write["instruction"] for write in port.writes} == {"Lit by candles"}
    assert {entry["rolled_instruction"] for entry in written(counting, run)} == {"Lit by candles"}


def test_variables_of_the_target_step_never_reach_the_instruction(counting, port, alice, contracts):
    definition = describe_then_generate(assets=("p1",), instruction="Lit by ${light}.")
    brew(counting, alice, contracts, definition, target_variables={"light": "candles"})

    drain(counting)

    assert port.writes[0]["instruction"] == "Lit by ."


def test_a_plain_instruction_reaches_the_model_unchanged_and_shows_no_roll(counting, port, alice, contracts):
    definition = describe_then_generate(assets=("p1",), instruction="  Describe it, 100% faithful.  ")
    run = brew(counting, alice, contracts, definition, target_variables={"light": "candles"})

    drain(counting)

    assert port.writes[0]["instruction"] == "  Describe it, 100% faithful.  "
    assert "instruction_seed" not in port.writes[0]
    entry = written(counting, run)[0]
    assert "rolled_instruction" not in entry and "instruction_seed" not in entry
    assert [item["effective_seed"] for item in counting.store.invocations(run["id"]) if item["node_id"] == "writer"] == [None]


def test_running_the_writer_again_rolls_again(counting, port, alice, contracts):
    elixir = saved(describe_then_generate(assets=("p1",), instruction=MOOD))
    counting.start_graph_run(alice, elixir, contracts, {"make": template(KREA, "txt2img")}, "automatic", "brew-1")
    drain(counting)
    port.finish("g1", ("f1", "IMAGE"))
    drain(counting)

    counting.start_node_run(alice, elixir, contracts, "writer", template(KREA, "txt2img"), "node-2")
    drain(counting)

    assert len(port.writes) == 2
    assert port.writes[0]["instruction_seed"] != port.writes[1]["instruction_seed"]
