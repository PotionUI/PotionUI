"""Tests for MemoryCompactor: threshold gating, merge+delete application,
malformed/suspicious-output safety rails, and the per-user concurrency guard."""

import asyncio
import json

import pytest
from unittest.mock import AsyncMock, Mock

from src.features.chat.memory_compaction import (
    COMPACTION_THRESHOLD,
    MemoryCompactor,
)
from src.features.llm_memory.records import LLMMemoryNote


def _note(key: str, content: str, scope: str = "global", scope_ref=None, note_id=None) -> LLMMemoryNote:
    return LLMMemoryNote(
        id=note_id or f"id-{key}", user_id="user-1", key=key, content=content,
        scope=scope, scope_ref=scope_ref,
    )


def _group(count: int, scope: str = "global", scope_ref=None) -> list:
    return [_note(f"note_{i}", f"fact number {i}", scope=scope, scope_ref=scope_ref) for i in range(count)]


def _manager(monkeypatch, notes: list) -> Mock:
    """`memory_operations` (as imported into memory_compaction.py) is patched
    to a fresh Mock, exposed as `manager.memory_ops`, so tests can assert on
    write_note/read_notes/delete_note calls without exercising the real
    validation logic (covered separately by
    tests/features/llm_memory/test_operations.py)."""
    manager = Mock()
    manager.llm_memory_repository = Mock()
    mock_ops = Mock()
    mock_ops.read_notes.return_value = notes
    mock_ops.delete_note.return_value = True
    monkeypatch.setattr("src.features.chat.memory_compaction.memory_operations", mock_ops)
    manager.memory_ops = mock_ops
    manager.llm_service = Mock()
    return manager


class TestThresholdGating:
    @pytest.mark.asyncio
    async def test_group_at_or_below_threshold_is_not_compacted(self, monkeypatch):
        manager = _manager(monkeypatch, _group(COMPACTION_THRESHOLD))
        manager.llm_service.generate_with_history = AsyncMock()
        compactor = MemoryCompactor(manager)

        await compactor.compact_after_reflection("user-1", "session-1", "llm-1")

        manager.llm_service.generate_with_history.assert_not_called()
        manager.memory_ops.write_note.assert_not_called()
        manager.memory_ops.delete_note.assert_not_called()

    @pytest.mark.asyncio
    async def test_group_above_threshold_triggers_compaction_call(self, monkeypatch):
        manager = _manager(monkeypatch, _group(COMPACTION_THRESHOLD + 1))
        response = Mock()
        response.content = "[]"
        manager.llm_service.generate_with_history = AsyncMock(return_value=response)
        compactor = MemoryCompactor(manager)

        await compactor.compact_after_reflection("user-1", "session-1", "llm-1")

        manager.llm_service.generate_with_history.assert_called_once()


class TestMergeApplication:
    @pytest.mark.asyncio
    async def test_merged_notes_written_and_merged_away_sources_deleted(self, monkeypatch):
        group_notes = _group(COMPACTION_THRESHOLD + 1)  # note_0 .. note_15
        manager = _manager(monkeypatch, group_notes)

        response = Mock()
        response.content = json.dumps([
            {"key": "note_0", "content": "note_0 kept as-is, still the same fact"},
            {"key": "merged_a", "content": "merged fact a covering several notes"},
            {"key": "merged_b", "content": "merged fact b covering the rest"},
        ])
        manager.llm_service.generate_with_history = AsyncMock(return_value=response)

        def write_note(repo, user_id, key, content, scope, scope_ref=None):
            return LLMMemoryNote(id=f"new-{key}", user_id=user_id, key=key, content=content, scope=scope, scope_ref=scope_ref)

        manager.memory_ops.write_note.side_effect = write_note
        compactor = MemoryCompactor(manager)

        await compactor.compact_after_reflection("user-1", "session-1", "llm-1")

        assert manager.memory_ops.write_note.call_count == 3
        # 16 source notes minus the 1 whose key ("note_0") survived in the new set.
        assert manager.memory_ops.delete_note.call_count == 15
        deleted_ids = {c.args[2] for c in manager.memory_ops.delete_note.call_args_list}
        assert "id-note_0" not in deleted_ids
        assert "id-note_1" in deleted_ids


class TestSafetyRails:
    @pytest.mark.asyncio
    async def test_malformed_llm_output_leaves_group_untouched(self, monkeypatch):
        manager = _manager(monkeypatch, _group(COMPACTION_THRESHOLD + 1))
        response = Mock()
        response.content = "not json at all, sorry"
        manager.llm_service.generate_with_history = AsyncMock(return_value=response)
        compactor = MemoryCompactor(manager)

        await compactor.compact_after_reflection("user-1", "session-1", "llm-1")

        manager.memory_ops.write_note.assert_not_called()
        manager.memory_ops.delete_note.assert_not_called()

    @pytest.mark.asyncio
    async def test_suspicious_shrinkage_is_skipped(self, monkeypatch):
        """A 16-note group collapsed to 2 notes looks like dropped facts, not a merge."""
        manager = _manager(monkeypatch, _group(COMPACTION_THRESHOLD + 1))
        response = Mock()
        response.content = json.dumps([
            {"key": "only_one", "content": "one note claiming to cover everything"},
            {"key": "only_two", "content": "a second note claiming to cover the rest"},
        ])
        manager.llm_service.generate_with_history = AsyncMock(return_value=response)
        compactor = MemoryCompactor(manager)

        await compactor.compact_after_reflection("user-1", "session-1", "llm-1")

        manager.memory_ops.write_note.assert_not_called()
        manager.memory_ops.delete_note.assert_not_called()

    @pytest.mark.asyncio
    async def test_too_many_items_is_also_rejected(self, monkeypatch):
        """More items back than the requested target is equally implausible."""
        manager = _manager(monkeypatch, _group(COMPACTION_THRESHOLD + 1))
        response = Mock()
        response.content = json.dumps([
            {"key": f"k{i}", "content": f"fact {i}"} for i in range(11)
        ])
        manager.llm_service.generate_with_history = AsyncMock(return_value=response)
        compactor = MemoryCompactor(manager)

        await compactor.compact_after_reflection("user-1", "session-1", "llm-1")

        manager.memory_ops.write_note.assert_not_called()
        manager.memory_ops.delete_note.assert_not_called()


class TestConcurrencyGuard:
    @pytest.mark.asyncio
    async def test_second_call_for_same_user_is_a_noop_while_first_is_in_flight(self, monkeypatch):
        manager = _manager(monkeypatch, _group(COMPACTION_THRESHOLD + 1))
        hang = asyncio.Event()
        calls = {"n": 0}

        async def _generate(*args, **kwargs):
            calls["n"] += 1
            await hang.wait()
            return Mock(content="[]")

        manager.llm_service.generate_with_history = AsyncMock(side_effect=_generate)
        compactor = MemoryCompactor(manager)

        task1 = asyncio.create_task(compactor.compact_after_reflection("user-1", "session-1", "llm-1"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        await compactor.compact_after_reflection("user-1", "session-1", "llm-1")

        assert calls["n"] == 1
        assert manager.memory_ops.read_notes.call_count == 1

        hang.set()
        await task1
