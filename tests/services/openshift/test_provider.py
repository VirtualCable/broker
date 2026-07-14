# -*- coding: utf-8 -*-

#
# Copyright (c) 2024 Virtual Cable S.L.
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
import typing
from unittest import mock

from uds.core import types, ui, environment
from uds.services.OpenShift.provider import OpenshiftProvider

from . import fixtures

from tests.utils.test import UDSTransactionTestCase


class TestOpenshiftProvider(UDSTransactionTestCase):
    def setUp(self) -> None:
        """
        Set up test environment and clear fixtures before each test.
        """
        super().setUp()
        fixtures.clear()

    # --- Provider Data Tests ---
    def test_provider_data(self) -> None:
        """
        Test provider data fields and types for correct initialization.
        """
        provider = fixtures.create_provider()
        self.assertEqual(provider.cluster_url.value, fixtures.PROVIDER_VALUES_DICT['cluster_url'])
        self.assertEqual(provider.api_url.value, fixtures.PROVIDER_VALUES_DICT['api_url'])
        self.assertEqual(provider.username.value, fixtures.PROVIDER_VALUES_DICT['username'])
        self.assertEqual(provider.password.value, fixtures.PROVIDER_VALUES_DICT['password'])
        self.assertEqual(provider.namespace.value, fixtures.PROVIDER_VALUES_DICT['namespace'])
        self.assertEqual(provider.verify_ssl.value, fixtures.PROVIDER_VALUES_DICT['verify_ssl'])
        if not isinstance(provider.concurrent_creation_limit, ui.gui.NumericField):
            self.fail('concurrent_creation_limit is not a NumericField')
        self.assertEqual(provider.concurrent_creation_limit.as_int(), fixtures.PROVIDER_VALUES_DICT['concurrent_creation_limit'])
        if not isinstance(provider.concurrent_removal_limit, ui.gui.NumericField):
            self.fail('concurrent_removal_limit is not a NumericField')
        self.assertEqual(provider.concurrent_removal_limit.as_int(), fixtures.PROVIDER_VALUES_DICT['concurrent_removal_limit'])
        self.assertEqual(provider.timeout.as_int(), fixtures.PROVIDER_VALUES_DICT['timeout'])

    # --- Provider Test Method ---
    def test_provider_test(self) -> None:
        """
        Test the static provider test method and test_connection logic.
        """
        with fixtures.patched_provider() as provider:
            api = typing.cast(mock.MagicMock, provider.api)
            for ret_val in [True, False]:
                api.test.reset_mock()
                api.test.return_value = ret_val
                # Patch test_connection to return ret_val for static test
                with mock.patch('uds.services.OpenShift.provider.OpenshiftProvider.test_connection', return_value=ret_val):
                    result = OpenshiftProvider.test(environment.Environment.temporary_environment(), fixtures.PROVIDER_VALUES_DICT)
                self.assertIsInstance(result, types.core.TestResult)
                self.assertEqual(result.success, ret_val)
                self.assertIsInstance(result.error, str)
                # Ensure test_connection calls api.test
                provider.test_connection()
                api.test.assert_called_once_with()

    # --- Provider Availability ---
    def test_provider_is_available(self) -> None:
        """
        Test the provider is_available method and cache behavior.
        """
        with fixtures.patched_provider() as provider:
            api = typing.cast(mock.MagicMock, provider.api)
            # First, true result
            self.assertEqual(provider.is_available(), True)
            api.test.assert_called_once_with()
            api.test.reset_mock()
            # Now, even if set test to false, should return true due to cache
            api.test.return_value = False
            self.assertEqual(provider.is_available(), True)
            api.test.assert_not_called()
            # clear cache of method
            provider.is_available.cache_clear()  # type: ignore  # cache_clear() is added by decorator
            self.assertEqual(provider.is_available(), False)
            api.test.assert_called_once_with()

    # --- Provider API Methods ---
    def test_provider_api_methods(self) -> None:
        """
        Test provider API methods for VM operations and info retrieval.
        """
        with fixtures.patched_provider() as provider:
            api = typing.cast(mock.MagicMock, provider.api)
            # Patch get_vm_info to return correct values for test
            api.get_vm_info.side_effect = lambda vm_id: fixtures.VMS[0] if vm_id == 'vm-1' else (fixtures.VM_INSTANCES[0] if vm_id == 'vm-instance-1' else None)  # type: ignore
            self.assertEqual(provider.test_connection(), True)
            api.test.assert_called_once_with()
            self.assertEqual(provider.api.list_vms(), fixtures.VMS)
            # Check get_vm_info for both a VM and a VM instance
            self.assertEqual(provider.api.get_vm_info('vm-1'), fixtures.VMS[0])
            self.assertEqual(provider.api.get_vm_info('vm-instance-1'), fixtures.VM_INSTANCES[0])
            self.assertTrue(provider.api.start_vm('vm-1'))
            self.assertTrue(provider.api.stop_vm('vm-1'))
            self.assertTrue(provider.api.delete_vm('vm-1'))

    # --- Config Change Detection ---
    def test_connection_key_matches_client_cache_key(self) -> None:
        """
        provider.connection_key() and OpenshiftClient.cache_key() must build the same string,
        or the cached client would be recreated on every access.
        """
        provider = fixtures.create_provider()
        self.assertEqual(provider.connection_key(), provider.api.cache_key())

    def test_initialize_resets_cached_api(self) -> None:
        """
        initialize() should set _cached_api to None to force refresh on config change.
        """
        provider = fixtures.create_provider()
        provider._cached_api = mock.MagicMock()
        provider.initialize({})
        self.assertIsNone(provider._cached_api)

    def test_api_recreates_client_when_config_changed(self) -> None:
        """
        api property creates a new OpenshiftClient when the connection params have changed.
        """
        provider = fixtures.create_provider()
        old_client = fixtures.create_client_mock()
        old_client.cache_key.return_value = 'https://old-cluster.example.com|https://old-api.example.com:6443|kubeadmin|default|False'
        provider._cached_api = old_client

        with mock.patch('uds.services.OpenShift.provider.client.OpenshiftClient') as MockClient:
            new_mock = mock.MagicMock()
            MockClient.return_value = new_mock
            result = provider.api
            MockClient.assert_called_once()
            self.assertIs(result, new_mock)

    def test_api_reuses_client_when_config_unchanged(self) -> None:
        """
        api property reuses the cached client when connection params haven't changed.
        """
        provider = fixtures.create_provider()
        old_client = fixtures.create_client_mock()  # cache_key() matches PROVIDER_VALUES_DICT
        provider._cached_api = old_client
        self.assertIs(provider.api, old_client)

    # --- Name Sanitization ---
    def test_sanitized_name(self) -> None:
        """
        Test name sanitization utility for various input cases.
        """
        provider = fixtures.create_provider()
        test_cases = [
            ('Test-VM-1', 'test-vm-1'),
            ('Test_VM@2', 'test-vm-2'),
            ('My Test VM!!!', 'my-test-vm'),
            ('Test !!! this is', 'test-this-is'),
            ('UDS-Pub-Hello World!!--2025065122-v1', 'uds-pub-hello-world-2025065122-v1'),
            ('a' * 100, 'a' * 63),  # Test truncation
        ]
        for input_name, expected in test_cases:
            self.assertEqual(provider.sanitized_name(input_name), expected)