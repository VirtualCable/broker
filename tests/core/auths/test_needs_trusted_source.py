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
Author: Andres Schumann, aschumann at virtualcable dot es

needs_trusted_source must deny when the trusted-source list cannot be evaluated.
"""

import typing
from unittest import mock

from django.http import HttpResponse, HttpResponseForbidden

from uds.core.auths import auth

from tests.utils.test import UDSTestCase


class _Request:
    ip = "198.51.100.7"


def _view(request: typing.Any) -> HttpResponse:
    return HttpResponse("ok", status=200)


class NeedsTrustedSourceTest(UDSTestCase):
    def _call(self, trusted: typing.Any) -> tuple[HttpResponse, mock.Mock]:
        view = mock.Mock(wraps=_view)
        guarded = auth.needs_trusted_source(view)
        with mock.patch.object(auth, "is_trusted_source", side_effect=trusted):
            response = guarded(_Request())
        return response, view

    def test_trusted_ip_reaches_the_view(self) -> None:
        response, view = self._call(lambda ip: True)
        self.assertEqual(response.status_code, 200)
        view.assert_called_once()

    def test_untrusted_ip_is_denied(self) -> None:
        response, view = self._call(lambda ip: False)
        self.assertIsInstance(response, HttpResponseForbidden)
        view.assert_not_called()

    def test_failed_evaluation_is_denied_not_open(self) -> None:
        def broken(ip: str) -> bool:
            raise ValueError("not a valid network list")

        response, view = self._call(broken)
        self.assertIsInstance(response, HttpResponseForbidden)
        view.assert_not_called()
