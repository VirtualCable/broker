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
Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import typing

from tests.utils.test import UDSTestCase
from uds.core import types
from uds.transports.UdsRP.common import BaseUdsRPTransport, TicketChannelPolicy, TicketPayloadData
from uds.transports.UdsRP.direct import UdsRPTransport

TICKET = "a" * 48


def _connection_data(
    username: str = "testuser",
    password: str = "testpassword",
    domain: str = "TESTDOM",
) -> types.connections.ConnectionData:
    return types.connections.ConnectionData(
        protocol=types.transports.Protocol.UDSRP,
        service_type=types.services.ServiceType.VDI,
        username=username,
        password=password,
        domain=domain,
    )


class UdsRPTransportTest(UDSTestCase):
    def _transport(self) -> UdsRPTransport:
        return UdsRPTransport(self.create_environment(), None)

    def _params(
        self, transport: UdsRPTransport, ci: types.connections.ConnectionData | None = None
    ) -> dict[str, typing.Any]:
        return transport.build_connection_params("1.2.3.4", TICKET, ci or _connection_data()).as_dict()

    def _payload(self, transport: UdsRPTransport) -> dict[str, typing.Any]:
        return transport.build_ticket_payload(_connection_data()).as_dict()

    # --- Client params -------------------------------------------------

    def test_default_params_shape(self) -> None:
        data = self._params(self._transport())

        self.assertEqual(data["server"], "1.2.3.4")
        self.assertEqual(data["port"], 14433)
        self.assertEqual(data["ticket"], TICKET)
        # Fixed default geometry travels explicitly.
        self.assertEqual(data["screen_width"], 1920)
        self.assertEqual(data["screen_height"], 1080)
        self.assertEqual(data["max_fullscreen_width"], 1920)
        self.assertEqual(data["max_fullscreen_height"], 1080)
        self.assertFalse(data["allow_fullscreen_scaling"])
        # Redirections: audio/clipboard/storage on, the rest off by default.
        redirections = data["redirections"]
        self.assertTrue(redirections["audio"])
        self.assertFalse(redirections["mic"])
        self.assertTrue(redirections["clipboard"])
        self.assertTrue(redirections["storage"])
        self.assertFalse(redirections["printing"])
        self.assertFalse(redirections["webcam"])
        self.assertFalse(redirections["smartcard"])
        # Options: privacy off, codec auto → omitted.
        self.assertFalse(data["options"]["privacy_screen"])
        self.assertNotIn("preferred_codec", data["options"])
        # Credentials: username/domain travel for prefill, never the password.
        self.assertEqual(data["user"], "testuser")
        self.assertEqual(data["domain"], "TESTDOM")
        self.assertTrue(data["has_credentials"])
        # The PSK never travels in params (it is data.shared_secret).
        self.assertNotIn("key", data)
        self.assertNotIn("shared_secret", data)
        self.assertNotIn("password", data)

    def test_missing_credentials_flag(self) -> None:
        """Without usable credentials the script must ask (has_credentials=False)."""
        data = self._params(self._transport(), _connection_data("testuser", "", "TESTDOM"))
        self.assertFalse(data["has_credentials"])
        # Username still travels so the dialog can prefill it.
        self.assertEqual(data["user"], "testuser")

        data = self._params(self._transport(), _connection_data("", "", ""))
        self.assertFalse(data["has_credentials"])
        self.assertNotIn("user", data)
        self.assertNotIn("domain", data)

    def test_params_prune_none_recursively(self) -> None:
        """No None value should survive at any nesting level."""

        def _assert_no_none(value: typing.Any) -> None:
            if isinstance(value, dict):
                items: dict[str, typing.Any] = typing.cast("dict[str, typing.Any]", value)
                for k, v in items.items():
                    self.assertIsNotNone(v, f"key '{k}' is None")
                    _assert_no_none(v)

        _assert_no_none(self._params(self._transport()))

    def test_unset_geometry_is_omitted(self) -> None:
        """0 on either axis means "not fixed by the broker": keys are omitted."""
        transport = self._transport()
        transport.screen_width.value = 0
        transport.screen_height.value = 0

        data = self._params(transport)
        self.assertNotIn("screen_width", data)
        self.assertNotIn("screen_height", data)

    def test_preferred_codec_sent_when_not_auto(self) -> None:
        transport = self._transport()
        transport.preferred_codec.value = "vp9"

        self.assertEqual(self._params(transport)["options"]["preferred_codec"], "vp9")

    def test_redirection_flags(self) -> None:
        transport = self._transport()
        transport.enable_audio.value = False
        transport.enable_microphone.value = True
        transport.enable_webcam.value = True

        redirections = self._params(transport)["redirections"]
        self.assertFalse(redirections["audio"])
        self.assertTrue(redirections["mic"])
        self.assertTrue(redirections["webcam"])

    # --- Ticket payload (agent session definition) ---------------------

    def test_default_payload_shape(self) -> None:
        payload = self._payload(self._transport())

        self.assertEqual(payload["version"], 1)
        self.assertEqual(
            payload["credentials"],
            {"user": "testuser", "password": "testpassword", "domain": "TESTDOM"},
        )
        self.assertEqual(payload["display"], {"width": 1920, "height": 1080, "fps": 30, "fixed": True})
        self.assertEqual(
            payload["channels"],
            {
                "audio": True,
                "clipboard": True,
                "storage": True,
                "smartcard": False,
                "webcam": False,
                "printer": False,
                "lock": False,
            },
        )
        self.assertTrue(payload["wallpaper_off"])
        self.assertEqual(payload["console_conflict"], "take_over")
        self.assertEqual(payload["console_takeover"], "logoff")
        self.assertEqual(payload["console_conflict_timeout_secs"], 30)

    def test_payload_prune_none_recursively(self) -> None:
        """The agent rejects explicit nulls: no None may survive."""

        def _assert_no_none(value: typing.Any) -> None:
            if isinstance(value, dict):
                items: dict[str, typing.Any] = typing.cast("dict[str, typing.Any]", value)
                for k, v in items.items():
                    self.assertIsNotNone(v, f"key '{k}' is None")
                    _assert_no_none(v)

        _assert_no_none(self._payload(self._transport()))

    def test_payload_without_credentials_when_empty(self) -> None:
        transport = self._transport()
        transport.force_empty_creds.value = True

        # Empty creds → no auto-logon credentials in the ticket.
        self.assertNotIn("credentials", transport.build_ticket_payload(_connection_data("", "", "")).as_dict())

    def test_payload_display_omitted_when_unset(self) -> None:
        transport = self._transport()
        transport.screen_width.value = 0
        transport.screen_height.value = 0

        self.assertNotIn("display", self._payload(transport))

    def test_payload_console_policy(self) -> None:
        transport = self._transport()
        transport.console_policy.value = "ask"
        transport.console_takeover.value = "disconnect"

        payload = self._payload(transport)
        self.assertEqual(payload["console_conflict"], "ask")
        self.assertEqual(payload["console_takeover"], "disconnect")

    def test_payload_channels_follow_fields(self) -> None:
        transport = self._transport()
        transport.enable_smartcard.value = True
        transport.enable_storage.value = False
        transport.allow_lock.value = True

        channels = self._payload(transport)["channels"]
        self.assertTrue(channels["smartcard"])
        self.assertFalse(channels["storage"])
        self.assertTrue(channels["lock"])

    # --- Structure ------------------------------------------------------

    def test_transport_exposes_every_base_field(self) -> None:
        """Fields are not inherited into the form: the concrete transport must re-export them."""
        expected = set(BaseUdsRPTransport._gui_fields_template)

        fields = set(self._transport()._gui_fields_template)
        self.assertEqual(expected - fields, set(), "UdsRPTransport is missing fields")

    def test_udsrp_protocol_in_generic_vdi(self) -> None:
        self.assertIn(types.transports.Protocol.UDSRP, types.transports.Protocol.generic_vdi())

    def test_transport_is_registered(self) -> None:
        """The modfinder discovers and registers the concrete transport."""
        from uds.core import transports

        self.assertIs(transports.factory().lookup("udsrptransport"), UdsRPTransport)

    def test_payload_dataclass_serializes_clean(self) -> None:
        """TicketPayloadData alone (no transport) serializes with version and no nulls."""
        data = TicketPayloadData(channels=TicketChannelPolicy()).as_dict()
        self.assertEqual(data["version"], 1)
        self.assertNotIn("credentials", data)
        self.assertNotIn("display", data)
