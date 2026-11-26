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
Tests for the common transport reachability check (Transport.is_ip_allowed,
final, delegating on is_ip_reachable) and the allow_ip_check switch.

Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import typing
from unittest import mock

from tests.utils.test import UDSTestCase
from uds.core import consts, transports, types
from uds.core.environment import Environment
from uds.core.util import fields, serializer
from uds.transports.HTML5RDP.html5rdp import HTML5RDPTransport
from uds.transports.HTML5SSH.html5ssh import HTML5SSHTransport
from uds.transports.RDP.rdp import RDPTransport
from uds.transports.RDP.rdptunnel import TRDPTransport
from uds.transports.RDPEmbedded.direct import RDPEmbeddedTransport
from uds.transports.RDPEmbedded.tunnel import TRDPEmbeddedTransport
from uds.transports.SPICE.spice import SPICETransport
from uds.transports.SPICE.spicetunnel import TSPICETransport
from uds.transports.UdsRP.direct import UdsRPTransport
from uds.transports.UdsRP.tunnel import TUdsRPTransport
from uds.transports.URL.url_custom import URLCustomTransport
from uds.transports.X2GO.x2go import X2GOTransport
from uds.transports.X2GO.x2gotunnel import TX2GOTransport

if typing.TYPE_CHECKING:
    from uds import models

IP = "10.0.0.1"

# All transports with a real reachability check: (class, gui port field, expected port)
CHECKING_TRANSPORTS: typing.Final[list[tuple[type[transports.Transport], str, int]]] = [
    (RDPTransport, "rdp_port", 3389),
    (TRDPTransport, "rdp_port", 3389),
    (RDPEmbeddedTransport, "rdp_port", 3389),
    (TRDPEmbeddedTransport, "rdp_port", 3389),
    (HTML5RDPTransport, "rdp_port", 3389),
    (HTML5SSHTransport, "ssh_port", 22),
    (UdsRPTransport, "server_port", 14433),
    (TUdsRPTransport, "server_port", 14433),
    (X2GOTransport, "", 22),  # X2Go hardcodes port 22, no gui field
    (TX2GOTransport, "", 22),
]


class TestCommonReachability(UDSTestCase):
    """Behavior of the final, common is_ip_allowed on the base Transport"""

    def _transport(self) -> transports.Transport:
        return transports.Transport(Environment.private_environment("reachability-test"), None)

    def test_default_is_reachable_without_checking(self) -> None:
        # Base is_ip_reachable defaults to True: no connectivity test is done
        with mock.patch("uds.core.util.net.test_connectivity") as test_conn:
            self.assertTrue(self._transport().is_ip_allowed(mock.MagicMock(), IP))
            test_conn.assert_not_called()

    def test_url_transport_is_always_allowed(self) -> None:
        transport = URLCustomTransport(
            Environment.private_environment("reachability-url"), {"url_pattern": "https://_IP_/"}
        )
        with mock.patch("uds.core.util.net.test_connectivity") as test_conn:
            self.assertTrue(transport.is_ip_allowed(mock.MagicMock(), IP))
            test_conn.assert_not_called()

    def test_result_is_cached_and_returned_fresh_on_first_check(self) -> None:
        transport = RDPTransport(Environment.private_environment("reachability-cache"), None)
        userservice = mock.MagicMock()
        with mock.patch("uds.core.util.net.test_connectivity", side_effect=[False, True]) as test_conn:
            # First check: fresh False (regression: old code returned a stale value here)
            self.assertFalse(transport.is_ip_allowed(userservice, IP))
            # Consecutive checks are absorbed by the cache, success or failure
            self.assertFalse(transport.is_ip_allowed(userservice, IP))
            self.assertEqual(test_conn.call_count, 1)

            # A different ip is not cached
            self.assertTrue(transport.is_ip_allowed(userservice, "10.0.0.2"))
            self.assertEqual(test_conn.call_count, 2)

    def test_cache_eviction_rechecks(self) -> None:
        transport = RDPTransport(Environment.private_environment("reachability-expire"), None)
        userservice = mock.MagicMock()
        with mock.patch("uds.core.util.net.test_connectivity", side_effect=[False, True]) as test_conn:
            self.assertFalse(transport.is_ip_allowed(userservice, IP))
            # Once the cached entry is gone (TTL expired), the check runs again
            transport.cache.remove(f"reachability:{IP}")
            self.assertTrue(transport.is_ip_allowed(userservice, IP))
            self.assertEqual(test_conn.call_count, 2)

    def test_cache_ttl_is_the_short_common_one(self) -> None:
        transport = RDPTransport(Environment.private_environment("reachability-ttl"), None)
        with (
            mock.patch("uds.core.util.net.test_connectivity", return_value=True),
            mock.patch.object(transport.cache, "put", wraps=transport.cache.put) as cache_put,
        ):
            transport.is_ip_allowed(mock.MagicMock(), IP)
            cache_put.assert_called_once_with(f"reachability:{IP}", "Y", consts.net.REACHABILITY_CACHE_TIMEOUT)

    def test_check_timeout_is_short(self) -> None:
        transport = RDPTransport(Environment.private_environment("reachability-timeout"), None)
        with mock.patch("uds.core.util.net.test_connectivity", return_value=True) as test_conn:
            transport.is_ip_allowed(mock.MagicMock(), IP)
            self.assertEqual(test_conn.call_args.args[2], consts.net.REACHABILITY_CHECK_TIMEOUT)
            self.assertLessEqual(consts.net.REACHABILITY_CHECK_TIMEOUT, 1.0)

    def test_disabled_check_skips_connectivity_and_cache(self) -> None:
        transport = RDPTransport(Environment.private_environment("reachability-off"), None)
        typing.cast(typing.Any, transport.allow_ip_check).value = "False"
        with (
            mock.patch("uds.core.util.net.test_connectivity") as test_conn,
            mock.patch.object(transport.cache, "put") as cache_put,
        ):
            self.assertTrue(transport.is_ip_allowed(mock.MagicMock(), IP))
            test_conn.assert_not_called()
            cache_put.assert_not_called()

    def test_is_ip_allowed_is_final(self) -> None:
        # Guards the design: subclasses must override is_ip_reachable, never is_ip_allowed
        self.assertTrue(getattr(transports.Transport.is_ip_allowed, "__final__", False))


