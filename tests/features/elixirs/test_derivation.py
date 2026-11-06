import json
from pathlib import Path

import pytest
import yaml

from src.features.elixirs.catalog import installed_catalog
from src.features.elixirs.contracts import ModeContract, lint_contract
from src.features.elixirs.derivation import derived_contracts, preset_results, sentence_case
from src.features.presets.file_repository import FilePresetRepository
from src.features.presets.linter import PresetLinter
from src.features.presets.loader import PresetTemplateLoader
from tests.features.elixirs.conftest import make_client, make_components, make_user

REPO_ROOT = Path(__file__).resolve().parents[3]
MARKETPLACE = REPO_ROOT / "content" / "presets" / "marketplace"
EXPECTED = json.loads(Path(__file__).with_name("expected_contracts.json").read_text(encoding="utf-8"))
PRESET_ID = "ELIXIR-DERIVE-TEST"

STEPPER = {"name": "quantity", "type": "stepper", "default": 1, "configuration": {"min": 1, "max": 4, "step": 1}}
FINAL_GALLERY = {"name": "gallery", "input": [["image", "generator", "image"], ["seed", "seed_generator", "seed"]]}


def media_field(name, kind, label=None, **extra):
    return {"name": name, "type": kind, "label": label or name, **extra}


def write_preset(root, fields, pipes=None, mode="make", extra_manifest=None, preset_id=PRESET_ID, override=None):
    preset_dir = root / preset_id
    mode_dir = preset_dir / "modes" / mode
    mode_dir.mkdir(parents=True)
    manifest = {
        "schema": 1, "id": preset_id, "name": f"Preset {preset_id}", "version": "1.0.0",
        "category": "image", "engine": "native", "modes": [mode],
    }
    manifest.update(extra_manifest or {})
    (preset_dir / "preset.yml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    pipeline = {"pipeline": [{"name": "generator", "id": "generator", "configuration": {"quantity": "{{ form.quantity }}"}}, *(pipes if pipes is not None else [FINAL_GALLERY])]}
    (mode_dir / "pipeline.yml").write_text(yaml.safe_dump(pipeline), encoding="utf-8")
    form = {
        "name": "custom",
        "fields": [{"type": "tabs", "children": [{"type": "tab", "anchor": "main", "label": "Main", "children": fields}]}],
    }
    (mode_dir / "form.yml").write_text(yaml.safe_dump(form), encoding="utf-8")
    if override is not None:
        text = override if isinstance(override, str) else yaml.safe_dump({"schema_version": 1, **override})
        (mode_dir / "elixir.yml").write_text(text, encoding="utf-8")
    return preset_dir


def load(*roots):
    loader = PresetTemplateLoader([str(root) for root in roots])
    loader.load_presets()
    return loader


def result_of(root, mode="make"):
    loader = load(root)
    return {r.mode: r for r in preset_results(loader.get(PRESET_ID))}[mode]


@pytest.fixture
def root(tmp_path):
    return tmp_path / "presets"


@pytest.fixture(scope="module")
def marketplace():
    return load(MARKETPLACE)


def test_the_six_original_contracts_are_derived_identically(marketplace):
    derived = derived_contracts(marketplace.presets)

    for key, expected in EXPECTED.items():
        preset_id, mode = key.split("|")
        assert derived[(preset_id, mode)].model_dump(mode="json") == expected


def test_media_fields_become_inputs_with_sentence_case_labels(root):
    write_preset(
        root,
        [media_field("photo", "image", "Source Photo"), media_field("clip", "video", "HD Clip"), STEPPER],
    )

    contract = result_of(root).contract

    assert contract.label == "Make"
    assert contract.prompt is True
    assert contract.quantity_field == "quantity"
    assert contract.inputs["photo"].label == "Source photo"
    assert contract.inputs["photo"].media_type == "image"
    assert contract.inputs["photo"].target.model_dump() == {"kind": "form_field", "field": "photo", "role": None}
    assert contract.inputs["clip"].label == "HD clip"
    assert contract.inputs["clip"].media_type == "video"
    assert contract.outputs["images"].label == "Final images"
    assert contract.outputs["images"].selector == {"is_final": True, "is_derived": False}


def test_a_multi_image_field_is_still_an_image_input(root):
    write_preset(root, [media_field("photos", "image", "Photos", configuration={"multi": True})])

    assert result_of(root).contract.inputs["photos"].media_type == "image"


def test_video_pipes_give_video_outputs_and_promptless_modes_have_no_prompt(root):
    write_preset(
        root,
        [media_field("clip", "video")],
        pipes=[{"name": "gallery", "input": [["video", "generator", "video"]]}],
        extra_manifest={"vars": {"promptless_modes": ["make"]}},
    )

    contract = result_of(root).contract

    assert contract.prompt is False
    assert contract.quantity_field is None
    assert list(contract.outputs) == ["videos"]
    assert contract.outputs["videos"].media_type == "video"


def test_derived_and_conditional_galleries_are_not_final_outputs(root):
    write_preset(
        root,
        [],
        pipes=[
            FINAL_GALLERY,
            {"name": "gallery", "configuration": {"derived": True}, "input": [["video", "generator", "video"]]},
            {"name": "gallery", "enabled": "{{ form.keep }}", "input": [["video", "generator", "video"]]},
            {"name": "gallery", "enabled": False, "input": [["video", "generator", "video"]]},
        ],
    )

    assert list(result_of(root).contract.outputs) == ["images"]


@pytest.mark.parametrize(
    "fields, pipes, problem",
    [
        ([media_field("voice", "audio")], None, "voice is a audio picker"),
        ([media_field("any", "media")], None, "any is a media picker"),
        ([], [{"name": "gallery", "input": [["audio", "generator", "audio"]]}], "outputs audio"),
        ([], [], "no always-enabled final gallery"),
        ([], [{"name": "gallery", "configuration": {"derived": "{{ form.d }}"}, "input": [["image", "g", "image"]]}], "templated derived flag"),
        ([media_field("images", "image")], None, "used for both an input and an output"),
    ],
)
def test_modes_that_cannot_be_derived_are_not_listed_and_say_why(root, fields, pipes, problem):
    write_preset(root, fields, pipes=pipes)

    result = result_of(root)

    assert result.contract is None and result.errors == []
    assert any(problem in message for message in result.problems)
    assert derived_contracts(load(root).presets) == {}


def test_an_override_replaces_scalars_and_merges_ports(root):
    write_preset(
        root,
        [media_field("photo", "image", "Photo"), media_field("mask", "image", "Mask"), STEPPER],
        override={
            "label": "Restyle",
            "prompt": False,
            "quantity_field": None,
            "inputs": {
                "photo": {"label": "Picture"},
                "mask": None,
                "pose": {"label": "Pose", "media_type": "image", "target": {"kind": "video_director_role", "role": "first"}},
            },
            "outputs": {"images": {"label": "Restyled images"}},
        },
    )

    result = result_of(root)
    contract = result.contract

    assert result.overridden
    assert (contract.label, contract.prompt, contract.quantity_field) == ("Restyle", False, None)
    assert set(contract.inputs) == {"photo", "pose"}
    assert contract.inputs["photo"].label == "Picture"
    assert contract.inputs["photo"].target.field == "photo"
    assert contract.inputs["pose"].target.role == "first"
    assert contract.outputs["images"].label == "Restyled images"
    assert contract.outputs["images"].media_type == "image"


def test_an_override_can_supply_what_derivation_cannot(root):
    write_preset(
        root,
        [media_field("voice", "audio")],
        pipes=[],
        override={
            "inputs": {"voice": {"label": "Voice", "media_type": "video", "target": {"kind": "form_field", "field": "voice"}}},
            "outputs": {"videos": {"label": "Clips", "media_type": "video"}},
        },
    )

    contract = result_of(root).contract

    assert contract.outputs["videos"].label == "Clips"
    assert contract.outputs["videos"].selector == {"is_final": True, "is_derived": False}


def test_enabled_false_hides_the_mode(root):
    write_preset(root, [], override={"enabled": False})

    result = result_of(root)

    assert result.disabled and result.contract is None and result.errors == []
    assert derived_contracts(load(root).presets) == {}


@pytest.mark.parametrize(
    "override",
    [
        "schema_version: 1\nunknown: true\n",
        "schema_version: 2\n",
        "label: no version\n",
        "schema_version: 1\ninputs:\n  Bad Name: {label: x}\n",
        "schema_version: 1\ninputs:\n  extra:\n    label: x\n",
        "schema_version: 1\ninputs:\n  a: {label: x, media_type: image, target: {kind: form_field}}\n",
        "schema_version: 1\noutputs:\n  images: {media_type: audio}\n",
        "schema_version: 1: [\n",
    ],
)
def test_an_invalid_override_is_an_error_and_the_mode_is_not_listed(root, override):
    write_preset(root, [], override=override)

    result = result_of(root)

    assert result.contract is None
    assert result.errors and "modes/make/elixir.yml" in result.errors[0]
    assert derived_contracts(load(root).presets) == {}


def test_presets_from_a_plugin_root_are_derived_and_overridable(tmp_path):
    core, plugin = tmp_path / "core", tmp_path / "plugin" / "presets"
    write_preset(core, [], preset_id="CORE-PRESET")
    write_preset(plugin, [media_field("photo", "image")], preset_id="PLUGIN-PRESET", override={"label": "From plugin"})

    contracts = derived_contracts(load(core, plugin).presets)

    assert contracts[("CORE-PRESET", "make")].label == "Make"
    assert contracts[("PLUGIN-PRESET", "make")].label == "From plugin"
    assert "photo" in contracts[("PLUGIN-PRESET", "make")].inputs


def test_a_derived_contract_passes_the_fit_check_against_its_own_preset(root):
    write_preset(root, [media_field("photo", "image"), STEPPER])
    loader = load(root)
    contracts = derived_contracts(loader.presets)
    catalog = installed_catalog(FilePresetRepository(loader), [PRESET_ID])

    assert lint_contract(PRESET_ID, "make", contracts[(PRESET_ID, "make")], catalog) == []


def test_sentence_case_keeps_acronyms():
    assert sentence_case("Source Image") == "Source image"
    assert sentence_case("  HD  video ") == "HD video"


def test_the_shipped_tree_lists_the_modes_that_declare_image_or_video_outputs(marketplace):
    derived = derived_contracts(marketplace.presets)

    assert set(EXPECTED) <= {f"{preset}|{mode}" for preset, mode in derived}
    assert len(derived) > len(EXPECTED)
    for contract in derived.values():
        assert isinstance(contract, ModeContract)
        assert all(port.media_type in ("image", "video") for port in [*contract.inputs.values(), *contract.outputs.values()])


def test_the_picker_endpoint_lists_derived_modes(root, port):
    write_preset(root, [media_field("photo", "image", "Photo")])
    loader = load(root)
    catalog = installed_catalog(FilePresetRepository(loader), [PRESET_ID])
    components = make_components(port, catalog=catalog, contracts=derived_contracts(loader.presets))
    client = make_client(components, lambda: make_user("alice"))

    body = client.get("/api/elixirs/presets").json()

    assert set(body) == {"presets"}
    assert [(item["preset_id"], item["mode"], item["mode_label"]) for item in body["presets"]] == [(PRESET_ID, "make", "Make")]
    assert body["presets"][0]["inputs"]["photo"]["label"] == "Photo"


def lint_issues(root):
    return [i for i in PresetLinter([str(root)]).lint() if "elixir" in i.message]


def test_lint_reports_underivable_modes_as_info(root):
    write_preset(root, [media_field("voice", "audio")])

    issues = lint_issues(root)

    assert [i.level for i in issues] == ["info"]
    assert "modes/make: not offered in elixirs" in issues[0].message and "voice" in issues[0].message


def test_lint_is_quiet_for_derivable_and_disabled_modes(root, tmp_path):
    write_preset(root, [media_field("photo", "image")])
    write_preset(tmp_path / "other", [media_field("voice", "audio")], preset_id="OFF", override={"enabled": False})

    assert lint_issues(root) == []
    assert lint_issues(tmp_path / "other") == []


def test_lint_reports_an_invalid_override_as_an_error(root):
    write_preset(root, [], override="schema_version: 1\nnope: 1\n")

    issues = lint_issues(root)

    assert [i.level for i in issues] == ["error"]
    assert "modes/make/elixir.yml" in issues[0].message


def test_lint_reports_an_override_target_the_form_does_not_have(root):
    write_preset(
        root,
        [],
        override={"inputs": {"photo": {"label": "Photo", "media_type": "image", "target": {"kind": "form_field", "field": "nope"}}}},
    )

    issues = lint_issues(root)

    assert [i.level for i in issues] == ["error"]
    assert "no nope field" in issues[0].message


def test_contracts_are_memoized_until_a_template_reload_or_an_override_change(root):
    import os

    from src.features.elixirs.derivation import ContractCache

    write_preset(root, [media_field("photo", "image")], override={"label": "One"})
    loader = load(root)
    cache = ContractCache(loader.all)

    first = cache()
    assert cache() is first
    assert first[(PRESET_ID, "make")].label == "One"

    path = root / PRESET_ID / "modes" / "make" / "elixir.yml"
    path.write_text(yaml.safe_dump({"schema_version": 1, "label": "Two"}), encoding="utf-8")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))
    second = cache()
    assert second is not first and second[(PRESET_ID, "make")].label == "Two"
    assert cache() is second

    path.unlink()
    third = cache()
    assert third is not second and third[(PRESET_ID, "make")].label == "Make"

    loader.reload()
    fourth = cache()
    assert fourth is not third and fourth == third
    assert cache() is fourth


