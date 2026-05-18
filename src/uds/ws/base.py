# -*- coding: utf-8 -*-

#
# Copyright (c) 2026 Virtual Cable S.L.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
#    * Redistributions of source code must retain the above copyright notice,
#      this list of conditions and the following disclaimer.
#    * Redistributions in binary form must reproduce the above copyright notice,
#      this list of conditions and the following disclaimer in the documentation
#      and/or other materials provided with the distribution.
#    * Neither the name of Virtual Cable S.L. nor the names of its contributors
#      may be used to endorse or promote products derived from this software
#      without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
Author: Adolfo Gómez, dkmaster at dkmon dot com
"""
import abc
import datetime
import json
import logging
import typing

from channels.generic.websocket import AsyncWebsocketConsumer
from django.utils import timezone

from uds.core import consts, types
from uds.core.auths.auth import root_user
from uds.models import User

logger = logging.getLogger(__name__)


class BaseUDSWebSocketConsumer(AsyncWebsocketConsumer, abc.ABC):
    """
    Base async WebSocket consumer for UDS.

    Authentication follows the same model as the HTTP GlobalRequestMiddleware:
      - The Django session (self.scope['session']) is read during the WS handshake,
        which is an HTTP request and therefore carries the session cookie normally.
      - SESSION_USER_KEY ('uk') identifies the logged-in user.
      - SESSION_EXPIRY_KEY ('ek') holds an ISO-format datetime; the session is
        rejected if it has expired.

    Subclasses must override:
        - verify()        — extra per-endpoint authorisation check (ABC).
        - handle_message() — process incoming JSON messages.
    """

    # Populated by authenticate() on successful auth; None if not authenticated.
    user: User | None = None

    # Client IP addresses, populated by _fill_ips() during connect.
    # Same semantics as ExtendedHttpRequest.ip / ip_proxy.
    ip: str = ''
    ip_proxy: str = ''

    # --- IP detection ---

    def _fill_ips(self) -> None:
        """
        Extracts the client IP from the WebSocket scope.

        scope['client'] is the connecting peer — empty when nginx reaches
        gunicorn via a Unix socket, which is the normal production setup.
        scope['headers'] carries the HTTP headers as [(b'name', b'value'), ...].
        """
        from uds.core.util import net

        client = self.scope.get('client') or ['']
        remote_addr = client[0] if client else ''

        xff = ''
        for name, value in self.scope.get('headers', []):
            if name == b'x-forwarded-for':
                xff = value.decode('latin-1')
                break

        info = net.recover_ips(remote_addr, xff)
        self.ip = info.ip
        self.ip_proxy = info.ip_proxy
        logger.debug('WebSocket ip: %s, ip_proxy: %s', self.ip, self.ip_proxy)

    # --- Authentication ---

    async def authenticate(self) -> bool:
        """
        Validates the connecting client using the UDS session, mirroring the
        logic in uds.middleware.request._get_user / _process_request.

        Returns True only when:
          1. A valid, active UDS user is found in the session.
          2. The session has not expired.
          3. self.verify() returns True.
        """
        self._fill_ips()
        session = self.scope.get('session')
        if session is None:
            logger.warning('WebSocket: no session in scope')
            return False

        user_id = session.get(consts.auth.SESSION_USER_KEY)
        if user_id is None:
            logger.warning('WebSocket: no user key in session')
            return False

        # Resolve user — same logic as _get_user()
        user: User | None= None
        try:
            if user_id == consts.auth.ROOT_ID:
                user = root_user()
            else:
                user = await User.objects.aget(pk=user_id)
        except User.DoesNotExist:
            pass

        if user is None or user.state != types.states.State.ACTIVE:
            logger.warning('WebSocket: user %s not found or not active', user_id)
            return False

        # Check session expiry — same logic as _process_request()
        now = timezone.now()
        try:
            expiry = datetime.datetime.fromisoformat(session.get(consts.auth.SESSION_EXPIRY_KEY, ''))
            expiry = timezone.make_aware(expiry)
        except ValueError:
            expiry = now  # treat missing/invalid expiry as already expired

        if expiry < now:
            logger.warning('WebSocket: session expired for user %s', user_id)
            return False

        self.user = user
        return await self.verify()

    @abc.abstractmethod
    async def verify(self) -> bool:
        """
        Per-endpoint authorisation check, called by authenticate() after the
        session-based user validation succeeds.

        self.user is guaranteed to be a valid, active User at this point.
        Return True to allow the connection, False to reject it.
        """

    # On connection, authenticate and accept or reject accordingly.
    async def connect(self) -> None:
        if not await self.authenticate():
            logger.warning('WebSocket rejected: path=%s', self.scope.get('path'))
            await self.close()
            return

        await self.accept()
        logger.debug('WebSocket connected: %s (user=%s)', self.scope.get('path'), self.user)

    async def disconnect(self, code: int) -> None:
        logger.debug('WebSocket disconnected: %s (code=%s)', self.scope.get('path'), code)

    # --- Message handling ---

    async def receive(self, text_data: str | None = None, bytes_data: bytes | None = None) -> None:
        """Parse incoming JSON and delegate to handle_message()."""
        if text_data is None:
            return  # ignore binary frames

        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({'error': 'Invalid JSON'}))
            return

        await self.handle_message(data)

    async def handle_message(self, data: dict[str, typing.Any]) -> None:
        """Override this to process messages from the client."""
        await self.send(text_data=json.dumps({'echo': data}))
