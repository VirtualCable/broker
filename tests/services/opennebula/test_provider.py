import ssl
from unittest import mock

from uds.core import environment
from uds.services.OpenNebula.provider import OpenNebulaProvider

from ...utils.test import UDSTransactionTestCase


class OpenNebulaVerifySslTest(UDSTransactionTestCase):
    def _provider(self) -> OpenNebulaProvider:
        return OpenNebulaProvider(environment=environment.Environment.testing_environment())

    def test_verify_ssl_defaults_to_enabled(self) -> None:
        # This provider has always verified, so migrating it off would relax what is deployed
        self.assertTrue(self._provider().verify_ssl.as_bool())

    def test_context_reaches_the_server_proxy(self) -> None:
        for enabled, expected in ((True, ssl.CERT_REQUIRED), (False, ssl.CERT_NONE)):
            with self.subTest(verify_ssl=enabled):
                provider = self._provider()
                provider.verify_ssl.value = enabled

                with mock.patch(
                    "uds.services.OpenNebula.on.client.xmlrpc.client.ServerProxy"
                ) as server_proxy:
                    provider.api.connect()

                context = server_proxy.call_args.kwargs["context"]
                self.assertEqual(context.verify_mode, expected)
                self.assertEqual(context.check_hostname, enabled)
