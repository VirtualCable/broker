# -*- coding: utf-8 -*-
#
# Copyright (c) 2023 Virtual Cable S.L.
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

import dataclasses
import datetime
import typing
import json

from uds.core.util.model import sql_now

if typing.TYPE_CHECKING:
    from uds.models import UserService


@dataclasses.dataclass(frozen=True)
class TunnelTicketRemote:
    host: str
    port: int

    extra: dict[str, typing.Any] = dataclasses.field(
        default_factory=dict[str, typing.Any]
    )  # Extra data that can be used for future extensions

    def as_dict(self) -> dict[str, typing.Any]:
        """Returns a dict representation of the remote"""
        return {
            "host": self.host,
            "port": self.port,
            "extra": self.extra,
        }


@dataclasses.dataclass(frozen=True)
class TunnelTicket:
    """Dataclass that represents a tunnel ticket"""

    userservice: "UserService | None"
    remotes: list[TunnelTicketRemote] = dataclasses.field(default_factory=list[TunnelTicketRemote])
    started: datetime.datetime = dataclasses.field(default_factory=sql_now)
    shared_secret: bytes | None = None

    def remotes_as_str(self) -> str:
        """Returns a string representation of the remotes"""
        return ", ".join(f"{r.host}:{r.port}" for r in self.remotes)

    def as_dict(self) -> dict[str, str]:
        """Returns a dict representation of the ticket"""
        return {
            "userservice_uuid": self.userservice.uuid if self.userservice else "",
            "remotes": json.dumps([r.as_dict() for r in self.remotes]),
            "started": self.started.isoformat(),
            "shared_secret": self.shared_secret.hex() if self.shared_secret else "",
        }

    @staticmethod
    def from_dict(data: dict[str, str]) -> "TunnelTicket":
        # Import here to avoid circular imports, global is only for type checking
        from uds.models import UserService

        """Creates a ticket from a dict representation"""
        userservice = UserService.objects.filter(uuid=data["userservice_uuid"]).first()
        userservice_ip = userservice.get_instance().get_ip() if userservice else ""

        remotes: list[TunnelTicketRemote] = []
        if data["remotes"].startswith("["):
            # New JSON format
            for r in json.loads(data["remotes"]):
                remotes.append(
                    TunnelTicketRemote(
                        host=r.get("host") or userservice_ip,
                        port=int(r.get("port", 0)),
                        extra=r.get("extra") or {},
                    )
                )
        else:
            # Old format
            for part in data["remotes"].split("#"):
                if part:
                    host, port = part.split(",")
                    remotes.append(
                        TunnelTicketRemote(
                            host=host or userservice_ip,
                            port=int(port),
                        )
                    )

        return TunnelTicket(
            userservice=userservice,
            remotes=remotes,
            started=datetime.datetime.fromisoformat(data["started"]),
            shared_secret=bytes.fromhex(data["shared_secret"]) if data["shared_secret"] else None,
        )


@dataclasses.dataclass(frozen=True)
class TunnelTicketRequest:
    token: str  # Token provided by the server on registration
    ticket: str  # Ticket string
    command: str  # start/stop right now
    ip: str  # Source IP address (who originates the connection request)
    kem_kyber_key: str = ""  # KEM Kyber public key (base64 encoded, only for start command)
    sent: int = 0  # Used only on stop command
    recv: int = 0  # Used only on stop command

    def as_dict(self) -> dict[str, str | int]:
        """Returns a dict representation of the ticket request"""
        return {
            "token": self.token,
            "ticket": self.ticket,
            "command": self.command,
            "ip": self.ip,
            "kem_kyber_key": self.kem_kyber_key,
            "sent": self.sent,
            "recv": self.recv,
        }

    @staticmethod
    def from_dict(data: dict[str, typing.Any]) -> "TunnelTicketRequest":
        """Creates a ticket request from a dict representation
        Truncates fields to their maximum expected length
        """
        return TunnelTicketRequest(
            token=data["token"][:48],
            ticket=data["ticket"][:48],
            command=data["command"][:16],
            kem_kyber_key=(data.get("kem_kyber_key") or "")[:16384],  # Kem keys can be large
            ip=data["ip"][:32],
            sent=int(data.get("sent") or 0),
            recv=int(data.get("recv") or 0),
        )


@dataclasses.dataclass(frozen=True)
class TunnelTicketResponse:
    remotes: list[TunnelTicketRemote]
    notify: str
    shared_secret: str  # Shared secret in hex

    def as_dict(self) -> dict[str, typing.Any]:
        """Returns a dict representation of the ticket response"""
        return {
            "remotes": [r.as_dict() for r in self.remotes],
            "notify": self.notify,
            "shared_secret": self.shared_secret,
        }

    def as_encrypted_dict(self, kem_key: str, ticket_id: str) -> dict[str, str]:
        from uds.core.managers.crypto import CryptoManager  # Avoid circular import

        (_shared_secret, dct) = CryptoManager.manager().encrypted_dict(
            self.as_dict(),
            ticket_id,
            kem_key_b64=kem_key,
        )
        return dct

    @staticmethod
    def from_dict(data: dict[str, typing.Any]) -> "TunnelTicketResponse":
        """Creates a ticket response from a dict representation"""
        return TunnelTicketResponse(
            remotes=[
                TunnelTicketRemote(
                    host=part["host"],
                    port=int(part["port"]),
                    extra=part.get("extra") or {},
                )
                for part in data["remotes"]
            ],
            notify=data["notify"],
            shared_secret=data["shared_secret"],
        )


@dataclasses.dataclass
class TunnelTicketLegacyResponse:
    """Dataclass that represents a tunnel ticket response"""

    host: str
    port: int
    notify: str
    shared_secret: str | None

    def as_dict(self) -> dict[str, str | int]:
        """Returns a dict representation of the ticket response"""
        return {
            "host": self.host,
            "port": self.port,
            "notify": self.notify,
            "shared_secret": self.shared_secret if self.shared_secret else "",
        }

    @staticmethod
    def from_dict(data: dict[str, typing.Any]) -> "TunnelTicketLegacyResponse":
        """Creates a ticket response from a dict representation"""
        return TunnelTicketLegacyResponse(
            host=data["host"],
            port=int(data["port"]),
            notify=data["notify"],
            shared_secret=data["shared_secret"] if data["shared_secret"] else None,
        )
