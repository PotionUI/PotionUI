"""Content validation truth table for LLMMemoryManager.write_note.

Seeds and generation ids describe one generation, not a lasting pattern, so
they are rejected regardless of scope. A bare parameter dump is only rejected
at global scope - model/preset scopes exist to carry parameter values.
"""

import pytest
from unittest.mock import MagicMock

from src.features.llm_memory.manager import LLMMemoryManager
from src.features.llm_memory.records import LLMMemoryNote


def make_manager():
    repo = MagicMock()
    return LLMMemoryManager(repository=repo), repo


class TestSeedRejection:
    def test_global_rejects_seed_with_teaching_message(self):
        manager, repo = make_manager()

        with pytest.raises(ValueError, match="one generation"):
            manager.write_note(user_id="u1", key="k", content="generated a castle at seed 1234")

        repo.upsert.assert_not_called()

    def test_model_scope_still_rejects_seed(self):
        manager, repo = make_manager()

        with pytest.raises(ValueError, match="one generation"):
            manager.write_note(
                user_id="u1", key="k", content="works best with seed 998877",
                scope="model", scope_ref="model-1",
            )

        repo.upsert.assert_not_called()

    def test_preset_scope_still_rejects_seed(self):
        manager, repo = make_manager()

        with pytest.raises(ValueError, match="one generation"):
            manager.write_note(
                user_id="u1", key="k", content="seed: 42424242 gave a good result",
                scope="preset", scope_ref="preset-1",
            )

        repo.upsert.assert_not_called()


class TestGenerationIdRejection:
    def test_global_rejects_ulid_content(self):
        manager, repo = make_manager()

        with pytest.raises(ValueError, match="one generation"):
            manager.write_note(
                user_id="u1", key="k",
                content="the good one was 01ARZ3NDEKTSV4RRFFQ69G5FAV",
            )

        repo.upsert.assert_not_called()

    def test_model_scope_rejects_ulid_content(self):
        manager, repo = make_manager()

        with pytest.raises(ValueError, match="one generation"):
            manager.write_note(
                user_id="u1", key="k",
                content="reference generation 01ARZ3NDEKTSV4RRFFQ69G5FAV was great",
                scope="model", scope_ref="model-1",
            )

        repo.upsert.assert_not_called()


class TestParamDumpScopeAware:
    def test_global_rejects_bare_param_dump(self):
        manager, repo = make_manager()

        with pytest.raises(ValueError, match="one generation"):
            manager.write_note(user_id="u1", key="k", content="cfg 7, steps 30")

        repo.upsert.assert_not_called()

    def test_model_scope_allows_cfg_values(self):
        manager, repo = make_manager()
        note = LLMMemoryNote(id="n1", user_id="u1", key="k", content="responds well to cfg 3.5 with short prompts", scope="model", scope_ref="model-1")
        repo.upsert.return_value = note

        result = manager.write_note(
            user_id="u1", key="k",
            content="responds well to cfg 3.5 with short prompts",
            scope="model", scope_ref="model-1",
        )

        assert result == note
        repo.upsert.assert_called_once()

    def test_preset_scope_allows_param_dump(self):
        manager, repo = make_manager()
        note = LLMMemoryNote(id="n1", user_id="u1", key="k", content="cfg 7, steps 30", scope="preset", scope_ref="preset-1")
        repo.upsert.return_value = note

        result = manager.write_note(
            user_id="u1", key="k", content="cfg 7, steps 30",
            scope="preset", scope_ref="preset-1",
        )

        assert result == note
        repo.upsert.assert_called_once()


class TestProseAlwaysPasses:
    def test_global_prose_about_style_passes(self):
        manager, repo = make_manager()
        note = LLMMemoryNote(id="n1", user_id="u1", key="k", content="prefers painterly fantasy scenes, dislikes photorealism")
        repo.upsert.return_value = note

        result = manager.write_note(
            user_id="u1", key="k",
            content="prefers painterly fantasy scenes, dislikes photorealism",
        )

        assert result == note
        repo.upsert.assert_called_once()

    def test_prose_mentioning_one_param_keyword_passes(self):
        """A single incidental parameter mention isn't a dump - only two-or-more
        with little surrounding prose trips the global-scope check."""
        manager, repo = make_manager()
        content = "cfg 7 with painterly digital art, warm color and dramatic lighting"
        note = LLMMemoryNote(id="n1", user_id="u1", key="k", content=content)
        repo.upsert.return_value = note

        result = manager.write_note(user_id="u1", key="k", content=content)

        assert result == note
        repo.upsert.assert_called_once()
