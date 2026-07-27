"""
ASGI config for server project.

It exposes the ASGI callable as a module-level variable named ``application``.

Uses Django Channels ProtocolTypeRouter to handle both HTTP and WebSocket traffic.
WebSocket routes are defined in server.routing.
"""

import os

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.settings")

# Import must happen after settings are loaded (channels layer config)
from server import routing  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": get_asgi_application(),
        "websocket": URLRouter(routing.websocket_urlpatterns),
    }
)
