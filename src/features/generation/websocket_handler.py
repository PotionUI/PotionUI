import logging
import asyncio
import json
from fastapi import WebSocket, WebSocketDisconnect

from src.platform.websocket.connection_hub import ConnectionHub
from src.features.generation.policy import GenerationPolicy

class WebSocketHandler:
    def __init__(self, connection_hub: ConnectionHub):
        self.connection_hub = connection_hub

    async def _send_heartbeat(self, websocket: WebSocket):
        """Send periodic heartbeat messages to keep the connection alive"""
        try:
            while True:
                await asyncio.sleep(15)  # Send heartbeat every 15 seconds
                try:
                    await websocket.send_text(json.dumps({
                        'type': 'heartbeat',
                        'timestamp': str(asyncio.get_event_loop().time())
                    }))
                except Exception as e:
                    print(f"Failed to send heartbeat: {str(e)}")
                    break
        except asyncio.CancelledError:
            # Task was cancelled, exit gracefully
            pass

    async def handle_websocket(self, websocket: WebSocket, client_id: str, status_tracker, user=None):
        """Handle WebSocket connection for real-time updates.

        ``user`` is the authenticated connection owner. Generation
        subscriptions are gated on it so a client can only subscribe to its
        own generations (administrators may subscribe to any).
        """
        logging.info(f"Starting WebSocket handler for client {client_id}")
        
        # Attempt to connect the WebSocket
        connection_success = await self.connection_hub.connect(websocket, client_id)

        if not connection_success:
            logging.error(f"Failed to connect WebSocket for client {client_id}")
            return

        logging.info(f"WebSocket connected successfully for client {client_id}")

        # Send initial connection confirmation
        try:
            await websocket.send_text(json.dumps({
                'type': 'connection_established',
                'client_id': client_id
            }))
            logging.info(f"Sent connection_established message to client {client_id}")
        except Exception as e:
            logging.error(f"Failed to send connection_established message to client {client_id}: {str(e)}")
            self.connection_hub.disconnect(client_id)
            return

        # Start heartbeat task
        heartbeat_task = asyncio.create_task(self._send_heartbeat(websocket))

        try:
            while True:
                # Wait for messages from client
                try:
                    data = await websocket.receive_text()
                    message = json.loads(data)
                except json.JSONDecodeError as e:
                    logging.error(f"Failed to parse message from client {client_id}: {str(e)}")
                    continue
                except Exception as e:
                    logging.error(f"Failed to receive message from client {client_id}: {str(e)}")
                    break

                # Handle different message types
                if message.get('type') == 'subscribe_generation':
                    generation_id = message.get('generation_id')
                    if generation_id:
                        logging.info(f"Client {client_id} requested to subscribe to generation {generation_id}")
                        try:
                            # Check if generation exists
                            status = status_tracker.get(generation_id)
                            if status is None:
                                known_ids = [r.id for r in status_tracker.list_all()]
                                logging.warning(f"Client {client_id} requested subscription to non-existent generation {generation_id}. Available generations: {known_ids}")
                                await websocket.send_text(json.dumps({
                                    'type': 'subscription_error',
                                    'generation_id': generation_id,
                                    'message': f'Generation {generation_id} not found'
                                }))
                                continue

                            # Enforce ownership: a client may only subscribe to
                            # its own generations. Report the same "not found"
                            # error as a missing generation so subscribing can't
                            # be used to probe for other users' generation ids.
                            if not GenerationPolicy.can_access(user, getattr(status, "user_id", None)):
                                logging.warning(f"Client {client_id} denied subscription to generation {generation_id} owned by another user")
                                await websocket.send_text(json.dumps({
                                    'type': 'subscription_error',
                                    'generation_id': generation_id,
                                    'message': f'Generation {generation_id} not found'
                                }))
                                continue

                            # Attempt to subscribe
                            subscription_success = await self.connection_hub.subscribe_to_generation(client_id, generation_id)
                            if subscription_success:
                                # Send subscription confirmation
                                await websocket.send_text(json.dumps({
                                    'type': 'subscribed',
                                    'generation_id': generation_id
                                }))

                                # Send the current status snapshot so the client
                                # doesn't miss updates that happened before subscription
                                await websocket.send_text(json.dumps({
                                    'type': 'status_update',
                                    'data': status.model_dump()
                                }))
                            else:
                                # Send error message to client
                                await websocket.send_text(json.dumps({
                                    'type': 'subscription_error',
                                    'generation_id': generation_id,
                                    'message': 'Failed to subscribe to generation'
                                }))
                        except Exception as e:
                            import traceback
                            traceback.print_exc()
                            # Send error message to client
                            await websocket.send_text(json.dumps({
                                'type': 'subscription_error',
                                'generation_id': generation_id,
                                'message': f'Error during subscription: {str(e)}'
                            }))
                    else:
                        # Send error message to client
                        await websocket.send_text(json.dumps({
                            'type': 'subscription_error',
                            'message': 'Missing generation_id in subscription request'
                        }))
                elif message.get('type') == 'ping':
                    # Respond to ping with pong
                    try:
                        await websocket.send_text(json.dumps({
                            'type': 'pong',
                            'timestamp': str(asyncio.get_event_loop().time())
                        }))
                    except Exception as e:
                        break

        except WebSocketDisconnect:
            self.connection_hub.disconnect(client_id)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.connection_hub.disconnect(client_id)
        finally:
            # Cancel heartbeat task
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

            # Ensure client is disconnected
            self.connection_hub.disconnect(client_id)
