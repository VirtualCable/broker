# -*- coding: utf-8 -*-

#
# Copyright (c) 2012-2019 Virtual Cable S.L.
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

import base64
import bz2
import collections.abc
import dataclasses
import enum
import json
import typing

from django.utils.translation import gettext
from django.utils.translation import gettext_noop as _


class Protocol(enum.StrEnum):
    NONE = ""
    RDP = "rdp"
    RDS = "rds"  # In fact, RDS (Remote Desktop Services) is RDP, but have "more info" for connection that RDP
    SPICE = "spice"
    VNC = "vnc"
    PCOIP = "pcoip"
    REMOTEFX = "remotefx"  # This in fact is RDP als
    HDX = "hdx"
    ICA = "ica"
    NX = "nx"
    X11 = "x11"
    X2GO = "x2go"  # Based on NX
    NICEDCV = "nicedcv"
    SSH = "ssh"
    OTHER = "other"

    @staticmethod
    def generic_vdi(*extra: "Protocol") -> tuple["Protocol", ...]:
        return (
            Protocol.RDP,
            Protocol.VNC,
            Protocol.NX,
            Protocol.X11,
            Protocol.X2GO,
            Protocol.PCOIP,
            Protocol.NICEDCV,
            Protocol.SSH,
            Protocol.OTHER,
        ) + extra


class Grouping(enum.StrEnum):
    DIRECT = _("Direct")
    TUNNELED = _("Tunneled")

    def localized(self) -> str:
        return gettext(self.value)


class ScriptType(enum.StrEnum):
    JAVASCRIPT = "javascript"


class SignatureAlgorithm(enum.StrEnum):
    MLDSA65 = "MLDSA65"  # Post quantum safe algorithm


@dataclasses.dataclass
class TransportLog:
    level: str = "info"  # info, debug, error
    ticket: str | None = None

    def as_dict(self) -> dict[str, typing.Any]:
        return {
            "level": self.level,
            "ticket": self.ticket,
        }


@dataclasses.dataclass
class TransportScript:
    script: str
    script_type: ScriptType
    signature_algorithm: SignatureAlgorithm
    signature_b64: str  # Signature of the script in base64
    parameters: collections.abc.Mapping[str, typing.Any] = dataclasses.field(default_factory=dict[str, typing.Any])
    log: TransportLog = dataclasses.field(default_factory=TransportLog)
    associated_ticket: str | None = None  # Ticket associated with this script, if any

    @property
    def encoded_parameters(self) -> str:
        """
        Returns encoded parameters for transport script
        """
        return base64.b64encode(bz2.compress(json.dumps(self.parameters).encode())).decode()

    @property
    def encoded_script(self) -> str:
        """
        Returns encoded script
        """
        return base64.b64encode(bz2.compress(self.script.encode())).decode()

    def as_dict(self) -> dict[str, typing.Any]:
        return {
            "script": self.encoded_script,
            "type": self.script_type,
            "signature_algorithm": self.signature_algorithm,
            "signature": self.signature_b64,
            "params": self.encoded_parameters,
            "log": self.log.as_dict(),
        }

    def as_encrypted_dict(self, kem_key: str, ticket_id: str) -> dict[str, str]:
        from uds.core.managers.crypto import CryptoManager  # Avoid circular import
        from uds.models.ticket_store import TicketStore  # Avoid circular import

        (shared_secret, dct) = CryptoManager.manager().encrypted_dict(
            self.as_dict(),
            ticket_id,
            kem_key_b64=kem_key,
        )

        # Associated ticket needs to have the shared secret to be accesed later
        if self.associated_ticket is not None:
            TicketStore.set_shared_secret(self.associated_ticket, shared_secret)

        return dct
