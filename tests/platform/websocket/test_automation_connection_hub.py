import json
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocket

from src.platform.websocket.automation_connection_hub import AutomationConnectionHub

RUN_FAILED = {
    "type": "automation_run_update",
    "run_id": "r1",
    "automation_id": "a1",
    "status": "failed",
    "error": "No such file or directory: '/srv/app/models/x.safetensors'",
}


@pytest.fixture
def hub():
    return AutomationConnectionHub()


async def _connect(hub, client_id, is_admin):
    websocket = AsyncMock(spec=WebSocket)
    await hub.connect(websocket, client_id, is_admin=is_admin)
    return websocket


@pytest.mark.asyncio
async def test_regular_clients_get_a_plain_error(hub):
    user_socket = await _connect(hub, "u", False)

    await hub.broadcast(RUN_FAILED)

    sent = json.loads(user_socket.send_text.call_args.args[0])
    assert "No such file" not in sent["error"]
    assert "/srv/app" not in sent["error"]
    assert sent["status"] == "failed"
    assert sent["run_id"] == "r1"


@pytest.mark.asyncio
async def test_admin_clients_keep_the_detail_with_paths_scrubbed(hub):
    admin_socket = await _connect(hub, "a", True)

    await hub.broadcast(RUN_FAILED)

    sent = json.loads(admin_socket.send_text.call_args.args[0])
    assert "No such file" in sent["error"]
    assert "/srv/app" not in sent["error"]


@pytest.mark.asyncio
async def test_messages_without_an_error_are_untouched(hub):
    user_socket = await _connect(hub, "u", False)
    message = {"type": "automation_run_update", "run_id": "r1", "status": "running"}

    await hub.broadcast(message)

    assert json.loads(user_socket.send_text.call_args.args[0]) == message


@pytest.mark.asyncio
async def test_disconnect_forgets_the_admin_flag(hub):
    await _connect(hub, "a", True)
    hub.disconnect("a")
    reconnected = await _connect(hub, "a", False)

    await hub.broadcast(RUN_FAILED)

    assert "No such file" not in json.loads(reconnected.send_text.call_args.args[0])["error"]
