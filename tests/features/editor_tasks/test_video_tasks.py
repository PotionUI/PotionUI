from pathlib import Path
from types import SimpleNamespace

import pytest

from src.features.editor_tasks.manager import EditorTaskManager
from src.features.editor_tasks.registry import CORE_TASKS, TaskRegistry, register_core_tasks
from src.features.presets.editor_mode_lint import lint_editor_mode
from src.features.presets.editor_modes import EditorModeFile, validate_editor_mode_file
from src.features.presets.linter import PresetLinter
from src.features.presets.loader import PresetTemplateLoader
from tests.features.editor_tasks.support import BASE_FIELDS, CROP, write_preset

REPO_ROOT = Path(__file__).resolve().parents[3]
MARKETPLACE = REPO_ROOT / "content" / "presets" / "marketplace"
REGISTRY = register_core_tasks(TaskRegistry())
USER = SimpleNamespace(id="u1")

VIDEO_TASKS = {"extend", "remix", "refine_faces"}

EXPECTED = {
    ("LTX-2.5", "extend"): "extend",
    ("MiniMax-H3", "extend"): "extend",
    ("MiniMax-H3", "remix"): "remix",
    ("LTX-2.5", "refine_faces"): "refine_faces",
    ("MiniMax-H3", "refine_faces"): "refine_faces",
    ("MiniMax-H3-Fast", "refine_faces"): "refine_faces",
    ("MiniMax-H3-Fast", "extend"): "extend",
}

VIDEO_FIELD = {"name": "clip", "type": "video", "label": "Clip"}
RANGE = {"schema_version": 1, "task": "remix", "from": "edit", "region": "range", "inputs": {"source": "clip"}}


def need(task_id):
    found = REGISTRY.get(task_id)
    return found.prompt if found else None


def editors(task_id):
    found = REGISTRY.get(task_id)
    return found.editors if found else ("image",)


@pytest.fixture(scope="module")
def templates():
    loader = PresetTemplateLoader([str(MARKETPLACE)])
    loader.load_presets()
    return {Path(template.path).name: template for template in loader.all()}


@pytest.fixture(scope="module")
def manager(templates):
    return EditorTaskManager(
        REGISTRY, lambda: list(templates.values()), lambda user: {t.id for t in templates.values()}, lambda engine: True
    )


def test_the_video_tasks_are_registered_for_the_video_editor_only():
    assert {task.id for task in REGISTRY.list("video")} == VIDEO_TASKS
    assert {"edit_region", "remove_object", "fix_faces", "upscale"} <= {task.id for task in REGISTRY.list("image")}


def test_every_video_task_states_what_it_needs():
    found = {task.id: task for task in CORE_TASKS if "video" in task.editors}

    assert all(task.region == "none" for task in found.values())
    assert {task.id: task.range for task in found.values()} == {"extend": "optional", "remix": "optional", "refine_faces": "required"}
    assert found["extend"].result == "append" and found["extend"].audio == "result"
    assert [task.id for task in found.values() if task.resizes] == []
    assert {task.id for task in found.values() if task.result == "replace"} == {"remix", "refine_faces"}
    assert (found["remix"].prompt, found["remix"].audio) == ("required", "keep")
    assert {task.id: task.prompt for task in found.values()}["extend"] == "optional"


def test_image_tasks_take_no_range():
    assert {task.range for task in REGISTRY.list("image")} == {"none"}


@pytest.mark.parametrize("folder, name", sorted(EXPECTED))
def test_every_shipped_video_editor_mode_is_clean(templates, folder, name):
    template = templates[folder]
    mode = template.editor_modes[name]

    issues = lint_editor_mode(template, name, mode, need, editors, None)

    assert [message for level, message in issues if level != "info"] == []
    assert mode.editor.region == "range"
    assert mode.editor.task == EXPECTED[(folder, name)]
    assert mode.editor.from_mode == name
    assert mode.editor.inputs.get("mask") is None


def test_the_set_of_shipped_video_editor_modes_is_exactly_these():
    found = {
        (path.parents[2].name, path.parent.name)
        for path in MARKETPLACE.glob("*/editor_modes/*/editor_mode.yml")
        if "region: range" in path.read_text()
    }

    assert found == set(EXPECTED)


def test_each_video_mode_hides_what_the_editor_supplies(templates):
    extend = templates["LTX-2.5"].editor_modes["extend"].editor
    h3 = templates["MiniMax-H3"].editor_modes["extend"].editor

    remix = templates["MiniMax-H3"].editor_modes["remix"].editor

    assert remix.fixed == {"keep_sound": True, "max_seconds": 14} and remix.inputs["source"] == "source_video"
    assert extend.fixed == {"match_source": True, "direction": "forward"}
    assert h3.fixed == {"direction": "after"}
    assert extend.inputs["source"] == "input_video" and h3.inputs["source"] == "source_video"


def test_every_shipped_video_mode_is_offered_by_the_manager(templates, manager):
    offered = {
        (Path(entry.template.path).name, entry.name)
        for entry in manager.editor_entries()
        if entry.spec.region == "range"
    }

    assert offered == set(EXPECTED)


