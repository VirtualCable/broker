# -*- coding: utf-8 -*-
#
# Copyright (c) 2022-2026 Virtual Cable S.L.
# All rights reserved.
#
# ...license...
#
"""
WebSocket consumers for UDS.

Consumers are the WebSocket equivalent of Django views.
Subclass BaseUDSWebSocketConsumer and override handle_message().
"""
import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)


class BaseUDSWebSocketConsumer(AsyncWebsocketConsumer):
    """
    Base async WebSocket consumer for UDS.

    Subclasses should override:
        - authenticate() to validate the connecting client (return True/False)
        - handle_message() to process incoming JSON messages
    """

    room_group_name: str = 'uds_default'

    # --- Connection lifecycle ---

    async def authenticate(self) -> bool:
        """Override to validate the client. Return True to allow, False to reject."""
        return True

    async def connect(self) -> None:
        if not await self.authenticate():
            logger.warning('WebSocket rejected by authenticate()')
            await self.close()
            return

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()
        logger.debug('WebSocket connected: %s', self.room_group_name)

    async def disconnect(self, code: int) -> None:
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)
        logger.debug('WebSocket disconnected: %s (code: %s)', self.room_group_name, code)

    # --- Message handling ---

    async def receive(self, text_data: str | None = None, bytes_data: bytes | None = None) -> None:
        """Parse incoming JSON and delegate to handle_message()."""
        if text_data is None:
            return  # ignore binary messages

        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({'error': 'Invalid JSON'}))
            return

        await self.handle_message(data)

    async def handle_message(self, data: dict) -> None:
        """Override this to process messages from the client."""
        await self.send(text_data=json.dumps({'echo': data}))

    # --- Group messaging helpers ---

    async def send_to_group(self, event_type: str, payload: dict) -> None:
        """Broadcast to all members of this consumer's room group."""
        await self.channel_layer.group_send(
            self.room_group_name,
            {'type': f'{event_type}.event', **payload},
        )

    async def broadcast_event(self, event: dict) -> None:
        """Receive a group event and forward it to the WebSocket client."""
        await self.send(text_data=json.dumps(event))
