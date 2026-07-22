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
Author: Adolfo Gomez, dkmaster at dkmon dot com
"""

import base64
import dataclasses
import logging
import typing

from django.db import models

from uds.core.util.autoserializable import HEADER_BASE, HEADER_COMPRESSED, HEADER_ENCRYPTED
from uds.models import OSManager

logger = logging.getLogger(__name__)

_NEW_FORMAT_PREFIXES: typing.Final[tuple[str, ...]] = tuple(
    base64.b64encode(h).decode() for h in (HEADER_BASE, HEADER_COMPRESSED, HEADER_ENCRYPTED)
)


@dataclasses.dataclass(frozen=True)
class OSManagerTypeInfo:
    name: str = ""


OSMANAGER_TYPES: typing.Final[dict[str, OSManagerTypeInfo]] = {
    "LinuxManager": OSManagerTypeInfo(name="Linux OS Manager"),
    "LinRandomPasswordManager": OSManagerTypeInfo(name="Linux Random Password"),
    "WinDomainManager": OSManagerTypeInfo(name="Windows Domain"),
    "WindowsManager": OSManagerTypeInfo(name="Windows OS Manager"),
    "WinRandomPasswordManager": OSManagerTypeInfo(name="Windows Random Password"),
}


def upgrade_osmanagers() -> dict[str, int]:
    results: dict[str, int] = {}
    for type_type in OSMANAGER_TYPES:
        qs = OSManager.objects.filter(data_type=type_type)

        exclude_q = models.Q()
        for prefix in _NEW_FORMAT_PREFIXES:
            exclude_q |= models.Q(data__startswith=prefix)
        qs = qs.exclude(exclude_q)

        upgraded = 0
        for osm in qs.iterator(chunk_size=100):
            try:
                osm.get_instance()
                upgraded += 1
            except Exception:
                logger.exception("Error upgrading OSManager %s", osm.uuid)

        logger.info("Upgraded %d OSManagers for %s", upgraded, type_type)
        results[type_type] = upgraded
    return results
