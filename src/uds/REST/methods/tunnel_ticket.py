# -*- coding: utf-8 -*-
#
# Copyright (c) 2014-2021 Virtual Cable S.L.
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
import logging
import typing

from uds import models
from uds.core import consts, exceptions, types
from uds.core.auths.auth import is_trusted_source
from uds.core.util import log, net
from uds.core.util.model import sql_now
from uds.core.util.stats import events
from uds.REST import Handler

from .servers import ServerRegisterBase

logger = logging.getLogger(__name__)

MAX_SESSION_LENGTH = 60 * 60 * 24 * 7 * 2  # Two weeks is max session length for a tunneled connection


# Enclosed methods under /tunnel path
class TunnelTicket(Handler):
    """
    Processes tunnel requests
    """

    ROLE = consts.UserRole.ANONYMOUS
    PATH = 'tunnel'
    NAME = 'ticket'

    def get(self) -> typing.Any:
        """
        Processes get requests
        """
        logger.debug(
            'Tunnel parameters for GET: %s (%s) from %s',
            self._args,
            self._params,
            self._request.ip,
        )

        if not is_trusted_source(self._request.ip) or len(self._args) != 3 or len(self._args[0]) != 48:
            # Invalid requests
            raise exceptions.rest.AccessDenied()

        # Take token from url and validate it
        # Token is the "auth" of the tunnel server
        token = self._args[2][:48]
        if not models.Server.validate_token(token, server_type=types.servers.ServerType.TUNNEL):
            raise exceptions.rest.AccessDenied()

        # Try to get ticket from DB
        try:
            ticket = models.TicketStore.get_for_tunnel(self._args[0])
            if ticket.userservice is None or ticket.userservice.user is None or not ticket.remotes:
                raise Exception('Ticket has no associated userservice or the userservice has no user (or no remotes)')

            # response = types.tickets.TunnelTicketResponse.from_ticket(ticket)
            if self._args[1][:4] == 'stop':
                sent, recv = self._params['sent'], self._params['recv']
                try:
                    total_time = sql_now() - ticket.started
                except Exception:  # DB may contain old not tz aware dates
                    total_time = sql_now().replace(tzinfo=None) - ticket.started.replace(tzinfo=None)

                msg = f'User {ticket.userservice.user.name} stopped tunnel {token[:8]}... to {ticket.remotes_as_str()}: u:{sent}/d:{recv}/t:{total_time}.'
                log.log(ticket.userservice.user.manager, types.log.LogLevel.INFO, msg)
                log.log(ticket.userservice, types.log.LogLevel.INFO, msg)

                # Try to log Close event
                if ticket.userservice:
                    # If pool does not exists, do not log anything
                    events.add_event(
                        ticket.userservice.deployed_service,
                        events.types.stats.EventType.TUNNEL_CLOSE,
                        duration=total_time,
                        sent=sent,
                        received=recv,
                        tunnel=token,
                    )

            else:  # New tunnel request
                if net.ip_to_long(self._args[1][:32]).version == 0:
                    raise Exception('Invalid from IP')
                events.add_event(
                    ticket.userservice.deployed_service,
                    events.types.stats.EventType.TUNNEL_OPEN,
                    username=ticket.userservice.user.pretty_name,
                    srcip=self._args[1],
                    dstip=ticket.remotes_as_str(),
                    tunnel=self._args[0],
                )
                msg = f'User {ticket.userservice.user.name} started tunnel {self._args[0][:8]}... to {ticket.remotes_as_str()} from {self._args[1]}.'
                log.log(ticket.userservice.user.manager, types.log.LogLevel.INFO, msg)
                log.log(ticket.userservice, types.log.LogLevel.INFO, msg)
                # Generate new, notify only, ticket, for the userservice to notify when done
                notify_ticket = models.TicketStore.create_for_tunnel(
                    userservice=ticket.userservice,
                    remotes=ticket.remotes,
                    validity=MAX_SESSION_LENGTH,
                )

                return types.tickets.TunnelTicketLegacyResponse(
                    host=ticket.remotes[0].host,
                    port=ticket.remotes[0].port,
                    notify=notify_ticket,
                    shared_secret=ticket.shared_secret.hex() if ticket.shared_secret else None,
                )

            return {}
        except Exception as e:
            logger.info('Ticket ignored: %s', e)
            raise exceptions.rest.AccessDenied() from e


class TunnelRegister(ServerRegisterBase):
    ROLE = consts.UserRole.ADMIN

    PATH = 'tunnel'
    NAME = 'register'

    # Just a compatibility method for old tunnel servers
    @typing.override
    def post(self) -> dict[str, typing.Any]:
        self._params['type'] = types.servers.ServerType.TUNNEL
        self._params['os'] = self._params.get(
            'os', types.os.KnownOS.LINUX.os_name()
        )  # Legacy tunnels are always linux
        self._params['version'] = ''  # No version for legacy tunnels, does not respond to API requests from UDS
        self._params['certificate'] = (
            ''  # No certificate for legacy tunnels, does not respond to API requests from UDS
        )
        return super().post()
