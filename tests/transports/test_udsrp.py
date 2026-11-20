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

import os
import sys
import typing
from unittest import mock

from tests.utils.test import UDSTestCase
from uds.core import types
from uds.transports.UdsRP.common import (
    DEFAULT_SCREEN_SIZE,
    SCREEN_SIZES,
    BaseUdsRPTransport,
    TicketChannelPolicy,
    TicketPayloadData,
)
from uds.transports.UdsRP.direct import UdsRPTransport
from uds.transports.UdsRP.tunnel import TUdsRPTransport

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
        # Redirections: audio/clipboard/storage on, the rest off by default.
        redirections = data["redirections"]
        self.assertTrue(redirections["audio"])
        self.assertFalse(redirections["mic"])
        self.assertTrue(redirections["clipboard"])
        self.assertTrue(redirections["storage"])
        self.assertFalse(redirections["printing"])
        self.assertFalse(redirections["webcam"])
        self.assertFalse(redirections["smartcard"])
        # No launcher options: the codec travels in the ticket, and privacy screen
        # was dropped (the agent ignores the flag, it is incompatible with
        # console-mirror capture).
        self.assertNotIn("options", data)
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

    def test_screen_size_choice_sets_geometry(self) -> None:
        transport = self._transport()
        transport.screen_size.value = "1024x768"

        data = self._params(transport)
        self.assertEqual(data["screen_width"], 1024)
        self.assertEqual(data["screen_height"], 768)

    def test_unknown_screen_size_falls_back_to_default(self) -> None:
        transport = self._transport()
        transport.screen_size.value = "1234x567"

        self.assertEqual(transport.selected_screen_size(), (1920, 1080))

    def test_default_screen_size_is_one_of_the_offered_sizes(self) -> None:
        self.assertIn(DEFAULT_SCREEN_SIZE, SCREEN_SIZES)

    def test_screen_size_field_defaults_to_default_screen_size(self) -> None:
        transport = self._transport()

        self.assertEqual(transport.screen_size.value, "1920x1080")
        self.assertEqual(transport.selected_screen_size(), DEFAULT_SCREEN_SIZE)

    def test_screen_size_offers_the_five_fixed_sizes(self) -> None:
        values = [choice.id for choice in self._transport().screen_size._field_info.choices]

        self.assertEqual(values, ["800x600", "1024x768", "1600x1200", "1920x1080", "3840x2160"])

    def test_codec_is_not_sent_to_the_launcher(self) -> None:
        transport = self._transport()
        transport.preferred_codec.value = "vp9-balanced"

        self.assertNotIn("options", self._params(transport))

    def test_codec_choices_match_the_agent_presets(self) -> None:
        values = [choice.id for choice in self._transport().preferred_codec._field_info.choices]

        self.assertEqual(
            values,
            [
                "auto",
                "h264",
                "vp9-low-latency",
                "vp9-balanced",
                "vp9-bandwidth-saver",
                "rects-jpeg",
                "rects-jpeg-fast",
            ],
        )

    def test_h264_is_sent_as_a_literal_preset(self) -> None:
        transport = self._transport()
        transport.preferred_codec.value = "h264"

        self.assertEqual(self._payload(transport)["video"], {"preset": "h264"})

    def test_fixed_codec_is_sent_as_the_chosen_preset(self) -> None:
        transport = self._transport()
        transport.preferred_codec.value = "rects-jpeg"

        self.assertEqual(self._payload(transport)["video"], {"preset": "rects-jpeg"})

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
        self.assertFalse(payload["interactive_credentials"])
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

    def test_window_mode_defaults_to_fullscreen(self) -> None:
        self.assertEqual(self._params(self._transport())["window_mode"], "fullscreen")

    def test_window_mode_follows_transport_setting(self) -> None:
        transport = self._transport()
        transport.window_mode.value = "minimized"
        self.assertEqual(self._params(transport)["window_mode"], "minimized")

    def test_payload_video_preset_follows_preferred_codec(self) -> None:
        transport = self._transport()
        transport.preferred_codec.value = "vp9-balanced"
        self.assertEqual(self._payload(transport)["video"], {"preset": "vp9-balanced"})

    def test_payload_has_no_video_when_codec_is_auto(self) -> None:
        transport = self._transport()
        transport.preferred_codec.value = "auto"
        self.assertNotIn("video", self._payload(transport))

    def test_payload_without_credentials_when_empty(self) -> None:
        transport = self._transport()
        transport.force_empty_creds.value = True

        # Empty creds → no auto-logon credentials in the ticket, and the
        # agent is told to accept the client's interactive credentials.
        payload = transport.build_ticket_payload(_connection_data("", "", "")).as_dict()
        self.assertNotIn("credentials", payload)
        self.assertTrue(payload["interactive_credentials"])

    def test_payload_display_follows_screen_size(self) -> None:
        transport = self._transport()
        transport.screen_size.value = "800x600"

        self.assertEqual(self._payload(transport)["display"]["width"], 800)
        self.assertEqual(self._payload(transport)["display"]["height"], 600)

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


