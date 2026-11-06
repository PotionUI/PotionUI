import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.platform.websocket.download_connection_hub import (
    DOWNLOAD_STATUS_EVENTS,
    DownloadConnectionHub,
)

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = json.loads((ROOT / "tests/contracts/download_ws_messages.json").read_text(encoding="utf-8"))


def test_hub_status_vocabulary_matches_contract():
    assert set(DOWNLOAD_STATUS_EVENTS) == set(CONTRACT["status_events"])


@pytest.mark.asyncio
async def test_hub_message_types_match_contract():
    hub = DownloadConnectionHub()
    hub.broadcast_to_download = AsyncMock()
    hub.broadcast = AsyncMock()
    types = set()

    for status in DOWNLOAD_STATUS_EVENTS:
        await hub.send_download_status("d1", status, "f.bin")
        types.add(hub.broadcast_to_download.call_args.args[1]["type"])
    await hub.send_download_progress("d1", 0.5, 1, 2, 1.0, "f.bin")
    types.add(hub.broadcast_to_download.call_args.args[1]["type"])
    await hub.send_download_queued("d1", "f.bin", 1)
    types.add(hub.broadcast.call_args.args[0]["type"])

    expected = {f"download_{s}" for s in CONTRACT["status_events"]} | set(CONTRACT["other_types"])
    assert types == expected


@pytest.mark.asyncio
async def test_hub_refuses_unknown_status():
    hub = DownloadConnectionHub()
    hub.broadcast_to_download = AsyncMock()
    with pytest.raises(ValueError):
        await hub.send_download_status("d1", "exploding", "f.bin")
    hub.broadcast_to_download.assert_not_called()


@pytest.mark.asyncio
async def test_failed_status_carries_the_gated_flag_and_repo_only_when_gated():
    hub = DownloadConnectionHub()
    hub.broadcast_to_download = AsyncMock()

    await hub.send_download_status("d1", "failed", "f.bin", error_message="denied", gated=True, gated_repo="org/name")
    gated = hub.broadcast_to_download.call_args.args[1]
    await hub.send_download_status("d1", "failed", "f.bin", error_message="boom")
    plain = hub.broadcast_to_download.call_args.args[1]

    assert gated["gated"] is True
    assert gated["gated_repo"] == "org/name"
    assert "gated" not in plain
    assert "gated_repo" not in plain
