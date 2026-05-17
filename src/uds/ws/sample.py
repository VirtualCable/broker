# pyright: reportUnusedImport=false
"""
Sample WebSocket consumer.

This is an example, not imported anywhere. Copy and adapt as needed.
"""
from uds.ws import BaseUDSWebSocketConsumer


# class PoolStatusConsumer(BaseUDSWebSocketConsumer):
#     room_group_name = 'pool_status'
#
#     async def authenticate(self) -> bool:
#         # TODO: validate user/session
#         return True
#
#     async def handle_message(self, data: dict) -> None:
#         # TODO: process client message
#         pass
