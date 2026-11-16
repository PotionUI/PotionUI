from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.editor_tasks.declarations import DECLARATION_FILE, parse_file
from src.features.editor_tasks.routes import build_router
from src.features.presets.linter import PresetLinter
from src.platform.security import current_user
from src.platform.security.user import AccountType, User
from tests.features.editor_tasks.support import MASK, load, write_preset
from tests.features.editor_tasks.test_manager import USER, build, entry

BROKEN = ["tasks: [", "- edit_region\n- other\n", "just a string\n", "42\n", "{{{{: ]]"]


@pytest.mark.parametrize("text", BROKEN)
def test_a_broken_file_carries_an_error_and_the_preset_still_loads(tmp_path, text):
    root = tmp_path / "presets"
    root.mkdir()
    preset_dir = write_preset(root, text)

    parsed = parse_file(preset_dir / "modes" / "edit" / DECLARATION_FILE)

    assert parsed.present is True
    assert parsed.declaration is None
    assert parsed.error
    assert load(root).get("EDITOR-TASKS-TEST") is not None


@pytest.mark.parametrize("text", BROKEN)
def test_a_broken_file_does_not_hide_the_other_presets_offers(tmp_path, text):
    manager = build(tmp_path, {"A-CROP": text, "B-MASK": MASK})

    offers = entry(manager.offers(USER), "edit_region")["offers"]

    assert [offer["preset_id"] for offer in offers] == ["B-MASK"]


@pytest.mark.parametrize("text", BROKEN)
def test_the_endpoint_still_answers_with_a_broken_file(tmp_path, text):
    manager = build(tmp_path, {"A-CROP": text, "B-MASK": MASK})
    app = FastAPI()
    app.include_router(build_router(SimpleNamespace(editor_tasks=manager)))
    account = User(id="u1", username="u1", email="u1@example.com", password_hash="h", account_type=AccountType.USER)
    auth = Mock()
    auth.get_user_from_token.return_value = account
    previous = (current_user._auth, current_user._key_resolver)
    current_user.set_auth(auth)
    try:
        with TestClient(app) as client:
            response = client.get("/api/editor/tasks", headers={"Authorization": "Bearer a.session.jwt"})
    finally:
        current_user._auth, current_user._key_resolver = previous

    assert response.status_code == 200
    tasks = response.json()["data"]["tasks"]
    assert [o["preset_id"] for item in tasks if item["id"] == "edit_region" for o in item["offers"]] == ["B-MASK"]


@pytest.mark.parametrize("text", BROKEN)
def test_the_lint_reports_a_broken_file(tmp_path, text):
    root = tmp_path / "presets"
    root.mkdir()
    write_preset(root, text)

    errors = [i for i in PresetLinter([str(root)]).lint() if i.level == "error" and "editor.yml" in i.message]

    assert len(errors) == 1


def contributed_plugin(tmp_path, editor_text):
    root = tmp_path / "presets"
    root.mkdir()
    write_preset(root, None, preset_id="HOST-PRESET", mode="base")
    mode_dir = tmp_path / "plugin" / "modes" / "extra"
    mode_dir.mkdir(parents=True)
    (mode_dir / "pipeline.yml").write_text("pipeline: []\n", encoding="utf-8")
    (mode_dir / "form.yml").write_text(
        'name: "custom"\nfields:\n'
        '  - {name: source_image, type: image, configuration: {allow_inpaint: true}}\n'
        "  - {name: seed, type: seed, default: -1}\n",
        encoding="utf-8",
    )
    (mode_dir / DECLARATION_FILE).write_text(editor_text, encoding="utf-8")
    manifest = SimpleNamespace(
        id="shiny",
        plugin_dir=tmp_path / "plugin",
        preset_modes=[{"target": "HOST-PRESET", "modes_root": "."}],
        presets=[],
        editor_tasks=[],
    )
    return root, manifest


def contributed_issues(tmp_path, editor_text):
    root, manifest = contributed_plugin(tmp_path, editor_text)
    issues = PresetLinter([str(root)], plugin_manifests=[manifest]).lint()
    return [i for i in issues if "editor.yml" in i.message]


def test_a_good_contributed_mode_file_is_quiet(tmp_path):
    good = (
        "schema_version: 1\ntasks:\n  - task: edit_region\n    region: mask\n"
        "    inputs: {image: source_image, mask: source_image_inpaint_mask}\n"
    )

    assert contributed_issues(tmp_path, good) == []


@pytest.mark.parametrize(
    "text, message",
    [
        ("tasks: [", "editor.yml"),
        ("schema_version: 2\n", "schema_version"),
        ("schema_version: 1\ntasks:\n  - {task: edit_region, region: crop, inputs: {image: nope}}\n", "not a field of the mode"),
        ("schema_version: 1\ntasks:\n  - {task: other.colorize, region: crop, inputs: {image: source_image}}\n", "cannot be offered by a mode of plugin 'shiny'"),
    ],
)
def test_a_broken_contributed_mode_file_is_a_lint_error(tmp_path, text, message):
    errors = [i for i in contributed_issues(tmp_path, text) if i.level == "error"]

    assert errors and any(message in i.message for i in errors), [i.message for i in errors]
    assert "plugin 'shiny' preset_modes -> 'HOST-PRESET'" in errors[0].preset_path
