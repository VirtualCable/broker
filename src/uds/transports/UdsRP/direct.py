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

import logging
import typing

from django.utils.translation import gettext_noop as _

from uds import models
from uds.core import types

from .common import BaseUdsRPTransport

# Not imported at runtime, just for type checking
if typing.TYPE_CHECKING:
    from uds.core.types.requests import ExtendedHttpRequestWithUser

logger: logging.Logger = logging.getLogger(__name__)

READY_CACHE_TIMEOUT = 30


class UdsRPTransport(BaseUdsRPTransport):
    """
    UDS Remote Protocol direct transport: the client connects straight to
    the URP service listening on the VM (no tunnel).
    """

    is_base = False

    type_name = _("UDS Remote Protocol")
    type_type = "UdsRPTransport"
    type_description = _("UDS Remote Protocol client. Direct connection.")
    icon_file = "udsrp.png"

    # GUI fields are not inherited into the form: re-export them all.
    force_empty_creds = BaseUdsRPTransport.force_empty_creds
    forced_username = BaseUdsRPTransport.forced_username
    forced_password = BaseUdsRPTransport.forced_password
    force_no_domain = BaseUdsRPTransport.force_no_domain
    forced_domain = BaseUdsRPTransport.forced_domain

    enable_audio = BaseUdsRPTransport.enable_audio
    enable_microphone = BaseUdsRPTransport.enable_microphone
    enable_clipboard = BaseUdsRPTransport.enable_clipboard
    enable_storage = BaseUdsRPTransport.enable_storage
    enable_printers = BaseUdsRPTransport.enable_printers
    enable_webcam = BaseUdsRPTransport.enable_webcam
    enable_smartcard = BaseUdsRPTransport.enable_smartcard
    allow_lock = BaseUdsRPTransport.allow_lock
    wallpaper_off = BaseUdsRPTransport.wallpaper_off

    screen_width = BaseUdsRPTransport.screen_width
    screen_height = BaseUdsRPTransport.screen_height
    session_fps = BaseUdsRPTransport.session_fps

    server_port = BaseUdsRPTransport.server_port
    preferred_codec = BaseUdsRPTransport.preferred_codec
    console_policy = BaseUdsRPTransport.console_policy
    console_takeover = BaseUdsRPTransport.console_takeover
    console_conflict_timeout = BaseUdsRPTransport.console_conflict_timeout
    ticket_validity = BaseUdsRPTransport.ticket_validity

    @typing.override
    def is_ip_allowed(self, userservice: "models.UserService", ip: str) -> bool:
        """
        Checks if the URP service is reachable on the destination ip.
        """
        logger.debug("Checking URP availability for %s", ip)
        ready = self.cache.get(ip)
        if not ready:
            if self.test_connectivity(userservice, ip, self.server_port.as_int()) is True:
                self.cache.put(ip, "Y", READY_CACHE_TIMEOUT)
                return True
            self.cache.put(ip, "N", READY_CACHE_TIMEOUT)
        return ready == "Y"

    @typing.override
    def get_transport_script(
        self,
        userservice: "models.UserService",
        transport: "models.Transport",
        ip: str,
        os: "types.os.DetectedOsInfo",
        user: "models.User",
        password: str,
        request: "ExtendedHttpRequestWithUser",
    ) -> "types.transports.TransportScript":
        if os.os not in (types.os.KnownOS.WINDOWS, types.os.KnownOS.LINUX, types.os.KnownOS.MAC_OS):
            logger.error(
                "OS not valid for URP transport: %s",
                request.META.get("HTTP_USER_AGENT", "Unknown"),
            )
            return super().get_transport_script(userservice, transport, ip, os, user, password, request)

        ci = self.get_connection_info(userservice, user, password)

        # Session ticket for the VM agent (consumed via actor/v3 ticket
        # endpoint). The session PSK is the script envelope's shared secret,
        # stored on this ticket by TransportScript.as_encrypted_dict.
        ticket = models.TicketStore.create_for_tunnel(
            userservice=userservice,
            port=self.server_port.as_int(),
            extra=self.build_ticket_payload(ci).as_dict(),
            validity=self.ticket_validity.as_int(),
        )

        params = self.build_connection_params(ip, ticket, ci)
        return self.get_script(os.os.os_name(), "direct", params.as_dict(), associated_ticket=ticket)