def test_the_video_editor_is_offered_only_video_tasks(manager):
    entries = manager.offers(USER, "video")

    assert {entry["id"] for entry in entries} == VIDEO_TASKS
    assert all(entry["editors"] == ["video"] and entry["offers"] for entry in entries)
    assert all(
        offer["region_strategy"] == "range" for entry in entries for offer in entry["offers"]
    )


def test_the_image_editor_is_offered_no_video_task(manager):
    entries = manager.offers(USER, "image")

    ids = {entry["id"] for entry in entries}
    assert {"edit_region", "remove_object", "fix_faces", "upscale"} <= ids
    assert not ids & VIDEO_TASKS
    assert all(offer["region_strategy"] != "range" for entry in entries for offer in entry["offers"])


def test_an_entry_carries_the_needs_of_the_task(manager):
    entries = {entry["id"]: entry for entry in manager.offers(USER, "video")}

    assert (entries["extend"]["range"], entries["extend"]["result"], entries["extend"]["audio"]) == ("optional", "append", "result")
    assert (entries["remix"]["prompt"], entries["remix"]["result"], entries["remix"]["audio"]) == ("required", "replace", "keep")
    assert entries["remix"]["range"] == "optional"


def test_the_extend_offers_name_every_preset_that_extends(templates, manager):
    offers = next(entry for entry in manager.offers(USER, "video") if entry["id"] == "extend")["offers"]

    assert {offer["preset_id"] for offer in offers} == {
        templates[folder].id for folder in ("LTX-2.5", "MiniMax-H3", "MiniMax-H3-Fast")
    }


@pytest.fixture
def root(tmp_path):
    folder = tmp_path / "presets"
    folder.mkdir()
    return folder


def issues_for(root):
    return [
        issue for issue in PresetLinter([str(root)]).lint() if "editor_mode" in issue.message and issue.level == "error"
    ]


def test_a_range_mode_of_a_video_task_is_accepted(root):
    write_preset(root, RANGE, fields=[VIDEO_FIELD, *BASE_FIELDS])

    assert issues_for(root) == []


def test_a_range_mode_cannot_serve_a_picture_task(root):
    write_preset(root, {**RANGE, "task": "edit_region", "inputs": {"source": "source_image"}}, fields=[VIDEO_FIELD, *BASE_FIELDS])

    assert any("region: range is for a task of the video editor" in issue.message for issue in issues_for(root))


def test_a_picture_region_cannot_serve_a_video_task(root):
    write_preset(root, {**CROP, "task": "remix", "inputs": {"source": "clip"}}, fields=[VIDEO_FIELD, *BASE_FIELDS])

    assert any("a video task takes region: range" in issue.message for issue in issues_for(root))


def test_a_range_mode_takes_no_mask():
    parsed, problems = validate_editor_mode_file(
        {**RANGE, "inputs": {"source": "clip", "mask": "clip_inpaint_mask"}}, "editor_modes/x/editor_mode.yml"
    )

    assert parsed is None
    assert any("takes no inputs.mask" in problem for problem in problems)


def test_a_range_mode_names_a_video_field(root):
    write_preset(root, {**RANGE, "inputs": {"source": "source_image"}}, fields=[VIDEO_FIELD, *BASE_FIELDS])

    assert any("not a video field" in issue.message for issue in issues_for(root))


def test_range_is_a_known_region():
    parsed, problems = validate_editor_mode_file(RANGE, "x")

    assert problems == [] and isinstance(parsed, EditorModeFile) and parsed.region == "range"


def test_remix_is_offered_by_h3_alone_with_the_cap_of_the_mode(templates, manager):
    entry = next(item for item in manager.offers(USER, "video") if item["id"] == "remix")

    assert [offer["preset_id"] for offer in entry["offers"]] == [templates["MiniMax-H3"].id]
    assert [offer["max_range_s"] for offer in entry["offers"]] == [14]
    assert templates["MiniMax-H3"].editor_modes["remix"].editor.max_range_s == 14


def test_an_offer_without_a_cap_carries_none(manager):
    entry = next(item for item in manager.offers(USER, "video") if item["id"] == "extend")

    assert [offer["max_range_s"] for offer in entry["offers"]] == [None, None, None]


def test_a_cap_is_declared_only_on_a_range_mode():
    data = {"schema_version": 1, "task": "edit_region", "region": "crop", "inputs": {"source": "x"}, "max_range_s": 3}

    parsed, problems = validate_editor_mode_file(data, "m")

    assert parsed is None and any("max_range_s" in problem for problem in problems)


def test_a_cap_must_be_positive():
    data = {**RANGE, "max_range_s": 0}

    parsed, problems = validate_editor_mode_file(data, "m")

    assert parsed is None and problems


def test_refine_faces_is_offered_by_the_three_h3_and_ltx_presets_and_needs_a_range(templates, manager):
    entry = next(item for item in manager.offers(USER, "video") if item["id"] == "refine_faces")

    assert entry["range"] == "required" and entry["prompt"] == "none"
    assert {offer["preset_id"] for offer in entry["offers"]} == {
        templates[folder].id for folder in ("LTX-2.5", "MiniMax-H3", "MiniMax-H3-Fast")
    }
