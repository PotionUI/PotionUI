"""
LLM-generated chat session titles.

After the first user/assistant exchange, a short LLM call produces a
conversation title (like other chat apps). Generation is best-effort:
failures are logged and leave ``title_generated`` unset so the next
exchange retries, bounded by MAX_MESSAGES.
"""

import logging
from typing import Optional

from src.features.chat.dto import SessionResponse
from src.features.chat.repository import ChatRepository
from src.features.llm import trace_collector

logger = logging.getLogger(__name__)

TITLE_PROMPT = (
    "Write a 3-6 word title for this conversation. "
    "Reply with the title only - no quotes, no punctuation at the end."
)

# Character budget for the excerpt of the first exchange sent to the LLM.
MAX_EXCHANGE_CHARS = 1500

# Stop retrying after this many messages; a session that failed titling three
# times in a row is not worth further calls.
MIN_MESSAGES = 2
MAX_MESSAGES = 6

MAX_TITLE_LENGTH = 80

_SURROUNDING_QUOTES = '"\'`“”‘’'
_TRAILING_PUNCTUATION = '.,;:!?…'


class ChatTitleGenerator:
    """Generates and persists session titles via a small LLM call."""

    def __init__(self, llm_service, chat_repository: ChatRepository):
        self.llm_service = llm_service
        self.chat_repository = chat_repository

    def should_generate(self, session: SessionResponse, message_count: int) -> bool:
        """Whether a title should be generated for this session right now."""
        if session.title_generated:
            return False
        return MIN_MESSAGES <= message_count <= MAX_MESSAGES

    async def generate(self, session_id: str) -> Optional[str]:
        """Generate, sanitize and persist a title. Returns it, or None on failure."""
        session = self.chat_repository.get_session(session_id)
        if not session or session.title_generated or not session.llm_config_id:
            return None

        messages = self.chat_repository.get_messages(session_id)
        first_user = next((m for m in messages if m.role == 'user'), None)
        first_assistant = next((m for m in messages if m.role == 'assistant'), None)
        if not first_user or not first_assistant:
            return None

        exchange = (
            f"User: {first_user.content}\n\nAssistant: {first_assistant.content}"
        )[:MAX_EXCHANGE_CHARS]

        try:
            with trace_collector.activate(session_id, session.user_id, purpose="title"):
                response = await self.llm_service.generate_with_history(
                    messages=[{"role": "user", "content": f"{TITLE_PROMPT}\n\n{exchange}"}],
                    llm_id=session.llm_config_id,
                    options_override={"max_tokens": 24, "temperature": 0.3, "think": False},
                )
        except Exception as e:
            logger.warning(f"Title generation failed for session {session_id}: {e}")
            return None

        title = self.sanitize(response.content if response else None)
        if not title:
            logger.warning(f"Title generation for session {session_id} produced no usable text")
            return None

        if not self.chat_repository.set_session_title(session_id, title):
            logger.warning(f"Failed to persist generated title for session {session_id}")
            return None

        logger.info(f"Generated title for session {session_id}: {title!r}")
        return title

    @staticmethod
    def sanitize(raw: Optional[str]) -> Optional[str]:
        """Normalize LLM output into a single clean title line."""
        if not raw:
            return None
        # Models sometimes return multiple lines or label the answer; keep the
        # first non-empty line and collapse internal whitespace.
        first_line = next((line for line in raw.splitlines() if line.strip()), '')
        title = ' '.join(first_line.split())
        title = title.strip().strip(_SURROUNDING_QUOTES).strip()
        title = title.rstrip(_TRAILING_PUNCTUATION).strip()
        if not title:
            return None
        return title[:MAX_TITLE_LENGTH].strip()