DIRECTOR_VARS = {"vars": {"video_director": {"preset_modes": ["make"]}}}


def test_director_mode_media_fields_hidden_under_the_director_are_not_inputs(root):
    write_preset(
        root,
        [
            media_field("first_frame", "image", hidden_when_video_director=True),
            media_field("ref", "image"),
        ],
        extra_manifest=DIRECTOR_VARS,
    )

    assert list(result_of(root).contract.inputs) == ["ref"]


def test_hidden_when_video_director_fields_stay_inputs_outside_director_modes(root):
    write_preset(root, [media_field("first_frame", "image", hidden_when_video_director=True)])

    assert list(result_of(root).contract.inputs) == ["first_frame"]


@pytest.mark.parametrize(
    "fields, expected",
    [
        ([{**STEPPER, "name": "count"}], "count"),
        ([{**STEPPER, "name": "count"}, {**STEPPER, "name": "extra"}], None),
        ([STEPPER, {**STEPPER, "name": "extra"}], "quantity"),
        ([{"name": "quantity", "type": "string"}], None),
    ],
)
def test_the_quantity_field_is_the_single_numeric_form_field_the_pipeline_reads(root, fields, expected):
    write_preset(root, fields)
    preset_dir = root / PRESET_ID / "modes" / "make" / "pipeline.yml"
    pipeline = yaml.safe_load(preset_dir.read_text(encoding="utf-8"))
    refs = " ".join("{{ form.%s }}" % f["name"] for f in fields)
    pipeline["pipeline"][0]["configuration"]["quantity"] = refs
    preset_dir.write_text(yaml.safe_dump(pipeline), encoding="utf-8")

    assert result_of(root).contract.quantity_field == expected


