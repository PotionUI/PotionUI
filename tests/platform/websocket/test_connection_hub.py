import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import WebSocket

from src.platform.websocket.connection_hub import ConnectionHub


class TestConnectionHub:
    
    @pytest.fixture
    def manager(self):
        return ConnectionHub()
    
    @pytest.fixture
    def mock_websocket(self):
        websocket = AsyncMock(spec=WebSocket)
        return websocket
    
    def test_init(self, manager):
        assert manager.active_connections == {}
        assert manager.generation_connections == {}
    
    @pytest.mark.asyncio
    async def test_connect_success(self, manager, mock_websocket):
        client_id = "test_client_1"
        
        result = await manager.connect(mock_websocket, client_id)
        
        assert result is True
        mock_websocket.accept.assert_called_once()
        assert manager.active_connections[client_id] == mock_websocket
        assert len(manager.active_connections) == 1
    
    @pytest.mark.asyncio
    async def test_connect_failure(self, manager, mock_websocket):
        client_id = "test_client_1"
        mock_websocket.accept.side_effect = Exception("Connection failed")
        
        with patch('logging.error') as mock_log:
            result = await manager.connect(mock_websocket, client_id)
        
        assert result is False
        mock_websocket.accept.assert_called_once()
        assert client_id not in manager.active_connections
        mock_log.assert_called_once()
    
    def test_disconnect_existing_client(self, manager, mock_websocket):
        client_id = "test_client_1"
        generation_id = "gen_1"
        
        # Setup initial state
        manager.active_connections[client_id] = mock_websocket
        manager.generation_connections[generation_id] = [client_id]
        
        manager.disconnect(client_id)
        
        assert client_id not in manager.active_connections
        assert client_id not in manager.generation_connections[generation_id]
    
    def test_disconnect_nonexistent_client(self, manager):
        client_id = "nonexistent_client"
        
        # Should not raise an exception
        manager.disconnect(client_id)
        
        assert client_id not in manager.active_connections
    
    def test_disconnect_removes_from_multiple_generations(self, manager, mock_websocket):
        client_id = "test_client_1"
        gen_id_1 = "gen_1"
        gen_id_2 = "gen_2"
        
        # Setup initial state
        manager.active_connections[client_id] = mock_websocket
        manager.generation_connections[gen_id_1] = [client_id, "other_client"]
        manager.generation_connections[gen_id_2] = [client_id]
        
        manager.disconnect(client_id)
        
        assert client_id not in manager.active_connections
        assert client_id not in manager.generation_connections[gen_id_1]
        assert client_id not in manager.generation_connections[gen_id_2]
        assert "other_client" in manager.generation_connections[gen_id_1]
    
    @pytest.mark.asyncio
    async def test_subscribe_to_generation_success(self, manager, mock_websocket):
        client_id = "test_client_1"
        generation_id = "gen_1"
        
        # Setup active connection
        manager.active_connections[client_id] = mock_websocket
        
        result = await manager.subscribe_to_generation(client_id, generation_id)
        
        assert result is True
        assert generation_id in manager.generation_connections
        assert client_id in manager.generation_connections[generation_id]
    
    @pytest.mark.asyncio
    async def test_subscribe_to_generation_inactive_client(self, manager):
        client_id = "inactive_client"
        generation_id = "gen_1"
        
        result = await manager.subscribe_to_generation(client_id, generation_id)
        
        assert result is False
        assert generation_id not in manager.generation_connections
    
    @pytest.mark.asyncio
    async def test_subscribe_to_generation_existing_subscription(self, manager, mock_websocket):
        client_id = "test_client_1"
        generation_id = "gen_1"
        
        # Setup active connection and existing subscription
        manager.active_connections[client_id] = mock_websocket
        manager.generation_connections[generation_id] = [client_id]
        
        result = await manager.subscribe_to_generation(client_id, generation_id)
        
        assert result is True
        # Should not duplicate the client in the list
        assert manager.generation_connections[generation_id].count(client_id) == 1
    
    @pytest.mark.asyncio
    async def test_broadcast_to_generation_success(self, manager, mock_websocket):
        client_id = "test_client_1"
        generation_id = "gen_1"
        message = {"type": "test", "data": "test_data"}
        
        # Setup active connection and subscription
        manager.active_connections[client_id] = mock_websocket
        manager.generation_connections[generation_id] = [client_id]
        
        await manager.broadcast_to_generation(generation_id, message)
        
        expected_json = json.dumps(message)
        mock_websocket.send_text.assert_called_once_with(expected_json)
    
    @pytest.mark.asyncio
    async def test_broadcast_to_generation_no_subscribers(self, manager):
        generation_id = "nonexistent_gen"
        message = {"type": "test", "data": "test_data"}
        
        # Should not raise an exception
        await manager.broadcast_to_generation(generation_id, message)
    
    @pytest.mark.asyncio
    async def test_broadcast_to_generation_connection_error(self, manager, mock_websocket):
        client_id = "test_client_1"
        generation_id = "gen_1"
        message = {"type": "test", "data": "test_data"}
        
        # Setup active connection and subscription
        manager.active_connections[client_id] = mock_websocket
        manager.generation_connections[generation_id] = [client_id]
        
        # Make send_text raise an exception
        mock_websocket.send_text.side_effect = Exception("Connection broken")
        
        await manager.broadcast_to_generation(generation_id, message)
        
        # Client should be disconnected
        assert client_id not in manager.active_connections
        assert client_id not in manager.generation_connections[generation_id]
    
    @pytest.mark.asyncio
    async def test_broadcast_to_generation_serialization_error(self, manager, mock_websocket):
        client_id = "test_client_1"
        generation_id = "gen_1"
        
        # Create a message that cannot be serialized
        class UnserializableObject:
            pass
        
        message = {"type": "test", "data": UnserializableObject()}
        
        # Setup active connection and subscription
        manager.active_connections[client_id] = mock_websocket
        manager.generation_connections[generation_id] = [client_id]
        
        await manager.broadcast_to_generation(generation_id, message)
        
        # Should send simplified error message
        mock_websocket.send_text.assert_called_once()
        sent_data = json.loads(mock_websocket.send_text.call_args[0][0])
        assert sent_data["type"] == "test"
        assert "Failed to serialize message" in sent_data["data"]["error"]
    
    @pytest.mark.asyncio
    async def test_broadcast_to_generation_complete_serialization_failure(self, manager, mock_websocket):
        client_id = "test_client_1"
        generation_id = "gen_1"
        
        # Setup active connection and subscription
        manager.active_connections[client_id] = mock_websocket
        manager.generation_connections[generation_id] = [client_id]
        
        # Mock json.dumps to fail completely
        with patch('json.dumps', side_effect=Exception("Complete failure")):
            await manager.broadcast_to_generation(generation_id, {"type": "test"})
        
        # Should not send anything
        mock_websocket.send_text.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_broadcast_to_generation_multiple_clients(self, manager):
        client_id_1 = "test_client_1"
        client_id_2 = "test_client_2"
        generation_id = "gen_1"
        message = {"type": "test", "data": "test_data"}
        
        mock_websocket_1 = AsyncMock(spec=WebSocket)
        mock_websocket_2 = AsyncMock(spec=WebSocket)
        
        # Setup active connections and subscriptions
        manager.active_connections[client_id_1] = mock_websocket_1
        manager.active_connections[client_id_2] = mock_websocket_2
        manager.generation_connections[generation_id] = [client_id_1, client_id_2]
        
        await manager.broadcast_to_generation(generation_id, message)
        
        expected_json = json.dumps(message)
        mock_websocket_1.send_text.assert_called_once_with(expected_json)
        mock_websocket_2.send_text.assert_called_once_with(expected_json)
    
    @pytest.mark.asyncio
    async def test_broadcast_to_generation_mixed_connection_states(self, manager):
        client_id_1 = "test_client_1"
        client_id_2 = "test_client_2"  # This one will be disconnected
        client_id_3 = "test_client_3"
        generation_id = "gen_1"
        message = {"type": "test", "data": "test_data"}
        
        mock_websocket_1 = AsyncMock(spec=WebSocket)
        mock_websocket_3 = AsyncMock(spec=WebSocket)
        
        # Setup connections - client_2 is subscribed but not active
        manager.active_connections[client_id_1] = mock_websocket_1
        manager.active_connections[client_id_3] = mock_websocket_3
        manager.generation_connections[generation_id] = [client_id_1, client_id_2, client_id_3]
        
        await manager.broadcast_to_generation(generation_id, message)
        
        expected_json = json.dumps(message)
        mock_websocket_1.send_text.assert_called_once_with(expected_json)
        mock_websocket_3.send_text.assert_called_once_with(expected_json)
        # client_2 should be skipped since it's not in active_connections