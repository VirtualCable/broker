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
"""

import contextlib
import collections.abc
import json
import logging
import typing
from unittest import mock

from django.test import override_settings

from uds.core import consts, types
from uds.core.managers.crypto import CryptoManager
from uds.core.managers.servers_api.requester import ServerApiRequester
from uds.models import TicketStore
from uds.REST.methods.client import Client
from uds.REST.processors import JsonProcessor, ParametersException
from uds.transports.RDP.rdp import RDPTransport
from uds.transports.RDP.rdptunnel import TRDPTransport
from uds.transports.RDPEmbedded.direct import RDPEmbeddedTransport
from uds.transports.RDPEmbedded.tunnel import TRDPEmbeddedTransport
from uds.transports.SPICE.spice import SPICETransport

from tests.fixtures.modules.transport import TestTransport
from tests.utils import test

SECRET: typing.Final[str] = "Sentinel-Pw-9f2c"
SCRAMBLER: typing.Final[str] = "a-scrambler"


class _Collector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@contextlib.contextmanager
def _debug_logs() -> collections.abc.Iterator[_Collector]:
    # The "uds" loggers do not propagate to root, so every one of them gets the handler
    collector = _Collector()
    names = [""] + [name for name in logging.root.manager.loggerDict if name.startswith("uds")]
    previous = [(logging.getLogger(name), logging.getLogger(name).level) for name in names]
    for logger, _level in previous:
        logger.addHandler(collector)
        logger.setLevel(logging.DEBUG)
    try:
        yield collector
    finally:
        for logger, level in previous:
            logger.removeHandler(collector)
            logger.setLevel(level)


@override_settings(DEBUG=False)
class NoSecretsInDebugLogsTest(test.UDSTestCase):
    def assertSecretNotLogged(self, collector: _Collector) -> None:
        self.assertEqual([m for m in collector.messages if SECRET in m], [])

    def test_collector_captures_uds_debug_records(self) -> None:
        with _debug_logs() as collector:
            logging.getLogger("uds.REST.methods.client").debug("probe %s", SECRET)

        self.assertIn(f"probe {SECRET}", collector.messages)

    def test_module_field_values(self) -> None:
        transport = TestTransport(self.create_environment(), {"pin": SECRET})

        with _debug_logs() as collector:
            self.assertEqual(transport.get_fields_as_dict()["pin"], SECRET)

        self.assertSecretNotLogged(collector)

    def test_rdp_client_launch(self) -> None:
        for transport_type in (RDPTransport, TRDPTransport, RDPEmbeddedTransport, TRDPEmbeddedTransport):
            with self.subTest(transport_type.__name__), _debug_logs() as collector:
                self._launch(transport_type)
                self.assertSecretNotLogged(collector)

    def test_server_preconnect(self) -> None:
        server = mock.MagicMock(uuid="a-server-uuid", token_hash="a-hash", host="a-host")
        server.is_restrained.return_value = False
        info = types.connections.ConnectionData(
            protocol=types.transports.Protocol.RDP,
            username="a-user",
            password=SECRET,
            service_type=types.services.ServiceType.VDI,
        )
        requester = ServerApiRequester(server)

        with _debug_logs() as collector, mock.patch.object(requester, "post"):
            self.assertTrue(requester.notify_preconnect(mock.MagicMock(), info))

        self.assertSecretNotLogged(collector)

    def test_client_ticket_scrambler(self) -> None:
        with _debug_logs() as collector:
            self._launch(RDPTransport)

        self.assertTrue(any("Got Ticket" in m for m in collector.messages))
        self.assertEqual([m for m in collector.messages if SCRAMBLER in m], [])

    def test_unparseable_request_body(self) -> None:
        body = f'{{"password": "{SECRET}",'.encode()
        request = mock.MagicMock(method="POST", body=body, META={"CONTENT_LENGTH": str(len(body))})
        request.headers = {"Content-Length": str(len(body))}

        with _debug_logs() as collector, self.assertRaises(ParametersException):
            JsonProcessor(request).process_parameters()

        self.assertSecretNotLogged(collector)

    def test_rdp_sign_request_body(self) -> None:
        body = json.dumps({"rdp": f"full address:s:10.0.0.1\npassword 51:b:{SECRET}\n"}).encode()
        request = mock.MagicMock(method="POST", body=body, META={"CONTENT_LENGTH": str(len(body))})
        request.headers = {"Content-Length": str(len(body))}

        with _debug_logs() as collector:
            self.assertIn(SECRET, JsonProcessor(request).process_parameters()["rdp"])

        self.assertTrue(any("Unmarshalled content" in m for m in collector.messages))
        self.assertSecretNotLogged(collector)

    def test_spice_console_ticket(self) -> None:
        console = types.services.ConsoleConnectionInfo(
            type="spice",
            address="10.0.0.1",
            ticket=types.services.ConsoleConnectionTicket(value=SECRET),
        )
        userservice = mock.MagicMock()
        userservice.get_instance.return_value.get_console_connection.return_value = console
        transport = SPICETransport(self.create_environment(), None)

        with _debug_logs() as collector:
            self.assertFalse(transport.is_ip_allowed(userservice, "10.0.0.1"))
            transport.get_transport_script(
                userservice,
                mock.MagicMock(),
                "10.0.0.1",
                types.os.DetectedOsInfo(
                    os=types.os.KnownOS.WINDOWS, browser=types.os.KnownBrowser.CHROME, version="1"
                ),
                mock.MagicMock(),
                "",
                mock.MagicMock(),
            )

        self.assertSecretNotLogged(collector)

    def _launch(
        self, transport_type: type[RDPTransport | TRDPTransport | RDPEmbeddedTransport | TRDPEmbeddedTransport]
    ) -> None:
        ticket = TicketStore.create(
            {
                "user": "a-user-uuid",
                "service": "a-service-uuid",
                "transport": "a-transport-uuid",
                "password": CryptoManager.manager().symmetric_encrypt(SECRET, SCRAMBLER),
            }
        )
        client = object.__new__(Client)
        client._args = [ticket, SCRAMBLER]
        client._params = {"version": consts.system.VERSION}
        client._request = mock.MagicMock()
        client._request.os = types.os.DetectedOsInfo(
            os=types.os.KnownOS.WINDOWS, browser=types.os.KnownBrowser.CHROME, version="1"
        )
        connection = types.connections.ConnectionData(
            protocol=types.transports.Protocol.RDP,
            username="a-user",
            password=SECRET,
            service_type=types.services.ServiceType.VDI,
        )

        with (
            mock.patch("uds.REST.methods.client.User"),
            mock.patch("uds.REST.methods.client.UserServiceManager") as manager,
            mock.patch.object(transport_type, "get_connection_info", return_value=connection),
        ):
            info = manager.manager.return_value.get_user_service_info.return_value
            info.ip = "10.0.0.1"
            info.transport.get_instance.return_value = transport_type(self.create_environment(), None)
            client.get()
