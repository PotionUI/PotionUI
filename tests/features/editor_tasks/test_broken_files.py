from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.editor_tasks.routes import build_router
from src.features.presets.linter import PresetLinter
from src.platform.security import current_user
from src.platform.security.user import AccountType, User
from tests.features.editor_tasks.support import MASK, load, write_preset
from tests.features.editor_tasks.test_manager import USER, build, entry

BROKEN = ["task: [", "- edit_region\n- other\n", "just a string\n", "42\n", "{{{{: ]]"]


@pytest.mark.parametrize("text", BROKEN)
def test_a_broken_file_is_a_load_error_and_the_preset_still_loads(tmp_path, text):
    root = tmp_path / "presets"
    root.mkdir()
    write_preset(root, text)

    loader = load(root)

    template = loader.get("EDITOR-TASKS-TEST")
    assert template is not None and "edit" in template.modes
    assert template.editor_modes == {}
    ((key, messages),) = [(k, v) for k, v in loader.load_errors.items() if "editor_modes" in k]
    assert key.endswith("edit") and messages


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

    errors = [i for i in PresetLinter([str(root)]).lint() if i.level == "error" and "editor_mode.yml" in i.message]

    assert len(errors) == 1


def contributed_plugin(tmp_path, editor_text):
    root = tmp_path / "presets"
    root.mkdir()
    write_preset(root, None, preset_id="HOST-PRESET", mode="base")
    mode_dir = tmp_path / "plugin" / "editor_modes" / "extra"
    mode_dir.mkdir(parents=True)
    (mode_dir / "editor_mode.yml").write_text(editor_text, encoding="utf-8")
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
    return [i for i in issues if "editor_mode" in i.message]


GOOD = (
    "schema_version: 1\ntask: edit_region\nfrom: base\nregion: crop\ninputs: {source: source_image}\n"
)


def test_a_good_contributed_editor_mode_is_quiet(tmp_path):
    assert [i for i in contributed_issues(tmp_path, GOOD) if i.level != "info"] == []


@pytest.mark.parametrize(
    "text, message",
    [
        ("task: [", "editor_mode.yml"),
        ("schema_version: 2\n", "schema_version"),
        (GOOD.replace("source_image", "nope"), "not a field of the mode"),
        (GOOD.replace("edit_region", "other.colorize"), "cannot be offered by an editor mode of plugin 'shiny'"),
        (GOOD.replace("from: base", "from: gone"), "gone"),
    ],
)
def test_a_broken_contributed_editor_mode_is_a_lint_error(tmp_path, text, message):
    errors = [i for i in contributed_issues(tmp_path, text) if i.level == "error"]

    assert errors and any(message in i.message for i in errors), [i.message for i in errors]
    assert "plugin 'shiny' preset_modes" in errors[0].preset_path
    assert "HOST-PRESET" in errors[0].preset_path
