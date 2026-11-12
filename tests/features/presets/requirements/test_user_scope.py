from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.features.presets import operations
from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.requirements.builtin import register_builtin_requirement_checkers
from src.platform.plugins.requirement_checkers import requirement_checker_registry
from src.platform.security.user import AccountType

INGREDIENTS_FILE = "ltx-2.5-22b-ic-lora-ingredients-0.9.safetensors"
ALPHA_FILE = "ltx-2.5-22b-ic-lora-alpha-gen-0.9.safetensors"


def setup_module(module):
    if not requirement_checker_registry.all():
        register_builtin_requirement_checkers(requirement_checker_registry)


@pytest.fixture(scope="module")
def ltx25():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    return next(p for p in loader.presets if Path(p.path).as_posix().endswith("marketplace/LTX-2.5"))


class FakeRepo:
    def __init__(self, rows):
        self.rows = rows

    def get_by_identity(self, model_type, filename):
        return self.rows.get((model_type, filename))

    def get_by_filename(self, filename):
        return [row for (_, name), row in self.rows.items() if name == filename]

    def get_by_id(self, model_id, include_providers=False):
        return next((row for row in self.rows.values() if row.id == model_id), None)

    def get_by_sha256(self, sha256, include_providers=False):
        return None


def row(model_id, filename):
    return SimpleNamespace(id=model_id, filename=filename, model_type="lora", is_available=True)


def user(admin=False):
    return SimpleNamespace(
        id="u1", account_type=AccountType.ADMIN if admin else AccountType.USER,
        owner_account_type=AccountType.ADMIN if admin else AccountType.USER,
    )


def collaborators(template, rows, allowed, overrides=None):
    access = Mock()
    access.get_allowed_model_ids.return_value = allowed
    return SimpleNamespace(
        file_repo=SimpleNamespace(find_preset_by_id=lambda preset_id: template),
        db_repo=SimpleNamespace(get_preset_form_overrides=lambda preset_id: overrides or {}),
        model_index=SimpleNamespace(model_repo=FakeRepo(rows), digests=None, access=access),
    )


def modes(**requirements_missing):
    return {"modes": [{"name": name, "requirements_missing": missing} for name, missing in requirements_missing.items()]}


def depot():
    return {
        ("lora", INGREDIENTS_FILE): row("ing", INGREDIENTS_FILE),
        ("lora", ALPHA_FILE): row("alpha", ALPHA_FILE),
    }


def test_a_required_lora_in_the_depot_but_not_assigned_is_missing_for_the_user(ltx25):
    data = modes(ingredients=[], transparent_video=[])
    operations.apply_user_model_scope(collaborators(ltx25, depot(), ["alpha"]), "p", data, user())

    by_name = {m["name"]: m["requirements_missing"] for m in data["modes"]}
    assert by_name["ingredients"] and by_name["transparent_video"] == []


def test_a_required_lora_the_user_is_assigned_is_not_missing(ltx25):
    data = modes(ingredients=[])
    operations.apply_user_model_scope(collaborators(ltx25, depot(), ["ing"]), "p", data, user())

    assert data["modes"][0]["requirements_missing"] == []


def test_an_admin_default_the_user_is_assigned_satisfies_the_requirement(ltx25):
    overrides = {"ingredients": {"effect_lora": {"default": "my-own-name.safetensors"}}}
    rows = {**depot(), ("lora", "my-own-name.safetensors"): row("mine", "my-own-name.safetensors")}
    data = modes(ingredients=[])
    operations.apply_user_model_scope(collaborators(ltx25, rows, ["mine"], overrides), "p", data, user())

    assert data["modes"][0]["requirements_missing"] == []


def test_an_instance_level_miss_is_not_listed_twice(ltx25):
    data = modes(ingredients=None)
    operations.apply_user_model_scope(collaborators(ltx25, depot(), []), "p", data, user())
    first = list(data["modes"][0]["requirements_missing"])
    operations.apply_user_model_scope(collaborators(ltx25, depot(), []), "p", data, user())

    assert first and data["modes"][0]["requirements_missing"] == first


def test_an_admin_sees_the_instance_answer_unchanged(ltx25):
    data = modes(ingredients=[])
    operations.apply_user_model_scope(collaborators(ltx25, depot(), []), "p", data, user(admin=True))

    assert data["modes"][0]["requirements_missing"] == []