def test_different_media_types_for_one_field_across_forms_are_not_derivable(root):
    write_preset(root, [media_field("src", "image")])
    mode_dir = root / PRESET_ID / "modes" / "make"
    (mode_dir / "form.yml").write_text(
        yaml.safe_dump(
            {
                "name": "custom",
                "fields": [
                    media_field("src", "image"),
                    {"type": "group", "children": [media_field("src", "video")]},
                ],
            }
        ),
        encoding="utf-8",
    )

    result = result_of(root)

    assert result.contract is None
    assert any("different types" in problem for problem in result.problems)


def test_a_field_name_that_is_not_a_port_name_is_reported_not_listed(root):
    write_preset(root, [media_field("Bad-Name", "image")])

    result = result_of(root)

    assert result.contract is None
    assert any("Bad-Name cannot be a port name" in problem for problem in result.problems)


def test_an_inputs_override_clears_input_problems_but_keeps_output_problems(root):
    inputs_patch = {"voice": {"label": "Voice", "media_type": "video", "target": {"kind": "form_field", "field": "voice"}}}
    write_preset(root, [media_field("voice", "audio")], pipes=[], override={"inputs": inputs_patch})

    result = result_of(root)

    assert result.contract is None
    assert result.problems and all("final gallery" in problem for problem in result.problems)


def test_an_override_that_does_not_touch_ports_keeps_derivation_problems(root):
    write_preset(root, [media_field("voice", "audio")], override={"label": "Voice"})

    result = result_of(root)

    assert result.contract is None
    assert any("voice is a audio picker" in problem for problem in result.problems)


def test_an_override_can_force_a_prompt_on_a_promptless_mode(root):
    write_preset(root, [], extra_manifest={"vars": {"promptless_modes": ["make"]}}, override={"prompt": True})

    assert result_of(root).contract.prompt is True


def test_a_mode_contributed_by_a_plugin_ignores_its_override_file(root):
    write_preset(root, [], override={"label": "Ignored"})
    loader = load(root)
    template = loader.get(PRESET_ID)
    template.modes["make"].source_plugin = "some-plugin"

    result = preset_results(template)[0]

    assert not result.overridden
    assert result.contract.label == "Make"


def test_the_cache_follows_the_template_set(root):
    from src.features.elixirs.derivation import ContractCache

    write_preset(root, [])
    loader = load(root)
    templates = [loader.get(PRESET_ID)]
    cache = ContractCache(lambda: templates)

    first = cache()
    assert set(first) == {(PRESET_ID, "make")}

    templates.clear()
    assert cache() == {}
