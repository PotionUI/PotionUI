from pathlib import Path

import pytest

from src.features.presets.linter import PresetLinter
from tests.features.editor_tasks.support import (
    BASE_FIELDS,
    CROP,
    MASK,
    PLAIN_IMAGE,
    declaration,
    task,
    write_preset,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
MARKETPLACE = REPO_ROOT / "content" / "presets" / "marketplace"


def editor_issues(root, level=None):
    return [
        issue
        for issue in PresetLinter([str(root)]).lint()
        if "editor.yml" in issue.message and (level is None or issue.level == level)
    ]


@pytest.fixture
def root(tmp_path):
    folder = tmp_path / "presets"
    folder.mkdir()
    return folder


def test_a_good_file_is_quiet(root):
    write_preset(root, MASK)

    assert editor_issues(root) == []


def test_no_file_is_quiet(root):
    write_preset(root, None)

    assert editor_issues(root) == []


@pytest.mark.parametrize(
    "editor, message",
    [
        ({"schema_version": 2, "tasks": []}, "schema_version"),
        ({"tasks": []}, "schema_version"),
        (declaration(task(colour="red")), "colour"),
        (declaration(task(task="nonsense")), "unknown task"),
        (declaration(task(inputs={"image": "nope"})), "not a field of the mode"),
        (declaration(task(inputs={"image": "seed"})), "not an image field"),
        (declaration(task(region="mask")), "needs inputs.mask"),
        (declaration(task(region="mask", inputs={"image": "source_image", "mask": "x"})), "inputs.mask must be"),
        (declaration(task(size={"field": "seed"})), "not a resolution field"),
        (declaration(task(size={"multiple": 5})), "multiple"),
        (declaration(task(size={"area": 10})), "area"),
        (declaration(task(set={"nope": 1})), "not a field of the mode"),
        (declaration(task(set={"output_size": "huge"})), "not an option"),
        (declaration(task(seed_field="nope")), "seed_field"),
        (declaration(task(quantity_field="nope")), "quantity_field"),
        (declaration(task(owned=["nope"])), "owned"),
        (declaration(task(), task()), "declared twice"),
        ("schema_version: [", "editor.yml"),
    ],
)
def test_each_rule_reports_an_error(root, editor, message):
    write_preset(root, editor)

    errors = editor_issues(root, "error")

    assert errors and any(message in issue.message for issue in errors), [i.message for i in errors]


def test_a_mask_without_allow_inpaint_reports_an_error(root):
    write_preset(root, MASK, fields=[PLAIN_IMAGE, *BASE_FIELDS[1:]])

    assert any("allow_inpaint" in issue.message for issue in editor_issues(root, "error"))


def test_an_unregistered_plugin_task_is_a_warning(root):
    write_preset(root, declaration(task(task="shiny.colorize")))

    assert editor_issues(root, "error") == []
    assert any("not installed or enabled" in issue.message for issue in editor_issues(root, "warning"))


def test_crop_on_a_mask_mode_is_a_warning(root):
    write_preset(root, CROP)

    assert editor_issues(root, "error") == []
    assert any("region: mask usually gives a better seam" in issue.message for issue in editor_issues(root, "warning"))


def test_a_prompt_task_on_a_promptless_mode_is_a_warning(root):
    write_preset(root, CROP, extra_manifest={"vars": {"promptless_modes": ["edit"]}})

    assert any("takes none" in issue.message for issue in editor_issues(root, "warning"))


def test_the_shipped_files_have_no_errors():
    files = sorted(MARKETPLACE.glob("*/modes/*/editor.yml"))

    assert len(files) == 9
    errors = [issue.message for issue in editor_issues(MARKETPLACE, "error")]
    assert errors == []


def test_the_krea2_seam_warning_is_present():
    warnings = editor_issues(MARKETPLACE, "warning")

    assert any(
        Path(issue.preset_path).parent.name == "Krea2" and "region: crop" in issue.message for issue in warnings
    )
