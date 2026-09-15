"""Tests for the per-send reply-contract reminder block.

A rule stated once near the top of a long system prompt loses to recency;
``inject_reply_contract_reminder_block`` restates it right next to the turn
it governs, on every send, for modes with ``structured_reply`` on.
"""

from src.features.chat.context_builder import ChatContextBuilder
from src.features.chat.modes import ChatMode
from src.features.chat.reply_contract import REPLY_CONTRACT_REMINDER
from src.features.llm import context_budget


def _mode(structured_reply=True):
    return ChatMode(id="test", name="Test", structured_reply=structured_reply)


def test_reminder_folded_into_the_user_message_when_structured_reply_on():
    history = [{"role": "user", "content": "hi"}]
    ChatContextBuilder.inject_reply_contract_reminder_block(history, _mode(structured_reply=True))

    assert len(history) == 1
    assert history[-1]["role"] == "user"
    assert history[-1]["content"] == f"hi\n\n<context>\n{REPLY_CONTRACT_REMINDER}\n</context>"


def test_reminder_absent_when_structured_reply_off():
    history = [{"role": "user", "content": "hi"}]
    ChatContextBuilder.inject_reply_contract_reminder_block(history, _mode(structured_reply=False))

    assert history == [{"role": "user", "content": "hi"}]


def test_reminder_absent_when_mode_is_none():
    history = [{"role": "user", "content": "hi"}]
    ChatContextBuilder.inject_reply_contract_reminder_block(history, None)

    assert history == [{"role": "user", "content": "hi"}]


def test_reminder_composes_with_an_existing_contributor_block():
    """The reminder must add a second block, not replace the mode's own
    context-contributor block -- both fold into the same user turn, so the
    reminder (folded second) lands after it in the user's content."""
    history = [{"role": "user", "content": "hi"}]
    mode = _mode(structured_reply=True)

    context_budget.attach_context_block(history, "WORKSPACE CONTEXT")
    ChatContextBuilder.inject_reply_contract_reminder_block(history, mode)

    assert len(history) == 1
    assert history[-1]["role"] == "user"
    assert history[-1]["content"] == (
        f"hi\n\n<context>\nWORKSPACE CONTEXT\n</context>"
        f"\n\n<context>\n{REPLY_CONTRACT_REMINDER}\n</context>"
    )
