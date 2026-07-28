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
OpenGnsys secure-callback tests.

These instantiate the real ``OGService`` against a mocked provider and check
that ``gui_description()`` swaps the naked ``prov_uuid`` for an opaque
``cb_ticket`` ticket that resolves back to the provider's uuid.
"""

from unittest import mock

from tests.services.cb_helpers import assert_secure_callbacks
from tests.utils.test import UDSTestCase
from uds.core import environment
from uds.services.OpenGnsys import provider as og_provider
from uds.services.OpenGnsys import service as og_service


class OpenGnsysServiceSecureCallbacksTests(UDSTestCase):
    def test_ou_uses_cb_ticket(self) -> None:
        prov = og_provider.OGProvider(
            environment=environment.Environment.private_environment("prov-uuid"),
            values={
                "host": "host",
                "port": 443,
                "verify_ssl": False,
                "username": "user",
                "password": "pass",
                "uds_endpoint": "https://uds/",
                "concurrent_creation_limit": 1,
                "concurrent_removal_limit": 1,
                "timeout": 10,
            },
            uuid="prov-uuid",
        )
        service = og_service.OGService(
            environment=environment.Environment.private_environment("svc-uuid"),
            provider=prov,
            values={
                "ou": "",
                "image": "",
                "lab": 0,
                "max_reserve_hours": 1,
                "start_if_unavailable": False,
                "basename": "b",
                "lenname": 5,
                "maintain_on_error": False,
                "try_soft_shutdown": False,
                "prov_uuid": "prov-uuid",
            },
            uuid="svc-uuid",
        )
        db_obj = mock.MagicMock()
        db_obj.uuid = "prov-uuid"
        api_mock = mock.MagicMock()
        api_mock.list_of_ous.return_value = []
        prov._api = api_mock
        with mock.patch.object(prov, "db_obj", return_value=db_obj):
            assert_secure_callbacks(self, service, ("ou",))
