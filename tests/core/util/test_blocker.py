#
# Copyright (c) 2012-2022 Virtual Cable S.L.
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
Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""
import typing

from uds.core import consts, exceptions
from uds.core.util.decorators import blocker

from ...utils.test import UDSTransactionTestCase


class FakeRequest:
    ip = '127.0.0.1'


def build_endpoint(module: str) -> type:
    """
    Builds an endpoint type named Endpoint, living in the given module, whose post is protected.
    Two modules holding a same named class must not share the failures counter.
    """

    def post(self: typing.Any) -> str:
        if self.must_fail:
            raise exceptions.rest.BlockAccess()
        return 'ok'

    post.__module__ = module
    post.__qualname__ = 'Endpoint.post'

    def init(self: typing.Any, must_fail: bool) -> None:
        self._request = FakeRequest()
        self.must_fail = must_fail

    return type('Endpoint', (), {'__init__': init, 'post': blocker()(post)})


class BlockerTest(UDSTransactionTestCase):
    def test_failures_are_not_shared_between_equally_named_endpoints(self) -> None:
        blocked = build_endpoint('uds.tests.one')
        twin = build_endpoint('uds.tests.other')

        for _ in range(consts.system.ALLOWED_FAILS):
            with self.assertRaises(exceptions.rest.AccessDenied):
                blocked(must_fail=True).post()

        with self.assertRaises(exceptions.rest.AccessDenied):
            blocked(must_fail=False).post()

        self.assertEqual(twin(must_fail=False).post(), 'ok')