def _tunnel_userservice() -> tuple[mock.MagicMock, mock.MagicMock]:
    userservice = mock.MagicMock()
    userservice.process_user_password = mock.MagicMock(side_effect=lambda u, p: (u, p))
    user = mock.MagicMock()
    user.get_username_for_auth.return_value = "testuser"
    return userservice, user


class TUdsRPTransportTest(UDSTestCase):
    def _transport(self) -> TUdsRPTransport:
        return TUdsRPTransport(self.create_environment(), None)

    def _script_params(self, transport: TUdsRPTransport) -> tuple[dict[str, typing.Any], mock.MagicMock]:
        userservice, user = _tunnel_userservice()
        with (
            mock.patch("uds.models.TicketStore.create_for_tunnel") as create_for_tunnel,
            mock.patch("uds.core.util.fields.get_tunnel_from_field") as get_tunnel,
            mock.patch.object(TUdsRPTransport, "get_script") as get_script,
        ):
            create_for_tunnel.return_value = TICKET
            get_tunnel.return_value = mock.MagicMock(host="tunnel-host", port=443)
            get_script.return_value = "script"

            transport.get_transport_script(
                userservice=userservice,
                transport=mock.MagicMock(),
                ip="1.2.3.4",
                os=types.os.DetectedOsInfo(
                    os=types.os.KnownOS.LINUX, browser=types.os.KnownBrowser.FIREFOX, version=""
                ),
                user=user,
                password="testpassword",
                request=mock.MagicMock(),
            )

            params = get_script.call_args.args[2]
            self.assertEqual(get_script.call_args.args[:2], ("linux", "tunnel"))
            self.assertEqual(get_script.call_args.kwargs, {"associated_ticket": TICKET})
            self.assertEqual(create_for_tunnel.call_args.kwargs["port"], 14433)
            self.assertEqual(create_for_tunnel.call_args.kwargs["extra"]["version"], 1)
            return params, create_for_tunnel

    def test_group_is_tunneled(self) -> None:
        self.assertEqual(TUdsRPTransport.group, types.transports.Grouping.TUNNELED)
        self.assertEqual(UdsRPTransport.group, types.transports.Grouping.DIRECT)

    def test_transport_exposes_every_base_field(self) -> None:
        expected = set(BaseUdsRPTransport._gui_fields_template)

        fields = set(self._transport()._gui_fields_template)
        self.assertEqual(expected - fields, set(), "TUdsRPTransport is missing fields")

    def test_transport_is_registered(self) -> None:
        from uds.core import transports

        self.assertIs(transports.factory().lookup("tudsrptransport"), TUdsRPTransport)

    def test_tunnel_fields_present(self) -> None:
        fields = self._transport()._gui_fields_template
        self.assertIn("tunnel", fields)
        self.assertIn("startup_time", fields)
        self.assertIn("verify_certificate", fields)

    def test_tunnel_script_generated_with_tunnel_block(self) -> None:
        params, _create = self._script_params(self._transport())

        self.assertEqual(params["server"], "1.2.3.4")
        self.assertEqual(params["port"], 14433)
        self.assertEqual(params["ticket"], TICKET)
        self.assertEqual(
            params["tunnel"],
            {
                "host": "tunnel-host",
                "port": 443,
                "ticket": TICKET,
                "startup_time": 30000,
                "verify_ssl": True,
            },
        )
        self.assertNotIn("password", params)

    def test_tunnel_ticket_carries_same_payload_as_direct(self) -> None:
        transport = self._transport()
        _params, create_for_tunnel = self._script_params(transport)

        expected = transport.build_ticket_payload(_connection_data("testuser", "testpassword", "")).as_dict()
        self.assertEqual(create_for_tunnel.call_args.kwargs["extra"], expected)

    def test_tunnel_ticket_validity_covers_startup_wait(self) -> None:
        transport = self._transport()
        transport.startup_time.value = 180
        transport.ticket_validity.value = 60

        _params, create_for_tunnel = self._script_params(transport)
        self.assertEqual(create_for_tunnel.call_args.kwargs["validity"], 240)

    def test_tunnel_script_files_exist_for_each_os(self) -> None:
        module_dir = os.path.dirname(sys.modules[TUdsRPTransport.__module__].__file__ or "")
        for osname in ("linux", "macosx", "windows"):
            path = os.path.join(module_dir, "scripts", osname, "tunnel.js")
            self.assertTrue(os.path.isfile(path), path)
