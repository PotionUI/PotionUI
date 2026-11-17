import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import WebSocket, WebSocketDisconnect
from PIL import Image

from src.features.generation.output_broadcaster import GenerationOutputBroadcaster
from src.features.generation.status_tracker import GenerationState, GenerationStatusTracker
from src.features.generation.websocket_handler import WebSocketHandler
from src.pipelines.outputs import ImageGenerationOutput, ProgressGenerationOutput
from src.platform.security.user import AccountType
from src.platform.websocket.connection_hub import ConnectionHub
from tests.features.generation.test_preview_clip_output import make_output as make_clip

OWNER = SimpleNamespace(id="u1", account_type=AccountType.USER)
ADMIN = SimpleNamespace(id="root", account_type=AccountType.ADMIN)


@pytest.fixture
def rig():
    hub = ConnectionHub()
    tracker = GenerationStatusTracker()
    with patch("src.features.generation.status_tracker.generation_repo"):
        tracker.create("gen-1", preset_id="p", user_id="u1")
        tracker.transition("gen-1", GenerationState.RUNNING)
    broadcaster = GenerationOutputBroadcaster(hub, tracker, Mock())
    return SimpleNamespace(hub=hub, tracker=tracker, broadcaster=broadcaster, handler=WebSocketHandler(hub))


def image(color):
    return ImageGenerationOutput(image=Image.new("RGB", (8, 8), color), temporary=True, pipe_id=1, pipe_name="generator")


async def idle(self, websocket):
    await asyncio.sleep(3600)


async def subscribe(rig, user, client_id="c1", generation_id="gen-1"):
    websocket = AsyncMock(spec=WebSocket)
    websocket.receive_text.side_effect = [
        json.dumps({"type": "subscribe_generation", "generation_id": generation_id}), WebSocketDisconnect(),
    ]
    with patch.object(WebSocketHandler, "_send_heartbeat", idle):
        await rig.handler.handle_websocket(websocket, client_id, rig.tracker, user)
    return [json.loads(call.args[0]) for call in websocket.send_text.call_args_list]


def types(messages):
    return [message["type"] for message in messages]


@pytest.mark.asyncio
async def test_subscribing_mid_run_replays_the_last_preview_after_the_snapshot(rig):
    await rig.broadcaster.handle_output("gen-1", image("red"))
    await rig.broadcaster.handle_output("gen-1", image("blue"))

    messages = await subscribe(rig, OWNER)

    assert types(messages) == ["connection_established", "subscribed", "status_update", "workbench_update"]
    replay = messages[-1]
    assert replay["replayed"] is True and replay["generation_id"] == "gen-1" and replay["image"]


@pytest.mark.asyncio
async def test_only_the_last_image_is_replayed(rig):
    await rig.broadcaster.handle_output("gen-1", image("red"))
    first = (await subscribe(rig, OWNER, "c1"))[-1]["image"]
    await rig.broadcaster.handle_output("gen-1", image("blue"))

    messages = await subscribe(rig, OWNER, "c2")

    assert types(messages).count("workbench_update") == 1
    assert messages[-1]["image"] != first


@pytest.mark.asyncio
async def test_a_video_run_replays_the_last_image_and_the_last_clip(rig):
    await rig.broadcaster.handle_output("gen-1", image("red"))
    await rig.broadcaster.handle_output("gen-1", make_clip())

    messages = await subscribe(rig, OWNER)

    assert types(messages)[3:] == ["workbench_update", "workbench_preview_clip"]
    assert all(message["replayed"] is True for message in messages[3:])


@pytest.mark.asyncio
async def test_nothing_is_replayed_before_a_preview_exists(rig):
    await rig.broadcaster.handle_output("gen-1", ProgressGenerationOutput(state="running", progress=0.2))

    messages = await subscribe(rig, OWNER)

    assert types(messages) == ["connection_established", "subscribed", "status_update"]


@pytest.mark.asyncio
async def test_nothing_is_replayed_for_a_finished_generation(rig):
    await rig.broadcaster.handle_output("gen-1", image("red"))
    with patch("src.features.generation.status_tracker.generation_repo"):
        rig.tracker.transition("gen-1", GenerationState.COMPLETED)

    messages = await subscribe(rig, OWNER)

    assert types(messages) == ["connection_established", "subscribed", "status_update"]


@pytest.mark.asyncio
async def test_the_retained_previews_are_released_when_the_generation_finishes(rig):
    await rig.broadcaster.handle_output("gen-1", image("red"))
    await rig.broadcaster.handle_output("gen-1", make_clip())
    assert len(rig.tracker.get("gen-1").previews) == 2

    with patch("src.features.generation.status_tracker.generation_repo"):
        rig.tracker.transition("gen-1", GenerationState.FAILED)

    assert all(not record.previews for record in rig.tracker.list_all())


@pytest.mark.asyncio
async def test_a_preview_arriving_after_the_finish_is_not_retained(rig):
    with patch("src.features.generation.status_tracker.generation_repo"):
        rig.tracker.transition("gen-1", GenerationState.CANCELLED)

    await rig.broadcaster.handle_output("gen-1", image("red"))

    assert not rig.tracker.get("gen-1").previews


@pytest.mark.asyncio
async def test_a_regular_user_replay_has_no_admin_detail_and_an_admin_replay_does(rig):
    await rig.broadcaster.handle_output("gen-1", make_clip())

    user_clip = (await subscribe(rig, OWNER, "c1"))[-1]
    admin_clip = (await subscribe(rig, ADMIN, "c2"))[-1]

    assert user_clip["replayed"] is True and "admin_detail" not in user_clip
    assert admin_clip["replayed"] is True and admin_clip["admin_detail"]["format"] == "WEBP"
    assert "admin_detail" not in json.dumps(rig.tracker.get("gen-1").previews["workbench_preview_clip"][0])


@pytest.mark.asyncio
async def test_another_users_subscription_gets_no_replay(rig):
    await rig.broadcaster.handle_output("gen-1", image("red"))

    messages = await subscribe(rig, SimpleNamespace(id="u2", account_type=AccountType.USER))

    assert types(messages) == ["connection_established", "subscription_error"]


@pytest.mark.asyncio
async def test_a_failing_replay_does_not_break_the_subscription(rig):
    await rig.broadcaster.handle_output("gen-1", image("red"))
    rig.tracker.replay_previews = Mock(side_effect=RuntimeError("boom"))

    messages = await subscribe(rig, OWNER)

    assert types(messages) == ["connection_established", "subscribed", "status_update"]


@pytest.mark.asyncio
async def test_live_subscribers_still_receive_the_preview_without_the_replay_flag(rig):
    socket = AsyncMock()
    await rig.hub.connect(socket, "live")
    await rig.hub.subscribe_to_generation("live", "gen-1")

    await rig.broadcaster.handle_output("gen-1", image("red"))

    sent = json.loads(socket.send_text.call_args.args[0])
    assert sent["type"] == "workbench_update" and "replayed" not in sent
