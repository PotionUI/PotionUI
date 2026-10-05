import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin.list_models_tool import ListModelsTool, SearchModelsTool
from src.features.llm.tools.builtin.model_info_tool import GetModelInfoTool
from src.features.models.access_policy import ModelAccessPolicy
from src.features.models.catalog import ModelCatalog
from src.features.models.records import Model, ModelInfo
from src.features.models.repository import ModelRepository
from tests.conftest import TestDatabase


class _Safety:
    def __init__(self, restricted_users):
        self.restricted_users = set(restricted_users)

    def is_restricted(self, user_id):
        return user_id in self.restricted_users


@pytest.fixture
def scratch_db():
    test_database = TestDatabase.from_template()
    with patch("src.platform.database.database.db", test_database), \
         patch("src.platform.database.migration_runner.db", test_database):
        yield test_database
    test_database.close()


@pytest.fixture
def models(scratch_db):
    with scratch_db.get_cursor() as cursor:
        for user_id, account_type in (("user-1", "USER"), ("user-2", "USER"), ("admin-1", "ADMIN")):
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash, account_type) VALUES (?, ?, ?, ?, ?)",
                (user_id, user_id, f"{user_id}@x.com", "hash", account_type),
            )
    repo = ModelRepository()
    lora = repo.create(Model(filename="fox_style.safetensors", file_size=1, sha256="a" * 64, model_type="lora"))
    flagged = repo.create(Model(filename="spicy.safetensors", file_size=1, sha256="b" * 64, model_type="lora"))
    checkpoint = repo.create(Model(filename="base.safetensors", file_size=1, sha256="c" * 64, model_type="checkpoint"))
    repo.update_model_metadata(lora.id, {"triggers": ["foxstyle"]})
    repo.create_provider(ModelInfo(model_id=flagged.id, provider="civitai", name="Spicy", nsfw=True))
    with scratch_db.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO model_header_verdicts (sha256, format, status, model_type, family, registry_fingerprint, classified_at) "
            "VALUES (?, 'safetensors', 'decided', 'lora', 'sdxl', 'fp', '2026-10-05T00:00:00Z')",
            ("a" * 64,),
        )
    for model in (lora, flagged, checkpoint):
        repo.assign_model_to_user(model.id, "user-1")
    access = ModelAccessPolicy(repo)
    manager = SimpleNamespace(
        model_repo=repo, access=access, catalog=ModelCatalog(repo, access, scanner=None),
        locator=SimpleNamespace(model_for_path=lambda path: None),
    )
    return SimpleNamespace(repo=repo, manager=manager, lora=lora, flagged=flagged, checkpoint=checkpoint)


def _ctx(models, user_id="user-1", restricted=(), is_admin=False):
    return ToolContext(
        user_id=user_id, is_admin=is_admin, model_index_manager=models.manager,
        content_safety=_Safety(restricted), chat_session=False,
    )


@pytest.mark.asyncio
async def test_search_models_returns_ids_types_family_and_trigger_words(models):
    result = await SearchModelsTool().execute(_ctx(models), query="fox")

    payload = json.loads(result.data)
    assert result.success, result.error
    assert payload["models"] == [{
        "id": models.lora.id,
        "filename": "fox_style.safetensors",
        "name": models.lora.display_name,
        "type": "lora",
        "base_model": "sdxl",
        "trigger_words": ["foxstyle"],
    }]


@pytest.mark.asyncio
async def test_search_models_filters_by_type_and_pages(models):
    first = json.loads((await SearchModelsTool().execute(_ctx(models), type="lora", limit=1)).data)
    second = json.loads((await SearchModelsTool().execute(_ctx(models), type="lora", limit=1, offset=1)).data)

    assert first["has_more"] is True
    assert second["has_more"] is False
    assert {m["id"] for m in first["models"] + second["models"]} == {models.lora.id, models.flagged.id}


@pytest.mark.asyncio
async def test_search_models_hides_unassigned_models_from_another_user(models):
    payload = json.loads((await SearchModelsTool().execute(_ctx(models, user_id="user-2"))).data)

    assert payload["models"] == []
    assert "admin" in payload["message"]


@pytest.mark.asyncio
async def test_a_restricted_viewer_never_sees_a_model_its_provider_flags_nsfw(models):
    open_ids = {m["id"] for m in json.loads((await SearchModelsTool().execute(_ctx(models))).data)["models"]}
    restricted_ids = {
        m["id"] for m in json.loads((await SearchModelsTool().execute(_ctx(models, restricted={"user-1"}))).data)["models"]
    }
    listed_ids = {
        m["id"] for m in json.loads((await ListModelsTool().execute(_ctx(models, restricted={"user-1"}))).data)["models"]
    }

    assert models.flagged.id in open_ids
    assert models.flagged.id not in restricted_ids
    assert models.flagged.id not in listed_ids
    assert models.lora.id in restricted_ids


@pytest.mark.asyncio
async def test_get_model_info_reports_a_flagged_model_not_found_to_a_restricted_viewer(models):
    shown = await GetModelInfoTool().execute(_ctx(models), model_id=models.flagged.id)
    hidden = await GetModelInfoTool().execute(_ctx(models, restricted={"user-1"}), model_id=models.flagged.id)

    assert shown.success, shown.error
    assert hidden.success is False
    assert "search_models" in hidden.error
