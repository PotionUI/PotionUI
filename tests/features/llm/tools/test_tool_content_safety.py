import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin.manage_prompts_tool import (
    DeletePromptTool, EditPromptTool, GetPromptTool, ListPromptsTool,
)
from src.features.llm.tools.builtin.organize_gallery_tool import OrganizeGalleryTool
from src.features.llm.tools.builtin.search_gallery_tool import SearchGalleryTool
from src.features.llm.tools.builtin.search_prompts_tool import SearchModelPromptsTool
from src.features.prompt_database.records import Prompt
from src.features.prompt_database.repository import PromptRepository
from src.features.segments.dto import RichSegment
from tests.conftest import TestDatabase
from tests.features.generation.test_history_query_content_safety import ContentHistoryBase


class _Safety:
    def __init__(self, restricted):
        self.restricted = restricted

    def is_restricted(self, user_id):
        return self.restricted


class _NoEmbeddings:
    async def is_available(self):
        return False


@pytest.fixture
def prompts():
    test_database = TestDatabase.from_template()
    with patch("src.platform.database.database.db", test_database), \
         patch("src.platform.database.migration_runner.db", test_database):
        with test_database.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash) VALUES ('user-1', 'u1', 'u1@x.com', 'x')"
            )
        repository = PromptRepository()
        safe = repository.create(Prompt(user_id="user-1", name="Safe fox", segments=[RichSegment(content="a fox in snow")]))
        spicy = repository.create(Prompt(
            user_id="user-1", name="Spicy fox", segments=[RichSegment(content="a fox, explicit")], nsfw=True,
        ))
        yield SimpleNamespace(repository=repository, safe=safe, spicy=spicy)
    test_database.close()


def _prompt_ctx(prompts, restricted):
    safety = _Safety(restricted)
    database = SimpleNamespace(
        repository=prompts.repository, embedding_provider=_NoEmbeddings(), vector_store=None, content_safety=safety,
    )
    return ToolContext(user_id="user-1", prompt_database=database, content_safety=safety, chat_session=False)


@pytest.mark.asyncio
async def test_get_prompt_hides_an_nsfw_prompt_from_a_restricted_viewer_only(prompts):
    hidden = await GetPromptTool().execute(_prompt_ctx(prompts, True), prompt_id=prompts.spicy.id)
    shown = await GetPromptTool().execute(_prompt_ctx(prompts, False), prompt_id=prompts.spicy.id)
    safe = await GetPromptTool().execute(_prompt_ctx(prompts, True), prompt_id=prompts.safe.id)

    assert hidden.success is False and "explicit" not in (hidden.error or "")
    assert shown.success and safe.success


@pytest.mark.asyncio
async def test_list_prompts_never_lists_an_nsfw_prompt_to_a_restricted_viewer(prompts):
    payload = json.loads((await ListPromptsTool().execute(_prompt_ctx(prompts, True))).data)

    assert [p["id"] for p in payload["prompts"]] == [prompts.safe.id]


@pytest.mark.asyncio
async def test_search_model_prompts_never_returns_an_nsfw_prompt_to_a_restricted_viewer(prompts):
    restricted = json.loads((await SearchModelPromptsTool().execute(_prompt_ctx(prompts, True), queries=["fox"])).data)
    open_ = json.loads((await SearchModelPromptsTool().execute(_prompt_ctx(prompts, False), queries=["fox"])).data)

    assert {p["prompt_id"] for p in restricted["results"][0]["prompts"]} == {prompts.safe.id}
    assert prompts.spicy.id in {p["prompt_id"] for p in open_["results"][0]["prompts"]}


@pytest.mark.asyncio
async def test_edit_and_delete_cannot_reach_an_nsfw_prompt_for_a_restricted_viewer(prompts):
    context = _prompt_ctx(prompts, True)

    preview = await EditPromptTool().execute(context, prompt_id=prompts.spicy.id, name="renamed")
    edited = await EditPromptTool().execute_confirmed(context, prompt_id=prompts.spicy.id, name="renamed")
    deleted = await DeletePromptTool().execute_confirmed(context, prompt_id=prompts.spicy.id)

    assert preview.success is False and edited.success is False and deleted.success is False
    survivor = prompts.repository.get_by_id(prompts.spicy.id, "user-1")
    assert survivor is not None and survivor.name == "Spicy fox"


class _HistoryFacade:
    def __init__(self, query):
        self.query = query

    def get_history(self, **filters):
        return self.query.get_history(**filters)

    async def get_history_async(self, **filters):
        return self.query.get_history(**filters)

    def get_by_id(self, generation_id, user_id, include_files=True):
        return self.query.get_by_id(generation_id, user_id, include_files)


class TestGenerationToolsForARestrictedViewer(ContentHistoryBase):
    def _seed(self):
        query = self.build_query()
        for gen_id, file_id, score in (("safe-gen", "safe-file", 0.05), ("flagged-gen", "flagged-file", 0.95)):
            self.generation_with(gen_id, file_id, score)
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE generations SET status = 'completed'")
        return ToolContext(
            user_id=self.user_id, generation_history_facade=_HistoryFacade(query), chat_session=False,
        )

    def test_search_gallery_text_search_never_returns_a_flagged_generation(self):
        context = self._seed()

        result = asyncio.run(SearchGalleryTool().execute(context, queries=["test"]))

        matches = json.loads(result.data)["results"][0]["matches"]
        assert [m["generation_id"] for m in matches] == ["safe-gen"]
        assert "flagged-file" not in result.data

    def test_organize_gallery_never_lists_or_opens_a_flagged_generation(self):
        context = self._seed()

        listed = asyncio.run(OrganizeGalleryTool().execute(context, operation="list_recent"))
        opened = asyncio.run(OrganizeGalleryTool().execute(context, operation="get", generation_id="flagged-gen"))

        assert [g["id"] for g in json.loads(listed.data)["generations"]] == ["safe-gen"]
        assert opened.success is False
        assert "flagged-file" not in (opened.data or "") + (opened.error or "")


class TestVisualGallerySearchForARestrictedViewer:
    def test_only_safe_files_come_back_from_the_visual_index(self):
        safe_path = "generations/g/f1.png"
        indexer = SimpleNamespace(
            vision_embedder=SimpleNamespace(is_available=lambda: True),
            describe_files=lambda ids: {"f1": {"file_path": safe_path, "file_type": "IMAGE"}},
        )
        from src.features.media_index.indexer import MediaIndexer

        real = MediaIndexer.__new__(MediaIndexer)
        real.content_safety = SimpleNamespace(
            is_restricted=lambda user_id: True,
            filter_paths=lambda user_id, paths: {safe_path} & set(paths),
        )
        real.repository = SimpleNamespace(file_summaries=lambda ids: {
            "f1": {"file_path": safe_path}, "f2": {"file_path": "generations/g/f2.png"},
        })
        hits = [
            {"file_id": "f1", "generation_id": "g1", "similarity": 0.9},
            {"file_id": "f2", "generation_id": "g2", "similarity": 0.8},
        ]
        indexer.search_gallery = lambda user_id, query: real._viewable_hits(user_id, hits)

        result = asyncio.run(SearchGalleryTool().execute(
            ToolContext(user_id="user-1", media_indexer=indexer, chat_session=False), queries=["fox"],
        ))

        payload = json.loads(result.data)
        assert payload["search_mode"] == "visual"
        assert [m["file_id"] for m in payload["results"][0]["matches"]] == ["f1"]
