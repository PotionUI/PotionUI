"""SearchGalleryTool: schema contract and execution over a mocked media index."""

import json
from unittest.mock import MagicMock

import pytest

from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin.search_gallery_tool import SearchGalleryTool


def make_manager(hits_by_query=None, summaries=None):
    manager = MagicMock()

    def search_gallery(user_id, query, limit=100):
        return list((hits_by_query or {}).get(query, []))

    manager.search_gallery.side_effect = search_gallery
    manager.describe_files.return_value = summaries or {}
    return manager


def make_context(manager):
    return ToolContext(user_id="user-1", media_indexer=manager)


def hit(file_id, generation_id, similarity):
    return {"file_id": file_id, "generation_id": generation_id, "similarity": similarity}


class TestSchema:
    def test_identity(self):
        tool = SearchGalleryTool()
        assert tool.name == "search_gallery"
        assert tool.modes == ["generation"]
        assert tool.group == "Generation"
        assert tool.user_description
        assert tool.hint

    def test_parameters_require_queries_array(self):
        params = SearchGalleryTool().parameters
        assert params["required"] == ["queries"]
        assert params["properties"]["queries"]["type"] == "array"
        assert params["properties"]["limit"]["type"] == "integer"

    def test_parameters_limit_description_mentions_the_ceiling(self):
        params = SearchGalleryTool().parameters
        assert str(SearchGalleryTool.MAX_LIMIT) in params["properties"]["limit"]["description"]


class TestExecute:
    @pytest.mark.asyncio
    async def test_returns_grouped_matches_with_thumbnails(self):
        manager = make_manager(
            hits_by_query={"red fox": [hit("f1", "gen-1", 0.31234)]},
            summaries={"f1": {"file_type": "IMAGE", "thumbnail": "thumbs/f1.jpg", "file_path": "g/f1.png"}},
        )
        tool = SearchGalleryTool()

        result = await tool.execute(make_context(manager), queries=["red fox"])

        assert result.success is True
        payload = json.loads(result.data)
        [group] = payload["results"]
        assert group["query"] == "red fox"
        [match] = group["matches"]
        assert match["generation_id"] == "gen-1"
        assert match["file_id"] == "f1"
        assert match["similarity"] == 0.3123
        assert match["thumbnail"] == "thumbs/f1.jpg"
        assert match["media_type"] == "IMAGE"
        manager.search_gallery.assert_called_once_with("user-1", "red fox")

    @pytest.mark.asyncio
    async def test_merges_multiple_queries_and_dedupes_generations(self):
        manager = make_manager(hits_by_query={
            "fox": [hit("f1", "gen-1", 0.3)],
            "forest": [hit("f2", "gen-1", 0.28), hit("f3", "gen-2", 0.27)],
        })
        tool = SearchGalleryTool()

        result = await tool.execute(make_context(manager), queries=["fox", "forest"])

        payload = json.loads(result.data)
        assert [g["query"] for g in payload["results"]] == ["fox", "forest"]
        assert [m["generation_id"] for m in payload["results"][0]["matches"]] == ["gen-1"]
        assert [m["generation_id"] for m in payload["results"][1]["matches"]] == ["gen-2"]

    @pytest.mark.asyncio
    async def test_limit_caps_matches_per_query(self):
        manager = make_manager(hits_by_query={
            "fox": [hit(f"f{i}", f"gen-{i}", 0.3 - i * 0.001) for i in range(10)],
        })
        tool = SearchGalleryTool()

        result = await tool.execute(make_context(manager), queries=["fox"], limit=2)

        payload = json.loads(result.data)
        assert len(payload["results"][0]["matches"]) == 2

    @pytest.mark.asyncio
    async def test_limit_is_capped_at_the_maximum_even_when_a_larger_value_is_requested(self):
        manager = make_manager(hits_by_query={
            "fox": [hit(f"f{i}", f"gen-{i}", 0.3 - i * 0.001) for i in range(30)],
        })
        tool = SearchGalleryTool()

        result = await tool.execute(make_context(manager), queries=["fox"], limit=500)

        payload = json.loads(result.data)
        assert len(payload["results"][0]["matches"]) == SearchGalleryTool.MAX_LIMIT

    @pytest.mark.asyncio
    async def test_no_matches_yields_helpful_message(self):
        tool = SearchGalleryTool()

        result = await tool.execute(make_context(make_manager()), queries=["fox"])

        payload = json.loads(result.data)
        assert "message" in payload

    @pytest.mark.asyncio
    async def test_missing_queries_errors(self):
        tool = SearchGalleryTool()

        result = await tool.execute(make_context(make_manager()))

        assert result.success is False
        assert "queries" in result.error

    @pytest.mark.asyncio
    async def test_single_query_string_is_tolerated(self):
        manager = make_manager(hits_by_query={"fox": [hit("f1", "gen-1", 0.3)]})
        tool = SearchGalleryTool()

        result = await tool.execute(make_context(manager), queries="fox")

        assert result.success is True
        assert json.loads(result.data)["results"][0]["query"] == "fox"

    @pytest.mark.asyncio
    async def test_missing_manager_errors(self):
        tool = SearchGalleryTool()

        result = await tool.execute(make_context(None), queries=["fox"])

        assert result.success is False
        assert "not available" in result.error

    @pytest.mark.asyncio
    async def test_query_count_is_bounded(self):
        manager = make_manager()
        tool = SearchGalleryTool()

        result = await tool.execute(
            make_context(manager), queries=[f"q{i}" for i in range(10)]
        )

        payload = json.loads(result.data)
        assert len(payload["results"]) == SearchGalleryTool.MAX_QUERIES
        assert "truncated" in payload


