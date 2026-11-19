#
# Copyright (c) 2026 Virtual Cable S.L.U.
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
"""

from unittest import mock

from tests.utils.test import UDSTestCase
from uds.services.Harvester import provider

from tests.services.harvester import fixtures


class TestHarvesterProvider(UDSTestCase):
    def test_provider_values(self) -> None:
        """Provider config is kept as given and the api client reflects it."""
        with fixtures.patched_provider() as prov:
            self.assertEqual(prov.api_url.value, 'https://10.1.99.99')
            self.assertEqual(prov.namespace.value, 'default')
            # patched api is the one used
            self.assertIs(prov.api, prov._cached_api)

    def test_provider_api_clears_client_on_initialize(self) -> None:
        """Config changes invalidate the cached api."""
        prov = fixtures.create_provider()
        old_api = prov.api
        prov.initialize(None)
        self.assertIsNot(old_api, prov.api)

    def test_provider_values_fields(self) -> None:
        """Provider fields carry the values used to build it."""
        prov = fixtures.create_provider()
        self.assertEqual(prov.api_url.value, fixtures.PROVIDER_VALUES_DICT['api_url'])
        self.assertEqual(prov.namespace.value, fixtures.PROVIDER_VALUES_DICT['namespace'])
        self.assertEqual(prov.timeout.as_int(), fixtures.PROVIDER_VALUES_DICT['timeout'])
        self.assertEqual(prov.verify_ssl.value, fixtures.PROVIDER_VALUES_DICT['verify_ssl'])

    def test_provider_api_uses_verify_ssl_field(self) -> None:
        prov = fixtures.create_provider()
        self.assertIs(prov.api.verify_ssl, prov.verify_ssl.as_bool())

    def test_provider_test_connection(self) -> None:
        """
        Static test(): with api working -> ok result; without -> error result.
        """
        from uds.core import environment

        for expected, ret_val in ((True, True), (False, False)):
            with mock.patch(
                'uds.services.Harvester.provider.HarvesterProvider.test_connection',
                return_value=ret_val,
            ):
                result = provider.HarvesterProvider.test(
                    environment.Environment.temporary_environment(),  # type: ignore[arg-type]
                    fixtures.PROVIDER_VALUES_DICT,
                )
            self.assertEqual(result.success, expected)

    def test_sanitized_name(self) -> None:
        prov = fixtures.create_provider()
        self.assertEqual(prov.sanitized_name('VM name With Spaces'), 'vm-name-with-spaces')
        self.assertEqual(prov.sanitized_name('UPPER_case__and..dots'), 'upper-case-and..dots')
        self.assertEqual(prov.sanitized_name('a' * 100), 'a' * 63)

    def test_test_connection_false_on_error(self) -> None:
        with fixtures.patched_provider() as prov:
            prov.api.test.return_value = False
            self.assertFalse(prov.test_connection())
