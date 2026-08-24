import json
import types
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import Optional

from src.features.generation.websocket_handler import WebSocketHandler
from src.platform.websocket.connection_manager import ConnectionManager
from src.platform.security.user import AccountType


class MockGenerationStatus(BaseModel):
    id: str
    status: str
    progress: float
    user_id: Optional[str] = None

    def model_dump(self):
        return {"id": self.id, "status": self.status, "progress": self.progress}


class FakeStatusTracker:
    """Minimal stand-in for GenerationStatusTracker (get/list_all)."""

    def __init__(self, records):
        self._records = records

    def get(self, id):
        return self._records.get(id)

    def list_all(self):
        return list(self._records.values())


class TestWebSocketHandler:
    
    @pytest.fixture
    def mock_connection_manager(self):
        return MagicMock(spec=ConnectionManager)
    
    @pytest.fixture
    def handler(self, mock_connection_manager):
        return WebSocketHandler(mock_connection_manager)
    
    @pytest.fixture
    def mock_websocket(self):
        websocket = AsyncMock(spec=WebSocket)
        return websocket
    
    @pytest.fixture
    def generation_statuses(self):
        return FakeStatusTracker({
            "gen_1": MockGenerationStatus(id="gen_1", status="running", progress=0.5, user_id="user_1"),
            "gen_2": MockGenerationStatus(id="gen_2", status="completed", progress=1.0, user_id="user_1")
        })

    @pytest.fixture
    def owner_user(self):
        """The user that owns gen_1 / gen_2 in the generation_statuses fixture."""
        return types.SimpleNamespace(id="user_1", account_type=AccountType.USER)

    @pytest.fixture
    def other_user(self):
        """A different, non-admin user who owns none of the generations."""
        return types.SimpleNamespace(id="user_2", account_type=AccountType.USER)
    
    @pytest.mark.asyncio
    async def test_send_heartbeat(self, handler, mock_websocket):
        # Mock asyncio.sleep to prevent actual waiting
        with patch('asyncio.sleep', side_effect=asyncio.CancelledError):
            await handler._send_heartbeat(mock_websocket)
        
        # Should not have sent anything due to immediate cancellation
        mock_websocket.send_text.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_send_heartbeat_with_message(self, handler, mock_websocket):
        call_count = 0
        
        async def mock_sleep(duration):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:  # Stop after one heartbeat
                raise asyncio.CancelledError()
        
        with patch('asyncio.sleep', side_effect=mock_sleep), \
             patch('asyncio.get_event_loop') as mock_loop:
            mock_loop.return_value.time.return_value = 1234567890
            await handler._send_heartbeat(mock_websocket)
        
        # Should have sent one heartbeat
        mock_websocket.send_text.assert_called_once()
        sent_data = json.loads(mock_websocket.send_text.call_args[0][0])
        assert sent_data["type"] == "heartbeat"
        assert sent_data["timestamp"] == "1234567890"
    
    @pytest.mark.asyncio
    async def test_handle_websocket_connection_failure(self, handler, mock_websocket, generation_statuses):
        client_id = "test_client"
        handler.connection_manager.connect.return_value = False
        
        await handler.handle_websocket(mock_websocket, client_id, generation_statuses)
        
        # Should not proceed if connection fails
        mock_websocket.send_text.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_handle_websocket_connection_success(self, handler, mock_websocket, generation_statuses):
        client_id = "test_client"
        handler.connection_manager.connect.return_value = True
        
        # Mock receive_text to immediately disconnect to end the loop
        mock_websocket.receive_text.side_effect = WebSocketDisconnect()
        
        # Mock the heartbeat task creation and handling
        async def mock_heartbeat_coroutine():
            # Simulate heartbeat task that gets cancelled
            try:
                while True:
                    await asyncio.sleep(15)
            except asyncio.CancelledError:
                pass
        
        with patch('src.features.generation.websocket_handler.WebSocketHandler._send_heartbeat') as mock_heartbeat:
            mock_heartbeat.return_value = mock_heartbeat_coroutine()
            
            await handler.handle_websocket(mock_websocket, client_id, generation_statuses)
        
        # Should send connection established message
        mock_websocket.send_text.assert_called()
        sent_data = json.loads(mock_websocket.send_text.call_args_list[0][0][0])
        assert sent_data["type"] == "connection_established"
        assert sent_data["client_id"] == client_id
        
        # Should disconnect at the end
        handler.connection_manager.disconnect.assert_called_with(client_id)
    
    @pytest.mark.asyncio
    async def test_handle_websocket_connection_established_error(self, handler, mock_websocket, generation_statuses):
        client_id = "test_client"
        handler.connection_manager.connect.return_value = True
        mock_websocket.send_text.side_effect = Exception("Send failed")
        
        await handler.handle_websocket(mock_websocket, client_id, generation_statuses)
        
        # Should disconnect due to send error
        handler.connection_manager.disconnect.assert_called_with(client_id)
    
    @pytest.mark.asyncio
    async def test_subscribe_generation_success(self, handler, mock_websocket, generation_statuses, owner_user):
        client_id = "test_client"
        generation_id = "gen_1"

        handler.connection_manager.connect.return_value = True
        handler.connection_manager.subscribe_to_generation.return_value = True

        # Mock the message sequence
        messages = [
            json.dumps({"type": "subscribe_generation", "generation_id": generation_id})
        ]

        mock_websocket.receive_text.side_effect = messages + [WebSocketDisconnect()]

        # Mock the heartbeat task
        async def mock_heartbeat_coroutine():
            try:
                while True:
                    await asyncio.sleep(15)
            except asyncio.CancelledError:
                pass

        with patch('src.features.generation.websocket_handler.WebSocketHandler._send_heartbeat') as mock_heartbeat:
            mock_heartbeat.return_value = mock_heartbeat_coroutine()

            await handler.handle_websocket(mock_websocket, client_id, generation_statuses, owner_user)

        # Should call subscribe_to_generation
        handler.connection_manager.subscribe_to_generation.assert_called_with(client_id, generation_id)
        
        # Should send subscription confirmation and status update
        calls = mock_websocket.send_text.call_args_list
        assert len(calls) >= 3  # connection_established, subscribed, status_update
        
        # Check subscribed message
        subscribed_call = next(call for call in calls if "subscribed" in call[0][0])
        subscribed_data = json.loads(subscribed_call[0][0])
        assert subscribed_data["type"] == "subscribed"
        assert subscribed_data["generation_id"] == generation_id
        
        # Check status update
        status_call = next(call for call in calls if "status_update" in call[0][0])
        status_data = json.loads(status_call[0][0])
        assert status_data["type"] == "status_update"
        assert status_data["data"]["id"] == generation_id
    
    @pytest.mark.asyncio
    async def test_subscribe_generation_other_user_denied(self, handler, mock_websocket, generation_statuses, other_user):
        """A non-owner may not subscribe; they get the same 'not found' error
        as a missing generation and no subscription is attempted."""
        client_id = "test_client"
        generation_id = "gen_1"  # owned by user_1, not other_user

        handler.connection_manager.connect.return_value = True
        handler.connection_manager.subscribe_to_generation = AsyncMock(return_value=True)

        messages = [
            json.dumps({"type": "subscribe_generation", "generation_id": generation_id})
        ]
        mock_websocket.receive_text.side_effect = messages + [WebSocketDisconnect()]

        async def mock_heartbeat_coroutine():
            try:
                while True:
                    await asyncio.sleep(15)
            except asyncio.CancelledError:
                pass

        with patch('src.features.generation.websocket_handler.WebSocketHandler._send_heartbeat') as mock_heartbeat:
            mock_heartbeat.return_value = mock_heartbeat_coroutine()

            await handler.handle_websocket(mock_websocket, client_id, generation_statuses, other_user)

        # Must NOT have subscribed the non-owner
        handler.connection_manager.subscribe_to_generation.assert_not_called()

        # Must have sent a "not found" subscription_error (no existence leak)
        calls = mock_websocket.send_text.call_args_list
        error_call = next(call for call in calls if "subscription_error" in call[0][0])
        error_data = json.loads(error_call[0][0])
        assert error_data["type"] == "subscription_error"
        assert error_data["generation_id"] == generation_id
        assert "not found" in error_data["message"]

    @pytest.mark.asyncio
    async def test_subscribe_generation_owner_allowed(self, handler, mock_websocket, generation_statuses, owner_user):
        """The owner subscribes successfully."""
        client_id = "test_client"
        generation_id = "gen_1"

        handler.connection_manager.connect.return_value = True
        handler.connection_manager.subscribe_to_generation = AsyncMock(return_value=True)

        messages = [
            json.dumps({"type": "subscribe_generation", "generation_id": generation_id})
        ]
        mock_websocket.receive_text.side_effect = messages + [WebSocketDisconnect()]

        async def mock_heartbeat_coroutine():
            try:
                while True:
                    await asyncio.sleep(15)
            except asyncio.CancelledError:
                pass

        with patch('src.features.generation.websocket_handler.WebSocketHandler._send_heartbeat') as mock_heartbeat:
            mock_heartbeat.return_value = mock_heartbeat_coroutine()

            await handler.handle_websocket(mock_websocket, client_id, generation_statuses, owner_user)

        handler.connection_manager.subscribe_to_generation.assert_called_with(client_id, generation_id)
        calls = mock_websocket.send_text.call_args_list
        subscribed_call = next(call for call in calls if "subscribed" in call[0][0])
        assert json.loads(subscribed_call[0][0])["type"] == "subscribed"

    @pytest.mark.asyncio
    async def test_subscribe_generation_nonexistent(self, handler, mock_websocket, generation_statuses):
        client_id = "test_client"
        generation_id = "nonexistent_gen"
        
        handler.connection_manager.connect.return_value = True
        
        messages = [
            json.dumps({"type": "subscribe_generation", "generation_id": generation_id})
        ]
        
        mock_websocket.receive_text.side_effect = messages + [WebSocketDisconnect()]
        
        # Mock the heartbeat task
        async def mock_heartbeat_coroutine():
            try:
                while True:
                    await asyncio.sleep(15)
            except asyncio.CancelledError:
                pass
        
        with patch('src.features.generation.websocket_handler.WebSocketHandler._send_heartbeat') as mock_heartbeat:
            mock_heartbeat.return_value = mock_heartbeat_coroutine()
            
            await handler.handle_websocket(mock_websocket, client_id, generation_statuses)
        
        # Should send subscription error
        calls = mock_websocket.send_text.call_args_list
        error_call = next(call for call in calls if "subscription_error" in call[0][0])
        error_data = json.loads(error_call[0][0])
        assert error_data["type"] == "subscription_error"
        assert error_data["generation_id"] == generation_id
        assert "not found" in error_data["message"]
    
    @pytest.mark.asyncio
    async def test_ping_pong(self, handler, mock_websocket, generation_statuses):
        client_id = "test_client"
        
        handler.connection_manager.connect.return_value = True
        
        messages = [
            json.dumps({"type": "ping"})
        ]
        
        mock_websocket.receive_text.side_effect = messages + [WebSocketDisconnect()]
        
        # Mock the heartbeat task and event loop
        async def mock_heartbeat_coroutine():
            try:
                while True:
                    await asyncio.sleep(15)
            except asyncio.CancelledError:
                pass
        
        with patch('src.features.generation.websocket_handler.WebSocketHandler._send_heartbeat') as mock_heartbeat, \
             patch('asyncio.get_event_loop') as mock_loop:
            mock_heartbeat.return_value = mock_heartbeat_coroutine()
            mock_loop.return_value.time.return_value = 1234567890
            
            await handler.handle_websocket(mock_websocket, client_id, generation_statuses)
        
        # Should send pong response
        calls = mock_websocket.send_text.call_args_list
        pong_call = next(call for call in calls if "pong" in call[0][0])
        pong_data = json.loads(pong_call[0][0])
        assert pong_data["type"] == "pong"
        assert pong_data["timestamp"] == "1234567890"
    
    @pytest.mark.asyncio
    async def test_invalid_json_message(self, handler, mock_websocket, generation_statuses):
        client_id = "test_client"
        
        handler.connection_manager.connect.return_value = True
        
        # Send invalid JSON, then disconnect
        mock_websocket.receive_text.side_effect = ["invalid json", WebSocketDisconnect()]
        
        # Mock the heartbeat task
        async def mock_heartbeat_coroutine():
            try:
                while True:
                    await asyncio.sleep(15)
            except asyncio.CancelledError:
                pass
        
        with patch('src.features.generation.websocket_handler.WebSocketHandler._send_heartbeat') as mock_heartbeat:
            mock_heartbeat.return_value = mock_heartbeat_coroutine()
            
            await handler.handle_websocket(mock_websocket, client_id, generation_statuses)
        
        # Should continue despite JSON error and disconnect at the end
        handler.connection_manager.disconnect.assert_called_with(client_id)
    
    @pytest.mark.asyncio
    async def test_receive_exception(self, handler, mock_websocket, generation_statuses):
        client_id = "test_client"
        
        handler.connection_manager.connect.return_value = True
        mock_websocket.receive_text.side_effect = Exception("Receive failed")
        
        # Mock the heartbeat task
        async def mock_heartbeat_coroutine():
            try:
                while True:
                    await asyncio.sleep(15)
            except asyncio.CancelledError:
                pass
        
        with patch('src.features.generation.websocket_handler.WebSocketHandler._send_heartbeat') as mock_heartbeat:
            mock_heartbeat.return_value = mock_heartbeat_coroutine()
            
            await handler.handle_websocket(mock_websocket, client_id, generation_statuses)
        
        # Should disconnect due to receive error
        handler.connection_manager.disconnect.assert_called_with(client_id)
    
    @pytest.mark.asyncio
    async def test_heartbeat_task_cleanup(self, handler, mock_websocket, generation_statuses):
        client_id = "test_client"
        
        handler.connection_manager.connect.return_value = True
        mock_websocket.receive_text.side_effect = WebSocketDisconnect()
        
        # Mock the heartbeat task
        async def mock_heartbeat_coroutine():
            try:
                while True:
                    await asyncio.sleep(15)
            except asyncio.CancelledError:
                pass
        
        with patch('src.features.generation.websocket_handler.WebSocketHandler._send_heartbeat') as mock_heartbeat:
            mock_heartbeat.return_value = mock_heartbeat_coroutine()
            
            await handler.handle_websocket(mock_websocket, client_id, generation_statuses)
            
            # Task cleanup is handled by the handler
            pass