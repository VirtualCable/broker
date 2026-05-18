# -*- coding: utf-8 -*-
#
# Copyright (c) 2022-2026 Virtual Cable S.L.
# All rights reserved.
#
# ...license...
#
"""
WebSocket URL routing for UDS.
Top-level router that delegates to app-specific WebSocket routes,
equivalent to server/urls.py for HTTP (which does include('uds.urls')).
"""
import typing

from channels.routing import URLRouter
from django.urls import re_path

from uds.ws import websocket_urlpatterns as uds_ws_routes

websocket_urlpatterns: list[typing.Any]= [
    typing.cast(typing.Any, re_path(r'^uds/ws/', URLRouter(uds_ws_routes))),  # type:ignore
]
