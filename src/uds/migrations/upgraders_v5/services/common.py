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
import logging
import typing

from django.db import models

from uds.core.util.autoserializable import HEADER_BASE, HEADER_COMPRESSED, HEADER_ENCRYPTED
from uds.models import Provider, Service, ServicePoolPublication, UserService

logger = logging.getLogger(__name__)

_NEW_FORMAT_PREFIXES: typing.Final[tuple[str, ...]] = tuple(
    base64.b64encode(h).decode() for h in (HEADER_BASE, HEADER_COMPRESSED, HEADER_ENCRYPTED)
)


def upgrade_user_services(service_data_type: str) -> int:
    """
    Upgrades legacy UserService data for a given service type by triggering
    the existing lazy-upgrade path (get_instance → unmarshal → mark_for_upgrade → re-save).

    Records already in AutoSerializable format (base64 prefix match) are skipped.
    """
    qs = UserService.objects.filter(
        deployed_service__service__in=Service.objects.filter(data_type=service_data_type)
    )

    exclude_q = models.Q()
    for prefix in _NEW_FORMAT_PREFIXES:
        exclude_q |= models.Q(data__startswith=prefix)
    qs = qs.exclude(exclude_q)

    upgraded = 0
    for us in qs.iterator(chunk_size=100):
        try:
            us.get_instance()
            upgraded += 1
        except Exception:
            logger.exception('Error upgrading UserService %s', us.uuid)

    logger.info('Upgraded %d UserServices for %s', upgraded, service_data_type)
    return upgraded


def upgrade_publications(service_data_type: str) -> int:
    qs = ServicePoolPublication.objects.filter(
        deployed_service__service__in=Service.objects.filter(data_type=service_data_type)
    )

    exclude_q = models.Q()
    for prefix in _NEW_FORMAT_PREFIXES:
        exclude_q |= models.Q(data__startswith=prefix)
    qs = qs.exclude(exclude_q)

    upgraded = 0
    for pub in qs.iterator(chunk_size=100):
        try:
            pub.get_instance()
            upgraded += 1
        except Exception:
            logger.exception('Error upgrading Publication %s', pub.uuid)

    logger.info('Upgraded %d Publications for %s', upgraded, service_data_type)
    return upgraded


def upgrade_providers(provider_data_type: str) -> int:
    qs = Provider.objects.filter(data_type=provider_data_type)

    exclude_q = models.Q()
    for prefix in _NEW_FORMAT_PREFIXES:
        exclude_q |= models.Q(data__startswith=prefix)
    qs = qs.exclude(exclude_q)

    upgraded = 0
    for prov in qs.iterator(chunk_size=100):
        try:
            prov.get_instance()
            upgraded += 1
        except Exception:
            logger.exception('Error upgrading Provider %s', prov.uuid)

    logger.info('Upgraded %d Providers for %s', upgraded, provider_data_type)
    return upgraded


def upgrade_services(service_data_type: str) -> int:
    qs = Service.objects.filter(data_type=service_data_type)

    exclude_q = models.Q()
    for prefix in _NEW_FORMAT_PREFIXES:
        exclude_q |= models.Q(data__startswith=prefix)
    qs = qs.exclude(exclude_q)

    upgraded = 0
    for svc in qs.iterator(chunk_size=100):
        try:
            svc.get_instance()
            upgraded += 1
        except Exception:
            logger.exception('Error upgrading Service %s', svc.uuid)

    logger.info('Upgraded %d Services for %s', upgraded, service_data_type)
    return upgraded
