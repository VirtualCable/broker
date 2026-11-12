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

import hashlib
import typing
from unittest import mock

from tests.utils.test import UDSTestCase
from uds.services.Harvester.harvester import client as hv_client
from uds.services.Harvester.harvester import exceptions as hv_exceptions


def make_client() -> hv_client.HarvesterClient:
    return hv_client.HarvesterClient(
        endpoint='https://10.1.99.99',
        api_token='tok',
        namespace='default',
        verify_ssl=False,
        timeout=5,
    )


def response_mock(
    status: int = 200,
    json_data: typing.Any = None,
    text: str = '',
) -> mock.Mock:
    response = mock.MagicMock()
    response.status_code = status
    response.ok = 200 <= status < 300
    response.json.return_value = json_data if json_data is not None else {}
    response.text = text
    return response


class TestHarvesterClient(UDSTestCase):
    def test_cache_key_composed(self) -> None:
        client = make_client()
        token_digest = hashlib.sha256(b'tok').hexdigest()
        self.assertEqual(client.cache_key(), f'https://10.1.99.99|{token_digest}|default')
        self.assertNotIn('|tok|', client.cache_key())

    def test_verify_ssl_defaults_to_true(self) -> None:
        client = hv_client.HarvesterClient(endpoint='https://10.1.99.99', api_token='tok')
        self.assertTrue(client.verify_ssl)
        self.assertIs(client._session().verify, True)

    def test_do_request_headers_and_paths(self) -> None:
        client = make_client()
        session = mock.MagicMock()
        session.request.return_value = response_mock(200, {'items': []})
        client._requests_session = session  # type: ignore[attr-defined]
        client.do_request('DELETE', 'x/y')
        args, kwargs = session.request.call_args
        self.assertEqual(args[0], 'DELETE')
        self.assertTrue(args[1].startswith('https://10.1.99.99/x/y'))
        # no body on DELETE: no content type header set
        self.assertIsNone(kwargs['headers'])
    def test_session_carries_auth_header(self) -> None:
        client = make_client()
        session = client._session()
        self.assertTrue(session.verify is False, 'verify_ssl false must reach the requests session')
        self.assertEqual(session.headers.get('Authorization'), 'Bearer tok')

    def test_error_body_not_an_object_is_still_mapped(self) -> None:
        client = make_client()
        session = mock.MagicMock()
        client._requests_session = session  # type: ignore[attr-defined]
        session.request.return_value = response_mock(503, [])
        with self.assertRaises(hv_exceptions.HarvesterConnectionError):
            client.do_request('GET', 'x/y')

    def test_error_mapping(self) -> None:
        client = make_client()
        session = mock.MagicMock()
        client._requests_session = session  # type: ignore[attr-defined]
        errors = {
            401: hv_exceptions.HarvesterAuthError,
            403: hv_exceptions.HarvesterPermissionError,
            404: hv_exceptions.HarvesterNotFoundError,
            500: hv_exceptions.HarvesterError,
        }
        for status, expected in errors.items():
            session.request.return_value = response_mock(status, {'message': 'boom'})
            with self.assertRaises(expected):
                client.do_request('GET', 'x/y')

    def test_list_templates_maps_fields(self) -> None:
        client = make_client()
        session = mock.MagicMock()
        session.request.return_value = response_mock(
            200,
            {
                'items': [
                    {
                        'metadata': {'name': 't1', 'namespace': 'default'},
                        'spec': {'defaultVersionId': 'default/t1-v2', 'description': 'd'},
                    },
                    {
                        'metadata': {'name': 't2', 'namespace': 'default'},
                        'spec': {},
                    },
                ]
            },
        )
        client._requests_session = session  # type: ignore[attr-defined]
        templates = client.list_templates('default')
        self.assertEqual(len(templates), 2)
        self.assertEqual(templates[0].name, 't1')
        self.assertEqual(templates[0].default_version_id, 'default/t1-v2')
        self.assertTrue(templates[0].is_usable())
        self.assertFalse(templates[1].is_usable())

    def test_version_ref_namespace_split(self) -> None:
        client = make_client()
        session = mock.MagicMock()
        session.request.return_value = response_mock(
            200,
            {
                'metadata': {'name': 't1-v1', 'namespace': 'default'},
                'spec': {'templateId': 'default/t1'},
                'status': {
                    'version': 1,
                    'conditions': [{'type': 'ready', 'status': 'True'}],
                },
            },
        )
        client._requests_session = session  # type: ignore[attr-defined]
        version = client.get_template_version('default', 'default/t1-v1')
        self.assertEqual(version.name, 't1-v1')
        self.assertTrue(version.ready)
        args, _kwargs = session.request.call_args
        self.assertIn('/namespaces/default/virtualmachinetemplateversions/t1-v1', args[1])

    def test_create_vm_body_uses_template_spec(self) -> None:
        client = make_client()
        session = mock.MagicMock()
        session.request.return_value = response_mock(201, {})
        client._requests_session = session  # type: ignore[attr-defined]
        vm_spec = {
            'running': False,
            'template': {
                'spec': {
                    'domain': {'devices': {'interfaces': [{'name': 'nic-0', 'model': 'virtio'}], 'disks': []}}
                }
            },
            'dataVolumeTemplates': [
                {
                    'metadata': {'name': 'dv-image'},
                    'spec': {'source': {'pvc': {'namespace': 'default', 'name': 'vm-disk'}}},
                }
            ],
            'volumes': [{'name': 'disk0', 'dataVolume': {'name': 'dv-image'}}],
        }
        vm_metadata = {'labels': {'harvesterhci.io/creator': 'harvester'}, 'annotations': {'a': '1'}}
        client.create_vm('uds-vm', vm_spec, running=True, vm_metadata=vm_metadata)
        args, kwargs = session.request.call_args
        self.assertEqual(args[0], 'POST')
        body = kwargs['json']
        self.assertEqual(body['kind'], 'VirtualMachine')
        self.assertEqual(body['metadata']['name'], 'uds-vm')
        self.assertEqual(body['metadata']['annotations'], {'a': '1'})
        self.assertEqual(body['metadata']['labels']['uds.io/managed'], 'true')
        self.assertEqual(body['metadata']['labels']['harvesterhci.io/creator'], 'harvester')
        # full spec kept: dataVolumeTemplates and volumes reach the request body
        self.assertTrue(body['spec']['running'])
        self.assertEqual(body['spec']['dataVolumeTemplates'], vm_spec['dataVolumeTemplates'])
        self.assertEqual(body['spec']['volumes'], vm_spec['volumes'])
        iface = body['spec']['template']['spec']['domain']['devices']['interfaces'][0]
        # classic inline binding (pod network); binding plugins are NOT used because
        # they require cluster configuration that may not exist (masquerade plugin)
        self.assertNotIn('binding', iface)
        self.assertEqual(iface['masquerade'], {})
        # missing pod network gets created to match the interface
        self.assertEqual(
            body['spec']['template']['spec']['networks'], [{'name': 'nic-0', 'pod': {}}]
        )
        # original spec untouched
        self.assertIn('model', vm_spec['template']['spec']['domain']['devices']['interfaces'][0])  # type: ignore[index]

    def test_merge_patch_is_json_encoded(self) -> None:
        client = make_client()
        session = mock.MagicMock()
        session.request.return_value = response_mock(200, {})
        client._requests_session = session  # type: ignore[attr-defined]
        client.set_vm_running('vm1', False)
        args, kwargs = session.request.call_args
        self.assertEqual(args[0].upper(), 'PATCH')
        self.assertEqual(kwargs['headers']['Content-Type'], 'application/merge-patch+json')
        self.assertEqual(kwargs['json'], {'spec': {'running': False}})

    def test_transient_errors_are_retryable(self) -> None:
        client = make_client()
        session = mock.MagicMock()
        client._requests_session = session  # type: ignore[attr-defined]
        for status in (429, 500, 502, 503):
            session.request.return_value = response_mock(status, {'message': 'busy'})
            with self.assertRaises(hv_exceptions.HarvesterConnectionError):
                client.do_request('GET', 'x/y')

    def test_get_vm_info_missing_vmi(self) -> None:
        client = make_client()
        session = mock.MagicMock()
        session.request.side_effect = [
            response_mock(200, {'metadata': {'name': 'vm1'}}),  # VM
            response_mock(404, {'message': 'Not Found'}),  # VMI (not running)
        ]
        client._requests_session = session  # type: ignore[attr-defined]
        info = client.get_vm_info('vm1')
        self.assertTrue(info.exists)
        self.assertFalse(info.running)
        self.assertEqual(info.first_mac, '')

    def test_get_vm_info_not_found(self) -> None:
        client = make_client()
        session = mock.MagicMock()
        session.request.return_value = response_mock(404, {'message': 'Not Found'})
        client._requests_session = session  # type: ignore[attr-defined]
        info = client.get_vm_info('vm1')
        self.assertFalse(info.exists)

    def test_interfaces_read_from_vmi(self) -> None:
        client = make_client()
        session = mock.MagicMock()
        session.request.side_effect = [
            response_mock(200, {'metadata': {'name': 'vm1'}}),
            response_mock(
                200,
                {
                    'metadata': {'name': 'vm1'},
                    'status': {
                        'phase': 'Running',
                        'interfaces': [
                            {'mac': '52:54:00:aa:bb:cc', 'ipAddress': '10.1.250.7'}
                        ],
                    },
                },
            ),
        ]
        client._requests_session = session  # type: ignore[attr-defined]
        info = client.get_vm_info('vm1')
        self.assertTrue(info.running)
        self.assertEqual(info.first_mac, '52:54:00:aa:bb:cc')
        self.assertEqual(info.first_ip, '10.1.250.7')

    def test_set_vm_running_uses_merge_patch(self) -> None:
        client = make_client()
        session = mock.MagicMock()
        session.request.return_value = response_mock(200, {})
        client._requests_session = session  # type: ignore[attr-defined]
        client.set_vm_running('vm1', False)
        args, kwargs = session.request.call_args
        self.assertEqual(args[0].upper(), 'PATCH')
        self.assertEqual(kwargs['headers']['Content-Type'], 'application/merge-patch+json')
        self.assertEqual(kwargs['json'], {'spec': {'running': False}})

    def test_connection_error_is_retryable(self) -> None:
        import requests

        client = make_client()
        session = mock.MagicMock()
        session.request.side_effect = requests.exceptions.ConnectionError('no route')
        client._requests_session = session  # type: ignore[attr-defined]
        with self.assertRaises(hv_exceptions.HarvesterConnectionError):
            client.do_request('GET', 'x/y')

    def test_delete_template_version_already_gone(self) -> None:
        client = make_client()
        session = mock.MagicMock()
        session.request.return_value = response_mock(404, {'message': 'Not Found'})
        client._requests_session = session  # type: ignore[attr-defined]
        client.delete_template_version('default', 'default/uds-gone')
        args, _kwargs = session.request.call_args
        self.assertEqual(args[0], 'DELETE')
        self.assertIn('/namespaces/default/virtualmachinetemplateversions/uds-gone', args[1])