def make_history(generations_by_query):
    history = MagicMock()

    async def get_history_async(**filters):
        return {"generations": list(generations_by_query.get(filters["search"], [])), "total": 0}

    history.get_history_async.side_effect = get_history_async
    return history


def generation(gen_id, prompt, files):
    return {"id": gen_id, "form_data": {"prompt": prompt}, "created_at": "2026-10-05T00:00:00Z", "files": files}


class TestTextFallback:
    @pytest.mark.asyncio
    async def test_without_vision_weights_it_searches_prompts_and_says_visual_search_is_off(self):
        manager = make_manager()
        manager.vision_embedder.is_available.return_value = False
        history = make_history({"fox": [generation("gen-1", "a red fox", [
            {"id": "f0", "file_path": "g/f0.png", "file_type": "IMAGE", "is_final": False},
            {"id": "f1", "file_path": "g/f1.png", "file_type": "IMAGE", "is_final": True, "thumbnail_medium": "t/f1.jpg"},
        ])]})
        context = ToolContext(user_id="user-1", media_indexer=manager, generation_history_facade=history)

        result = await SearchGalleryTool().execute(context, queries=["fox"])

        payload = json.loads(result.data)
        assert result.success
        assert payload["search_mode"] == "text"
        assert "vision embedder" in payload["note"]
        assert payload["results"][0]["matches"] == [{
            "generation_id": "gen-1", "file_id": "f1", "media_type": "IMAGE", "path": "g/f1.png",
            "thumbnail": "t/f1.jpg", "prompt": "a red fox", "created_at": "2026-10-05T00:00:00Z",
        }]
        manager.search_gallery.assert_not_called()
        history.get_history_async.assert_called_once_with(
            user_id="user-1", search="fox", status="completed", limit=5, offset=0, include_tags=False,
        )

    @pytest.mark.asyncio
    async def test_a_failing_visual_search_falls_back_without_leaking_the_error(self):
        manager = make_manager()
        manager.search_gallery.side_effect = RuntimeError("weights not found under /srv/models/siglip")
        history = make_history({})
        context = ToolContext(user_id="user-1", media_indexer=manager, generation_history_facade=history)

        result = await SearchGalleryTool().execute(context, queries=["fox"])

        assert result.success
        assert json.loads(result.data)["search_mode"] == "text"
        assert "/srv/" not in result.data

    @pytest.mark.asyncio
    async def test_files_hidden_from_the_viewer_are_never_returned(self):
        manager = make_manager()
        manager.vision_embedder.is_available.return_value = False
        history = make_history({"fox": [generation("gen-1", "a fox", [{"id": "f1", "file_type": "IMAGE", "content_state": "unrated"}])]})
        context = ToolContext(user_id="user-1", media_indexer=manager, generation_history_facade=history)

        payload = json.loads((await SearchGalleryTool().execute(context, queries=["fox"])).data)

        assert payload["results"][0]["matches"] == []

    @pytest.mark.asyncio
    async def test_without_weights_or_history_it_explains_why(self):
        manager = make_manager()
        manager.vision_embedder.is_available.return_value = False

        result = await SearchGalleryTool().execute(make_context(manager), queries=["fox"])

        assert result.success is False
        assert "vision embedder" in result.error
