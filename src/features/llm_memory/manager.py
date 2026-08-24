"""LLM memory manager for persistent cross-session notes."""

import logging
import re
from typing import List, Optional

from src.features.llm_memory.records import LLMMemoryNote
from src.features.llm_memory.repository import LLMMemoryRepository

logger = logging.getLogger(__name__)

VALID_SCOPES = {"global", "preset", "model"}

MAX_CONTENT_LENGTH = 500
_CONTENT_TOO_LONG_MESSAGE = (
    f"Memory content is limited to {MAX_CONTENT_LENGTH} characters - distill the "
    "durable fact; details belong in the conversation."
)

# Crockford base32 (no I/L/O/U), 26 chars - matches src.platform.util.ids.generate_ulid.
_ULID_RE = re.compile(r"\b[0-9A-HJKMNP-TV-Z]{26}\b", re.IGNORECASE)
_SEED_RE = re.compile(r"\bseeds?\b\s*[:=]?\s*\d{2,}", re.IGNORECASE)

# Parameter names that describe ONE generation's settings rather than a lasting
# preference. Legitimate at model/preset scope (that's what those scopes are
# for); at global scope, a note that is little more than a list of these is a
# leftover from one run, not a pattern worth remembering everywhere.
_PARAM_DUMP_KEYWORDS = (
    "cfg", "steps", "width", "height", "sampler", "scheduler",
    "guidance", "strength", "denoise", "clip skip",
)
_PARAM_TOKEN_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in _PARAM_DUMP_KEYWORDS) + r")\b\s*[:=]?\s*[\d.]+",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[a-zA-Z]{3,}")

_ONE_GENERATION_MESSAGE = (
    "this describes one generation - save the preference that applies to "
    "future ones, or scope it to the model/preset it belongs to"
)


class LLMMemoryManager:
    """Manages persistent LLM memory notes scoped globally, per-preset, or per-model."""

    def __init__(self, repository: LLMMemoryRepository):
        self.repository = repository

    def write_note(
        self,
        user_id: str,
        key: str,
        content: str,
        scope: str = "global",
        scope_ref: Optional[str] = None,
    ) -> LLMMemoryNote:
        """Write (upsert) a memory note.

        Args:
            user_id: Owning user ID.
            key: Unique key for this note within its scope.
            content: The note content.
            scope: 'global', 'preset', or 'model'.
            scope_ref: Required when scope is 'preset' or 'model' (preset id / model id).

        Returns:
            The persisted LLMMemoryNote.

        Raises:
            ValueError: If scope is invalid or scope_ref is missing for a scoped note.
        """
        if scope not in VALID_SCOPES:
            raise ValueError(f"Invalid scope '{scope}'. Must be one of: {', '.join(VALID_SCOPES)}")

        if scope in ("preset", "model") and not scope_ref:
            raise ValueError(f"scope_ref is required when scope is '{scope}'")

        # Clear scope_ref for global scope
        if scope == "global":
            scope_ref = None

        self._validate_content(content, scope)

        note = LLMMemoryNote(
            user_id=user_id,
            key=key,
            content=content,
            scope=scope,
            scope_ref=scope_ref,
        )
        return self.repository.upsert(note)

    @staticmethod
    def _validate_content(content: str, scope: str) -> None:
        """Reject notes that describe one generation instead of a lasting pattern.

        Seeds and generation ids (ULIDs) are one-off by construction, so they
        are rejected in every scope. A bare parameter dump (cfg/steps/etc with
        no descriptive prose) is only rejected at global scope - model/preset
        scopes exist precisely to carry those parameter values.

        Raises:
            ValueError: If the content looks tied to a single generation, or
                exceeds the length cap.
        """
        LLMMemoryManager._check_length(content)

        if _SEED_RE.search(content) or _ULID_RE.search(content):
            raise ValueError(f"Memory note rejected: {_ONE_GENERATION_MESSAGE}")

        if scope == "global" and LLMMemoryManager._looks_like_param_dump(content):
            raise ValueError(f"Memory note rejected: {_ONE_GENERATION_MESSAGE}")

    @staticmethod
    def _check_length(content: str) -> None:
        if len(content) > MAX_CONTENT_LENGTH:
            raise ValueError(_CONTENT_TOO_LONG_MESSAGE)

    @staticmethod
    def _looks_like_param_dump(content: str) -> bool:
        param_hits = _PARAM_TOKEN_RE.findall(content)
        if len(param_hits) < 2:
            return False
        remaining = _PARAM_TOKEN_RE.sub("", content)
        return len(_WORD_RE.findall(remaining)) < 4

    def read_notes(
        self,
        user_id: str,
        scope: Optional[str] = None,
        scope_ref: Optional[str] = None,
    ) -> List[LLMMemoryNote]:
        """Read memory notes with optional filtering.

        Args:
            user_id: Owning user ID.
            scope: Optional scope filter ('global', 'preset', or 'model').
            scope_ref: Optional scope reference filter (preset id / model id).

        Returns:
            List of matching LLMMemoryNote objects.
        """
        return self.repository.list_notes(
            user_id=user_id,
            scope=scope,
            scope_ref=scope_ref,
        )

    def get_note(self, user_id: str, note_id: str) -> Optional[LLMMemoryNote]:
        """Fetch a single note by ID.

        Args:
            user_id: Owning user ID.
            note_id: Note ULID.

        Returns:
            LLMMemoryNote if found, None otherwise.
        """
        return self.repository.get_by_id(note_id, user_id)

    def update_note(self, user_id: str, note_id: str, key: str, content: str) -> Optional[LLMMemoryNote]:
        """Update a note's key/content by ID.

        Args:
            user_id: Owning user ID.
            note_id: Note ULID.
            key: New key.
            content: New content.

        Returns:
            The refreshed LLMMemoryNote, or None if not found.

        Raises:
            ValueError: If content exceeds the length cap.
        """
        self._check_length(content)
        return self.repository.update(note_id, user_id, key, content)

    def get_note_by_key(
        self,
        user_id: str,
        key: str,
        scope: str,
        scope_ref: Optional[str] = None,
    ) -> Optional[LLMMemoryNote]:
        """Fetch a single note by its (user, scope, scope_ref, key) address.

        Args:
            user_id: Owning user ID.
            key: The note's key within its scope.
            scope: 'global', 'preset', or 'model'.
            scope_ref: Required when scope is 'preset' or 'model' (preset id / model id).

        Returns:
            LLMMemoryNote if found, None otherwise.

        Raises:
            ValueError: If scope is invalid or scope_ref is missing for a scoped lookup.
        """
        if scope not in VALID_SCOPES:
            raise ValueError(f"Invalid scope '{scope}'. Must be one of: {', '.join(VALID_SCOPES)}")

        if scope in ("preset", "model") and not scope_ref:
            raise ValueError(f"scope_ref is required when scope is '{scope}'")

        return self.repository.get_by_key(user_id, key, scope, scope_ref)

    def delete_note(self, user_id: str, note_id: str) -> bool:
        """Delete a note by ID.

        Args:
            user_id: Owning user ID.
            note_id: Note ULID.

        Returns:
            True if deleted, False if not found.
        """
        return self.repository.delete(note_id, user_id)
