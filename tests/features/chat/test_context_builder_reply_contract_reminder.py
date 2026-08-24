"""Tests for the per-send reply-contract reminder block.

A rule stated once near the top of a long system prompt loses to recency;
``inject_reply_contract_reminder_block`` restates it right next to the turn
it governs, on every send, for modes with ``structured_reply`` on.
"""

from src.features.chat.context_builder import ChatContextBuilder
from src.features.chat.modes import ChatMode
from src.features.chat.reply_contract import REPLY_CONTRACT_REMINDER


def _mode(structured_reply=True):
    return ChatMode(id="test", name="Test", structured_reply=structured_reply)


def test_reminder_inserted_before_last_user_message_when_structured_reply_on():
    history = [{"role": "user", "content": "hi"}]
    ChatContextBuilder.inject_reply_contract_reminder_block(history, _mode(structured_reply=True))

    assert history[-1] == {"role": "user", "content": "hi"}
    assert history[-2] == {"role": "system", "content": REPLY_CONTRACT_REMINDER}


def test_reminder_absent_when_structured_reply_off():
    history = [{"role": "user", "content": "hi"}]
    ChatContextBuilder.inject_reply_contract_reminder_block(history, _mode(structured_reply=False))

    assert history == [{"role": "user", "content": "hi"}]


def test_reminder_absent_when_mode_is_none():
    history = [{"role": "user", "content": "hi"}]
    ChatContextBuilder.inject_reply_contract_reminder_block(history, None)

    assert history == [{"role": "user", "content": "hi"}]


def test_reminder_composes_with_an_existing_contributor_block():
    """The reminder must add a second system block, not replace the mode's
    own context-contributor block -- both follow the same "insert
    immediately before the last message" idiom, so the reminder (injected
    second) ends up closest to the user turn."""
    history = [{"role": "user", "content": "hi"}]
    mode = _mode(structured_reply=True)

    insert_at = len(history) - 1
    history.insert(insert_at, {"role": "system", "content": "WORKSPACE CONTEXT"})
    ChatContextBuilder.inject_reply_contract_reminder_block(history, mode)

    assert history[-1] == {"role": "user", "content": "hi"}
    assert history[-2] == {"role": "system", "content": REPLY_CONTRACT_REMINDER}
    assert history[-3] == {"role": "system", "content": "WORKSPACE CONTEXT"}
