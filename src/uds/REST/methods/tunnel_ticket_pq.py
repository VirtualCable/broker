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


# Enclosed methods under /tunnelpq path (post quantum tunnel)
class TunnelTicketPQ(Handler):
    """
    Processes tunnel requests
    """

    ROLE = consts.UserRole.ANONYMOUS
    PATH = "tunnelpq"
    NAME = "ticket"

    def post(self) -> typing.Any:
        """
        Processes get requests
        """
        logger.debug(
            "TunnelPQ parameters for post: %s (%s) from %s",
            self._args,
            self._params,
            self._request.ip,
        )

        req = types.tickets.TunnelTicketRequest.from_dict(self._params)

        if not is_trusted_source(self._request.ip):
            # Invalid requests
            raise exceptions.rest.AccessDenied()

        # Take token from url and validate it
        # Token is the "auth" of the tunnel server
        if not models.Server.validate_token(req.token, server_type=types.servers.ServerType.TUNNEL):
            raise exceptions.rest.AccessDenied()

        # Try to get ticket from DB
        try:
            ticket = models.TicketStore.get_for_tunnel(req.ticket)
            if ticket.userservice is None or ticket.userservice.user is None:
                raise Exception("Ticket has no associated userservice or the userservice has no user")

            match req.command:
                case "stop":
                    # This data will always be with tz info (from 5.0 onwards)
                    total_time = sql_now() - ticket.started

                    msg = f"User {ticket.userservice.user.name} stopped tunnel {req.token[:8]}... to {ticket.remotes_as_str()}: u:{req.sent}/d:{req.recv}/t:{total_time}."
                    log.log(ticket.userservice.user.manager, types.log.LogLevel.INFO, msg)
                    log.log(ticket.userservice, types.log.LogLevel.INFO, msg)

                    # Try to log Close event. Note that the userservice may already be gone
                    # If pool does not exists, do not log anything
                    events.add_event(
                        ticket.userservice.service_pool,
                        events.types.stats.EventType.TUNNEL_CLOSE,
                        duration=total_time,
                        sent=req.sent,
                        received=req.recv,
                        tunnel=req.token,
                    )
                    return {}

                case "start":
                    if net.ip_to_long(req.ip).version == 0:
                        raise Exception("Invalid from IP")
                    events.add_event(
                        ticket.userservice.service_pool,
                        events.types.stats.EventType.TUNNEL_OPEN,
                        username=ticket.userservice.user.pretty_name,
                        srcip=req.ip,
                        dstip=ticket.remotes_as_str(),
                        tunnel=req.token,
                    )
                    msg = f"User {ticket.userservice.user.name} started tunnel {req.token[:8]}... to {ticket.remotes_as_str()} from {req.ip}."
                    log.log(ticket.userservice.user.manager, types.log.LogLevel.INFO, msg)
                    log.log(ticket.userservice, types.log.LogLevel.INFO, msg)
                    # Generate new, notify only, ticket, for the userservice to notify when done
                    notify_ticket = models.TicketStore.create_for_tunnel(
                        userservice=ticket.userservice,
                        remotes=ticket.remotes,
                        validity=MAX_SESSION_LENGTH,
                    )

                    return types.tickets.TunnelTicketResponse(
                        remotes=ticket.remotes,
                        notify=notify_ticket,
                        shared_secret=ticket.shared_secret.hex() if ticket.shared_secret else "",
                    ).as_encrypted_dict(req.kem_kyber_key, ticket_id=req.ticket)
                case _:
                    raise Exception("Invalid command")

        except Exception as e:
            logger.info("Ticket Request ignored: %s", e)
            raise exceptions.rest.AccessDenied() from e


class TunnelRegisterPC(ServerRegisterBase):
    ROLE = consts.UserRole.ADMIN

    PATH = "tunnelpq"
    NAME = "register"

    # Just a compatibility method for old tunnel servers
    @typing.override
    def post(self) -> dict[str, typing.Any]:
        self._params["type"] = types.servers.ServerType.TUNNEL
        return super().post()
