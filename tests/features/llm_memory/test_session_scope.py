import pytest
from unittest.mock import MagicMock

from src.features.llm_memory import operations
from src.features.llm_memory.records import LLMMemoryNote
from src.features.llm_memory.repository import LLMMemoryRepository
from tests.fixtures.persistence_base import PersistenceTestBase


def test_session_scope_requires_scope_ref():
    repo = MagicMock()

    with pytest.raises(ValueError, match="scope_ref is required"):
        operations.write_note(repo, user_id="u1", key="k", content="keeps a warm palette", scope="session")

    repo.upsert.assert_not_called()


def test_session_scope_write_passes_through():
    repo = MagicMock()
    repo.upsert.side_effect = lambda note: note

    note = operations.write_note(
        repo, user_id="u1", key="k", content="keeps a warm palette", scope="session", scope_ref="sess-1",
    )

    assert note.scope == "session"
    assert note.scope_ref == "sess-1"


def test_bulk_delete_rejects_global_or_missing_ref():
    repo = MagicMock()

    with pytest.raises(ValueError):
        operations.delete_notes_for_scope_ref(repo, user_id="u1", scope="global", scope_ref="x")
    with pytest.raises(ValueError):
        operations.delete_notes_for_scope_ref(repo, user_id="u1", scope="session", scope_ref="")

    repo.delete_by_scope_ref.assert_not_called()


class TestDeleteByScopeRef(PersistenceTestBase):
    def setUp(self):
        super().setUp()
        self.repo = LLMMemoryRepository()
        self.user = self.create_test_user()
        self.other = self.create_test_user(user_id="other_user", username="other", email="other@example.com")
        for user, key, scope, ref in [
            (self.user, "a", "session", "sess-1"),
            (self.user, "b", "session", "sess-1"),
            (self.user, "c", "session", "sess-2"),
            (self.user, "d", "preset", "sess-1"),
            (self.other, "e", "session", "sess-1"),
        ]:
            self.repo.upsert(LLMMemoryNote(user_id=user, key=key, content="note text", scope=scope, scope_ref=ref))

    def _keys(self, user):
        return sorted(n.key for n in self.repo.list_notes(user_id=user))

    def test_deletes_only_matching_user_scope_and_ref(self):
        removed = operations.delete_notes_for_scope_ref(self.repo, user_id=self.user, scope="session", scope_ref="sess-1")

        assert removed == 2
        assert self._keys(self.user) == ["c", "d"]
        assert self._keys(self.other) == ["e"]
