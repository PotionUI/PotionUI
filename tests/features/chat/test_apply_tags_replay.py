import copy
from unittest.mock import Mock

import pytest

from src.features.chat.repository import ChatRepository
from src.features.chat.response_processor import ResponseProcessor
from src.features.chat.runtime import ChatRuntime
from src.features.llm.harness.replay import policy_replay
from src.features.llm.tools.base import ToolServices
from src.features.llm.tools.executor import ToolExecutor
from src.features.llm.tools.registry import ToolRegistry
from tests.fixtures.chat_engine import scripted_llm, turn_engine_for
from tests.fixtures.chat_modes import mode_registry
from tests.fixtures.seed import seed_user

USER_ID = "user-1"

VIDEO_DIRECTOR_CONTEXT = {
    "form_state": {
        "mode": "video",
        "form_data": {},
        "video_director": {"active": True, "doc": {"mode": "t2v", "global_prompt": "a cat"}, "capabilities": {}},
    }
}


@pytest.fixture
def chat_db(mock_db):
    seed_user(mock_db, USER_ID, username="u", email="u@example.com")
    return mock_db


def _manager(llm):
    executor = ToolExecutor(tool_registry=ToolRegistry())
    plugins = Mock()
    plugins.execute_hook.return_value = (Mock(data={}), [])
    repo = ChatRepository()
    manager = ChatRuntime(
        chat_repository=repo,
        llm_service=llm,
        response_processor=ResponseProcessor(plugin_registry=None),
        plugin_registry=plugins,
        chat_mode_registry=mode_registry(),
        tool_executor=executor,
        tool_services=ToolServices(),
        turn_engine=turn_engine_for(llm, executor),
        replay_selector=policy_replay,
    )
    session = repo.create_session(user_id=USER_ID, llm_config_id="llm-1", mode="generation")
    return manager, repo, session.id


async def _reply(chunks, context):
    manager, repo, session_id = _manager(scripted_llm(turns=[{"chunks": chunks}]))
    events = [
        event async for event in manager.send_message_stream(
            session_id=session_id, user_id=USER_ID, content="make it a dog",
            context_metadata=copy.deepcopy(context),
        )
    ]
    done = next(event["data"] for event in events if event["event"] == "done")
    return done["assistant_message"]["content"], repo.get_message(done["assistant_message"]["id"]).content


@pytest.mark.asyncio
async def test_a_bare_action_name_reply_is_stored_and_sent_as_a_canonical_apply_tag(chat_db):
    sent, stored = await _reply(
        ["Here you go.\n\n", '<tool_action update_director_segment segment_id="seg-1">', "a dog running on a beach"],
        VIDEO_DIRECTOR_CONTEXT,
    )
    expected = (
        'Here you go.\n\n<tool_action type="update_director_segment" segment_index="0" segment_id="seg-1">'
        "a dog running on a beach</tool_action>"
    )
    assert sent == expected
    assert stored == expected


@pytest.mark.asyncio
async def test_a_tag_naming_a_missing_shot_reaches_the_user_as_a_note(chat_db):
    sent, stored = await _reply(
        ['<tool_action type="update_director_segment" segment_index="3" segment_id="seg-4">a dog</tool_action>'],
        VIDEO_DIRECTOR_CONTEXT,
    )
    assert sent == stored == "_Can't apply: shot \"seg-4\" not found._\n\n> a dog"
