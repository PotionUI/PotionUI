"""Memory compaction: consolidates a user's memory notes once a scope group
grows too dense.

Fires from ``ChatReflectionGenerator.reflect()`` right after it persists at
least one note - never from the interactive ``write_memory`` tool path (see
``ChatReflectionGenerator._compactor``). A slow or failed compaction call must
never break the reflection pass it rides along with, so every entry point here
is best-effort and never raises.
"""

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from src.features.llm import trace_collector
from src.features.llm_memory import operations as memory_operations
from src.features.llm_memory.operations import MAX_CONTENT_LENGTH
from src.features.llm_memory.records import LLMMemoryNote

logger = logging.getLogger(__name__)

# A group is only considered for compaction once it exceeds this size, and is
# never compacted down below it.
COMPACTION_THRESHOLD = 15
COMPACTION_TARGET_MAX = 10

# Below this many surviving notes, a compaction result looks like it dropped
# facts rather than merged them - skip it and leave the group untouched.
_MIN_PLAUSIBLE_COMPACTED_NOTES = 3

_COMPACTION_PROMPT = (
    "You are compacting a user's saved memory notes. These notes are all in the "
    "same scope (about {scope_desc}), and there are too many of them.\n\n"
    "Consolidate the notes below into at most {target} notes: merge duplicates and "
    "closely related facts into single denser notes, prefer the phrasing from the "
    "most recently updated note when two notes conflict, and preserve EVERY "
    "distinct durable fact - do not drop information just to shorten the list.\n\n"
    "Each output note's content must be under 400 characters.\n\n"
    "Notes to consolidate (numbered):\n{notes_block}\n\n"
    "Reply with a JSON array only, no other text. Each item:\n"
    '{{"key": "<short snake_case identifier>", "content": "<the consolidated fact>"}}\n\n'
    "Reply with between 1 and {target} items."
)


class MemoryCompactor:
    """Consolidates dense (scope, scope_ref) note groups for one user at a time.

    Takes the owning ``ChatRuntime`` the same way ``ChatReflectionGenerator``
    does, for the same late-binding reason (see that class's docstring).
    """

    def __init__(self, manager):
        self._m = manager
        self._compacting_users: set = set()

    async def compact_after_reflection(self, user_id: str, session_id: str, llm_config_id: str) -> None:
        """Sweep every one of the user's note groups, compacting any that are over threshold.

        Guards against two compactions running concurrently for the same user
        (e.g. two sessions triggering reflection near-simultaneously) with a
        simple in-process set. Never raises.
        """
        if user_id in self._compacting_users:
            return
        self._compacting_users.add(user_id)
        try:
            await self._compact_all_groups(user_id, session_id, llm_config_id)
        except Exception as e:
            logger.warning(f"Memory compaction failed for user {user_id}: {e}")
        finally:
            self._compacting_users.discard(user_id)

    async def _compact_all_groups(self, user_id: str, session_id: str, llm_config_id: str) -> None:
        notes = memory_operations.read_notes(self._m.llm_memory_repository, user_id=user_id)
        groups: Dict[Tuple[str, Optional[str]], List[LLMMemoryNote]] = defaultdict(list)
        for note in notes:
            groups[(note.scope, note.scope_ref)].append(note)

        for (scope, scope_ref), group_notes in groups.items():
            if len(group_notes) <= COMPACTION_THRESHOLD:
                continue
            await self._compact_group(user_id, session_id, llm_config_id, scope, scope_ref, group_notes)

    async def _compact_group(
        self,
        user_id: str,
        session_id: str,
        llm_config_id: str,
        scope: str,
        scope_ref: Optional[str],
        group_notes: List[LLMMemoryNote],
    ) -> None:
        before_count = len(group_notes)
        prompt = self._build_prompt(scope, scope_ref, group_notes)

        try:
            with trace_collector.activate(session_id, user_id, purpose="memory_compaction"):
                response = await self._m.llm_service.generate_with_history(
                    messages=[{"role": "user", "content": prompt}],
                    llm_id=llm_config_id,
                    options_override={"max_tokens": 1200, "temperature": 0.2, "think": False},
                )
        except Exception as e:
            logger.info(f"Memory compaction call failed for group {scope}/{scope_ref}: {e}")
            return

        # Local import: avoids a module-level cycle (reflection.py will import
        # MemoryCompactor to construct it), reuses the exact same lenient
        # JSON-array parser and key slugifier reflection already uses.
        from src.features.chat.reflection import ChatReflectionGenerator

        items = ChatReflectionGenerator._parse_items(response.content if response else None)
        if not self._is_plausible(items):
            logger.info(
                f"Memory compaction skipped implausible result for user {user_id} "
                f"group {scope}/{scope_ref}: {before_count} notes -> {len(items)} proposed"
            )
            return

        written: List[LLMMemoryNote] = []
        for item in items:
            key = item.get("key")
            content = item.get("content")
            if not isinstance(key, str) or not key.strip() or not isinstance(content, str) or not content.strip():
                continue
            try:
                note = memory_operations.write_note(
                    self._m.llm_memory_repository,
                    user_id=user_id,
                    key=ChatReflectionGenerator._slugify(key),
                    content=content.strip()[:MAX_CONTENT_LENGTH],
                    scope=scope,
                    scope_ref=scope_ref,
                )
            except ValueError as e:
                logger.info(f"Compacted note dropped: {e}")
                continue
            written.append(note)

        if not written:
            logger.info(
                f"Memory compaction produced no valid notes for user {user_id} "
                f"group {scope}/{scope_ref}; leaving group untouched"
            )
            return

        written_keys = {n.key for n in written}
        deleted = 0
        for note in group_notes:
            if note.key in written_keys:
                continue
            if memory_operations.delete_note(self._m.llm_memory_repository, user_id, note.id):
                deleted += 1

        logger.info(
            f"Memory compaction for user {user_id} group {scope}/{scope_ref}: "
            f"{before_count} notes -> {len(written)} notes ({deleted} deleted)"
        )

    @staticmethod
    def _is_plausible(items: List[Dict[str, Any]]) -> bool:
        return _MIN_PLAUSIBLE_COMPACTED_NOTES <= len(items) <= COMPACTION_TARGET_MAX

    @staticmethod
    def _build_prompt(scope: str, scope_ref: Optional[str], group_notes: List[LLMMemoryNote]) -> str:
        scope_desc = "the user in general" if scope == "global" else f"the {scope} '{scope_ref}'"
        notes_block = "\n".join(f"{i}. [{n.key}] {n.content}" for i, n in enumerate(group_notes, start=1))
        return _COMPACTION_PROMPT.format(scope_desc=scope_desc, target=COMPACTION_TARGET_MAX, notes_block=notes_block)
