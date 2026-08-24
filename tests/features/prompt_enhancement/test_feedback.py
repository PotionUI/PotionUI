"""Tests for the enhancement feedback learning loop."""

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.features.prompt_enhancement.manager import PromptEnhancementManager


def make_manager(prompt_db=None, feedback_repo=None):
    return PromptEnhancementManager(
        llm_service=MagicMock(),
        prompt_database_manager=prompt_db,
        feedback_repository=feedback_repo,
    )


def make_feedback_repo():
    repo = MagicMock()
    repo.create.return_value = SimpleNamespace(id="fb-1")
    return repo


class TestRecordFeedback:
    @pytest.mark.asyncio
    async def test_approved_saves_prompt_to_library(self):
        prompt_db = MagicMock()
        prompt_db.add_prompt = AsyncMock(return_value=SimpleNamespace(id="mp-1"))
        feedback_repo = make_feedback_repo()
        manager = make_manager(prompt_db, feedback_repo)

        result = await manager.record_feedback(
            user_id="u-1", session_id="s-1", message_id="m-1",
            prompt_text="a rich prompt", verdict="approved", model_id="model-1",
        )

        prompt_db.add_prompt.assert_awaited_once_with(
            user_id="u-1",
            prompt_text="a rich prompt",
            model_id="model-1",
            source_provider="chat_approved",
            metadata={"mode": "generation"},
        )
        feedback_repo.create.assert_called_once()
        assert feedback_repo.create.call_args.kwargs["prompt_id"] == "mp-1"
        assert feedback_repo.create.call_args.kwargs["mode"] == "generation"
        assert result == {"feedback_id": "fb-1", "prompt_id": "mp-1"}

    @pytest.mark.asyncio
    async def test_mode_tags_exemplar_and_verdict(self):
        prompt_db = MagicMock()
        prompt_db.add_prompt = AsyncMock(return_value=SimpleNamespace(id="mp-1"))
        feedback_repo = make_feedback_repo()
        manager = make_manager(prompt_db, feedback_repo)

        await manager.record_feedback(
            user_id="u-1", session_id="s-1", message_id="m-1",
            prompt_text="dataset prompt", verdict="approved", mode="dataset",
        )

        assert prompt_db.add_prompt.call_args.kwargs["metadata"] == {"mode": "dataset"}
        assert feedback_repo.create.call_args.kwargs["mode"] == "dataset"

    @pytest.mark.asyncio
    async def test_rejected_does_not_touch_library(self):
        prompt_db = MagicMock()
        prompt_db.add_prompt = AsyncMock()
        feedback_repo = make_feedback_repo()
        manager = make_manager(prompt_db, feedback_repo)

        result = await manager.record_feedback(
            user_id="u-1", session_id="s-1", message_id="m-1",
            prompt_text="a bad prompt", verdict="rejected", reason="too dark",
        )

        prompt_db.add_prompt.assert_not_awaited()
        assert feedback_repo.create.call_args.kwargs["verdict"] == "rejected"
        assert feedback_repo.create.call_args.kwargs["reason"] == "too dark"
        assert result["prompt_id"] is None

    @pytest.mark.asyncio
    async def test_invalid_verdict_raises(self):
        manager = make_manager()
        with pytest.raises(ValueError):
            await manager.record_feedback(
                user_id="u-1", session_id="s-1", message_id="m-1",
                prompt_text="x", verdict="meh",
            )


class TestExemplarModeFiltering:
    @pytest.mark.asyncio
    async def test_search_prompts_filters_by_mode_post_retrieval(self):
        prompt_db = MagicMock()
        prompt_db.search = AsyncMock(return_value=[
            SimpleNamespace(id="p-gen", prompt="gen", source_url=None,
                            metadata={"mode": "generation"}),
            SimpleNamespace(id="p-legacy", prompt="legacy", source_url=None,
                            metadata={}),
            SimpleNamespace(id="p-ds", prompt="ds", source_url=None,
                            metadata={"mode": "dataset"}),
        ])
        manager = make_manager(prompt_db)

        results = await manager._search_prompts(
            "u-1", ["query"], None, limit_per_query=5, total_cap=10,
            source_provider="chat_approved", mode="generation",
        )

        # Legacy rows without a mode tag count as 'generation'
        assert [p.id for p in results] == ["p-gen", "p-legacy"]

        results_ds = await manager._search_prompts(
            "u-1", ["query"], None, limit_per_query=5, total_cap=10,
            source_provider="chat_approved", mode="dataset",
        )
        assert [p.id for p in results_ds] == ["p-ds"]

    @pytest.mark.asyncio
    async def test_search_prompts_overfetches_when_mode_filtering(self):
        prompt_db = MagicMock()
        prompt_db.search = AsyncMock(return_value=[])
        manager = make_manager(prompt_db)

        await manager._search_prompts(
            "u-1", ["query"], None, limit_per_query=4, total_cap=10, mode="generation",
        )
        assert prompt_db.search.call_args.kwargs["limit"] == 8

        await manager._search_prompts(
            "u-1", ["query"], None, limit_per_query=4, total_cap=10,
        )
        assert prompt_db.search.call_args.kwargs["limit"] == 4