class TestCheckingTransports(UDSTestCase):
    """Every transport with a real check exposes the switch and checks its port"""

    def test_allow_ip_check_field_defaults_to_true(self) -> None:
        for transport_type, _, _ in CHECKING_TRANSPORTS:
            with self.subTest(transport=transport_type.__name__):
                field = transport_type._gui_fields_template["allow_ip_check"]
                self.assertTrue(field.as_bool())
                self.assertEqual(field._field_info.tab, types.ui.Tab.ADVANCED)

    def test_reachable_uses_expected_port(self) -> None:
        for transport_type, port_field, expected_port in CHECKING_TRANSPORTS:
            with self.subTest(transport=transport_type.__name__):
                transport = transport_type(
                    Environment.private_environment(f"reach-{transport_type.__name__}"), None
                )
                if port_field:
                    getattr(transport, port_field).value = str(expected_port + 1)
                    expected_port += 1
                with mock.patch("uds.core.util.net.test_connectivity", return_value=True) as test_conn:
                    self.assertTrue(transport.is_ip_allowed(mock.MagicMock(), IP))
                    self.assertEqual(test_conn.call_args.args[:2], (IP, expected_port))

    def test_unreachable_blocks(self) -> None:
        for transport_type, _, _ in CHECKING_TRANSPORTS:
            with self.subTest(transport=transport_type.__name__):
                transport = transport_type(
                    Environment.private_environment(f"unreach-{transport_type.__name__}"), None
                )
                with mock.patch("uds.core.util.net.test_connectivity", return_value=False):
                    self.assertFalse(transport.is_ip_allowed(mock.MagicMock(), IP))

    def test_disabled_switch_allows_without_check(self) -> None:
        for transport_type, _, _ in CHECKING_TRANSPORTS:
            with self.subTest(transport=transport_type.__name__):
                transport = transport_type(
                    Environment.private_environment(f"off-{transport_type.__name__}"), None
                )
                typing.cast(typing.Any, transport.allow_ip_check).value = "False"
                with mock.patch("uds.core.util.net.test_connectivity") as test_conn:
                    self.assertTrue(transport.is_ip_allowed(mock.MagicMock(), IP))
                    test_conn.assert_not_called()

    def test_unmarshal_of_old_data_keeps_check_enabled(self) -> None:
        # Instances stored before allow_ip_check existed must keep checking (default True)
        for transport_type, _, _ in CHECKING_TRANSPORTS:
            with self.subTest(transport=transport_type.__name__):
                old = transport_type(Environment.private_environment(f"old-{transport_type.__name__}"), None)
                # Rebuild the marshalled data without the new field, as stored by old versions
                data = old.marshal()
                prefix_len = len(consts.ui.SERIALIZATION_HEADER) + len(consts.ui.SERIALIZATION_VERSION)
                stored_fields: list[tuple[str, str, typing.Any]] = serializer.deserialize(data[prefix_len:])
                self.assertTrue(any(name == "allow_ip_check" for name, _, _ in stored_fields))
                old_fields = [f for f in stored_fields if f[0] != "allow_ip_check"]
                old_data = (
                    consts.ui.SERIALIZATION_HEADER
                    + consts.ui.SERIALIZATION_VERSION
                    + serializer.serialize(old_fields)
                )

                restored = transport_type(
                    Environment.private_environment(f"new-{transport_type.__name__}"), None
                )
                restored.unmarshal(old_data)
                self.assertTrue(typing.cast(typing.Any, restored.allow_ip_check).as_bool())
                with mock.patch("uds.core.util.net.test_connectivity", return_value=False):
                    self.assertFalse(restored.is_ip_allowed(mock.MagicMock(), IP))

    def test_field_template_is_the_common_one(self) -> None:
        template = fields.allow_ip_check_field()
        self.assertTrue(template.as_bool())
        self.assertEqual(template._field_info.tab, types.ui.Tab.ADVANCED)
        for transport_type, _, _ in CHECKING_TRANSPORTS:
            with self.subTest(transport=transport_type.__name__):
                field = transport_type._gui_fields_template["allow_ip_check"]
                self.assertEqual(field._field_info.label, template._field_info.label)
                self.assertEqual(field._field_info.tooltip, template._field_info.tooltip)


