#
# Copyright (c) 2014-2021 Virtual Cable S.L.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#    * Redistributions of source code must retain the above copyright notice,
#      this list of conditions and the following disclaimer.
#
#    * Redistributions in binary form must reproduce the above copyright notice,
#      this list of conditions and the following disclaimer in the documentation
#      and/or other materials provided with the distribution.
#
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
from uds.core import consts
from uds.core import exceptions
from uds.core import types
from uds.core.auths.auth import is_trusted_source
from uds.core.util import log
from uds.core.util import net
from uds.core.util.model import sql_now
from uds.core.util.stats import events
from uds.REST import Handler

from .servers import ServerRegisterBase

logger: logging.Logger = logging.getLogger(__name__)

# Two weeks is max session length for a tunneled connection
MAX_SESSION_LENGTH = 60 * 60 * 24 * 7 * 2


class TunnelTicket(Handler):
    """
    Processes tunnel-server ticket requests.

    The tunnel-server authenticates via the ``Authorization: Bearer sk-<token>``
    header.  ``Handler.__init__`` captures it into ``self._sk_token`` (already
    stripped of the ``sk-`` prefix and validated against ``Server.token_hash``).
    The body is parsed as a ``TunnelTicketRequest`` whose ``command`` selects
    the action (``start`` or ``stop``) and whose ``kem_kyber_key`` carries the
    post-quantum KEM public key used to encrypt the response.

    Replaces the previous split between ``/tunnel/ticket`` (legacy GET, 4.x)
    and ``/tunnelpq/ticket`` (modern POST).  Modern tunnel-servers MUST speak
    this protocol; legacy 4.x tunnels are no longer supported.
    """

    ROLE = consts.Role.ANONYMOUS
    SK_TYPE = types.servers.ServerType.TUNNEL
    PATH = "tunnel"
    NAME = "ticket"

    def post(self) -> typing.Any:
        """
        Processes POST requests from modern tunnel-servers.
        """
        logger.debug(
            "Tunnel parameters for POST: %s (%s) from %s",
            self._args,
            self._params,
            self._request.ip,
        )

        if not is_trusted_source(self._request.ip):
            # Invalid requests
            raise exceptions.rest.AccessDenied()

        # The Authorization Bearer header has already been parsed and validated
        # by Handler.__init__; if missing or invalid we never reach this method.
        if not self._sk_token:
            raise exceptions.rest.AccessDenied()

        req = types.tickets.TunnelTicketRequest.from_dict(self._params)

        try:
            ticket = models.TicketStore.get_for_tunnel(req.ticket)
            if ticket.userservice is None or ticket.userservice.user is None:
                raise Exception("Ticket has no associated userservice or the userservice has no user")

            match req.command:
                case "stop":
                    # This data will always be with tz info (from 5.0 onwards)
                    total_time = sql_now() - ticket.started

                    msg = f"User {ticket.userservice.user.name} stopped tunnel {self._sk_token[:8]}... to {ticket.remotes_as_str()}: u:{req.sent}/d:{req.recv}/t:{total_time}."
                    log.log(ticket.userservice.user.manager, types.log.LogLevel.INFO, msg)
                    log.log(ticket.userservice, types.log.LogLevel.INFO, msg)

                    # Try to log Close event.  Note that the userservice may
                    # already be gone; if pool does not exist, do not log.
                    events.add_event(
                        ticket.userservice.service_pool,
                        events.types.stats.EventType.TUNNEL_CLOSE,
                        duration=total_time,
                        sent=req.sent,
                        received=req.recv,
                        tunnel=self._sk_token,
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
                        tunnel=self._sk_token,
                    )
                    msg = f"User {ticket.userservice.user.name} started tunnel {self._sk_token[:8]}... to {ticket.remotes_as_str()} from {req.ip}."
                    log.log(ticket.userservice.user.manager, types.log.LogLevel.INFO, msg)
                    log.log(ticket.userservice, types.log.LogLevel.INFO, msg)
                    # Generate a new notify-only ticket for the userservice
                    # to notify the broker when done.
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


class TunnelRegister(ServerRegisterBase):
    """
    Registers a tunnel-server with UDS.

    Legacy 4.x tunnel-servers do not provide ``os``/``version``/``certificate``
    in the registration payload; we fill in safe defaults so they can still
    register, even though they cannot speak the modern ticket protocol.
    """

    ROLE = consts.Role.ADMIN

    PATH = "tunnel"
    NAME = "register"

    # Just a compatibility method for old tunnel servers
    @typing.override
    def post(self) -> dict[str, typing.Any]:
        self._params["type"] = types.servers.ServerType.TUNNEL
        # ``ServerRegisterBase.post`` already supplies safe defaults for
        # ``os``/``version``/``certificate`` if the caller omits them, so
        # legacy 4.x tunnels that don't send those fields still register.
        return super().post()
