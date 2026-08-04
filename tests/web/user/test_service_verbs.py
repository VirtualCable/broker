# -*- coding: utf-8 -*-
#
# Copyright (c) 2024 Virtual Cable S.L.U.
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
#    * Neither the name of Virtual Cable S.L.U. nor the names of its contributors
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
import typing

from django.urls import reverse

from ...utils.web import test

# Dummy identifiers: the verb guard rejects GET before the view body ever
# looks them up, so they only need to match the URL regex.
DUMMY_SERVICE: typing.Final[str] = 'A0000000-0000-0000-0000-000000000000'
DUMMY_TRANSPORT: typing.Final[str] = 'T0000000-0000-0000-0000-000000000000'


class UserServiceVerbsTest(test.WEBTestCase):
    """
    The WebAPI endpoints that mutate state must only accept POST; the read-only
    status endpoint must keep answering GET (compat with the polling loop).
    """

    def setUp(self) -> None:
        super().setUp()
        self.login(as_admin=False)

    def test_mutating_endpoints_reject_get(self) -> None:
        for name, kwargs in (
            ('webapi.enabler', {'service_id': DUMMY_SERVICE, 'transport_id': DUMMY_TRANSPORT}),
            ('webapi.action', {'service_id': DUMMY_SERVICE, 'action_string': 'release'}),
            ('webapi.transport_own_link', {'service_id': DUMMY_SERVICE, 'transport_id': DUMMY_TRANSPORT}),
        ):
            response = self.client.get(reverse(name, kwargs=kwargs))
            self.assertEqual(response.status_code, 405, f'{name} must reject GET')

    def test_mutating_endpoint_accepts_post(self) -> None:
        # transport_own_link wraps everything in try/except and always answers
        # 200 JSON, so a POST with unknown ids still proves the verb is allowed.
        response = self.client.post(
            reverse(
                'webapi.transport_own_link',
                kwargs={'service_id': DUMMY_SERVICE, 'transport_id': DUMMY_TRANSPORT},
            )
        )
        self.assertEqual(response.status_code, 200)

    def test_status_endpoint_keeps_get(self) -> None:
        response = self.client.get(
            reverse(
                'webapi.status',
                kwargs={'service_id': DUMMY_SERVICE, 'transport_id': DUMMY_TRANSPORT},
            )
        )
        self.assertEqual(response.status_code, 200)