class TestSpiceReachability(UDSTestCase):
    """SPICE checks the console connection, not the service ip"""

    def _transport(self) -> SPICETransport:
        return SPICETransport(Environment.private_environment("reachability-spice"), None)

    def _userservice(self, con: types.services.ConsoleConnectionInfo | None) -> "models.UserService":
        userservice = mock.MagicMock()
        userservice.get_instance.return_value.get_console_connection.return_value = con
        return typing.cast("models.UserService", userservice)

    def test_no_console_is_unreachable(self) -> None:
        with mock.patch("uds.core.util.net.test_connectivity") as test_conn:
            self.assertFalse(self._transport().is_ip_allowed(self._userservice(None), IP))
            test_conn.assert_not_called()

    def test_proxy_skips_the_check(self) -> None:
        con = types.services.ConsoleConnectionInfo(type="spice", address="10.0.0.9", proxy="http://proxy:3128")
        with mock.patch("uds.core.util.net.test_connectivity") as test_conn:
            self.assertTrue(self._transport().is_ip_allowed(self._userservice(con), IP))
            test_conn.assert_not_called()

    def test_checks_console_address_and_port(self) -> None:
        con = types.services.ConsoleConnectionInfo(
            type="spice", address="10.0.0.9", port=5900, secure_port=5901
        )
        with mock.patch("uds.core.util.net.test_connectivity", return_value=True) as test_conn:
            self.assertTrue(self._transport().is_ip_allowed(self._userservice(con), IP))
            self.assertEqual(test_conn.call_args.args[:2], ("10.0.0.9", 5900))

    def test_falls_back_to_secure_port(self) -> None:
        con = types.services.ConsoleConnectionInfo(type="spice", address="10.0.0.9", secure_port=5901)
        with mock.patch("uds.core.util.net.test_connectivity", return_value=True) as test_conn:
            self.assertTrue(self._transport().is_ip_allowed(self._userservice(con), IP))
            self.assertEqual(test_conn.call_args.args[:2], ("10.0.0.9", 5901))

    def test_no_port_is_unreachable_with_message(self) -> None:
        transport = self._transport()
        con = types.services.ConsoleConnectionInfo(type="spice", address="10.0.0.9")
        with mock.patch("uds.core.util.net.test_connectivity") as test_conn:
            self.assertFalse(transport.is_ip_allowed(self._userservice(con), IP))
            test_conn.assert_not_called()
        self.assertIn("PORT", transport.get_available_error_msg(self._userservice(con), IP))

    def test_tunnel_variant_has_the_switch(self) -> None:
        self.assertIn("allow_ip_check", TSPICETransport._gui_fields_template)
