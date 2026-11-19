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

import pickle

from tests.utils.test import UDSTransactionTestCase
from uds.services.Harvester import deployment, provider, publication, service

from tests.services.harvester import fixtures


class TestHarvesterSerialization(UDSTransactionTestCase):
    """Provider, service, publication and service instances roundtrip."""

    def check_provider(self, inst: provider.HarvesterProvider) -> None:
        self.assertEqual(inst.api_url.value, fixtures.PROVIDER_VALUES_DICT['api_url'])
        self.assertEqual(inst.api_token.value, fixtures.PROVIDER_VALUES_DICT['api_token'])
        self.assertEqual(inst.namespace.value, fixtures.PROVIDER_VALUES_DICT['namespace'])
        self.assertEqual(inst.timeout.as_int(), fixtures.PROVIDER_VALUES_DICT['timeout'])

    def check_service(self, inst: 'service.HarvesterService') -> None:
        self.assertEqual(inst.template.value, fixtures.SERVICE_VALUES_DICT['template'])
        self.assertEqual(inst.basename.value, fixtures.SERVICE_VALUES_DICT['basename'])
        self.assertEqual(inst.lenname.value, fixtures.SERVICE_VALUES_DICT['lenname'])

    def check_publication(self, inst: 'publication.HarvesterTemplatePublication') -> None:
        self.assertEqual(inst._name, 'pub-1')
        self.assertEqual(inst._version_name, 'ver-1')
    def check_userservice(self, inst: 'deployment.HarvesterUserService') -> None:
        self.assertEqual(inst._name, 'depl-1')
        self.assertEqual(inst._vmid, 'vm-1')

    def test_provider_roundtrip(self) -> None:
        prov = fixtures.create_provider()
        data = pickle.dumps(prov)
        prov2: provider.HarvesterProvider = pickle.loads(data)
        self.check_provider(prov2)

    def test_service_roundtrip(self) -> None:
        srv = fixtures.create_service()
        data = pickle.dumps(srv)
        srv2: service.HarvesterService = pickle.loads(data)
        self.check_service(srv2)

    def test_publication_roundtrip(self) -> None:
        pub = self.make_publication()
        pub._name = 'pub-1'
        pub._version_name = 'ver-1'
        data = pickle.dumps(pub)
        pub2: publication.HarvesterTemplatePublication = pickle.loads(data)
        self.check_publication(pub2)

    def test_userservice_roundtrip(self) -> None:
        srv = self.make_userservice()
        srv._name = 'depl-1'
        srv._vmid = 'vm-1'
        data = pickle.dumps(srv)
        srv2: deployment.HarvesterUserService = pickle.loads(data)
        self.check_userservice(srv2)

    def make_publication(self) -> 'publication.HarvesterTemplatePublication':
        return fixtures.create_publication()

    def make_userservice(self) -> 'deployment.HarvesterUserService':
        return fixtures.create_userservice()
