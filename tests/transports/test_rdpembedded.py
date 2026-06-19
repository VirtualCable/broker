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
Author: Janier Rodríguez
"""
import typing

from uds.core import types
from uds.transports.RDPEmbedded.common import RDPTunnelParams
from uds.transports.RDPEmbedded.direct import RDPEmbeddedTransport

from tests.utils.test import UDSTestCase


def _connection_data(
    username: str = 'testuser',
    password: str = 'testpassword',  # noqa: S107  (synthetic test credential)
    domain: str = 'TESTDOM',
) -> types.connections.ConnectionData:
    return types.connections.ConnectionData(
        protocol=types.transports.Protocol.RDP,
        service_type=types.services.ServiceType.VDI,
        username=username,
        password=password,
        domain=domain,
    )


class RDPEmbeddedTest(UDSTestCase):
    def _transport(self) -> RDPEmbeddedTransport:
        return RDPEmbeddedTransport(self.create_environment(), None)

    def _build(self, transport: RDPEmbeddedTransport) -> dict[str, typing.Any]:
        return transport.build_connection_params('1.2.3.4', _connection_data()).as_dict()

    def test_default_shape(self) -> None:
        """Defaults nest flags under options/redirections and drop nothing unexpected."""
        data = self._build(self._transport())

        self.assertEqual(data['server'], '1.2.3.4')
        self.assertEqual(data['options'], {'use_nla': True, 'verify_cert': False})
        # Audio on, mic off by default; "Allow none" drives policy sends an empty list.
        self.assertEqual(data['redirections']['audio'], True)
        self.assertEqual(data['redirections']['mic'], False)
        self.assertEqual(data['redirections']['drives'], [])
        # Webcam disabled by default → key omitted entirely.
        self.assertNotIn('webcam', data['redirections'])
        # No tunnel for the direct transport.
        self.assertNotIn('tunnel', data)

    def test_as_dict_prunes_none_recursively(self) -> None:
        """No None value should survive at any nesting level."""

        def _assert_no_none(value: typing.Any) -> None:
            if isinstance(value, dict):
                items: dict[str, typing.Any] = typing.cast('dict[str, typing.Any]', value)
                for k, v in items.items():
                    self.assertIsNotNone(v, f"key '{k}' is None")
                    _assert_no_none(v)

        _assert_no_none(self._build(self._transport()))

    def test_audio_mic_flags(self) -> None:
        transport = self._transport()
        transport.enable_audio.value = False
        transport.enable_microphone.value = True

        redirections = self._build(transport)['redirections']
        self.assertFalse(redirections['audio'])
        self.assertTrue(redirections['mic'])

    def test_drives_allow_any(self) -> None:
        transport = self._transport()
        transport.allow_drives.value = 'true'

        self.assertEqual(self._build(transport)['redirections']['drives'], ['all'])

    def test_webcam_enabled_without_size_limit(self) -> None:
        transport = self._transport()
        transport.enable_webcam.value = True

        webcam = self._build(transport)['redirections']['webcam']
        self.assertEqual(webcam['enabled'], True)
        self.assertEqual(webcam['quality'], 80)
        self.assertEqual(webcam['fps'], 15)
        # Both caps at 0 → no size_limit sent.
        self.assertNotIn('size_limit', webcam)

    def test_webcam_size_limit_single_axis(self) -> None:
        """Capping a single axis must still emit size_limit (0 = no cap on that axis)."""
        transport = self._transport()
        transport.enable_webcam.value = True
        transport.webcam_max_width.value = 1280

        webcam = self._build(transport)['redirections']['webcam']
        self.assertEqual(webcam['size_limit'], (1280, 0))

    def test_webcam_size_limit_both_axes(self) -> None:
        transport = self._transport()
        transport.enable_webcam.value = True
        transport.webcam_max_width.value = 1280
        transport.webcam_max_height.value = 720

        webcam = self._build(transport)['redirections']['webcam']
        self.assertEqual(webcam['size_limit'], (1280, 720))

    def test_webcam_quality_fps_bounds(self) -> None:
        """Quality and FPS expose validation bounds to the admin UI."""
        transport = self._transport()

        quality = transport.webcam_quality.gui_description()
        self.assertEqual((quality.min_value, quality.max_value), (1, 100))

        fps = transport.webcam_fps.gui_description()
        self.assertEqual((fps.min_value, fps.max_value), (1, 60))

    def test_sso_overrides_credentials(self) -> None:
        transport = self._transport()
        transport.use_sso.value = True

        data = self._build(transport)
        self.assertEqual(data['password'], '__NO_PASSWORD__')
        self.assertEqual(data['domain'], 'UDS')
        self.assertIn('options', data)

    def test_tunnel_block_included(self) -> None:
        transport = self._transport()
        tunnel = RDPTunnelParams(host='tunnel-host', port=7777, ticket='x' * 48, startup_time=0)

        data = transport.build_connection_params('1.2.3.4', _connection_data(), tunnel=tunnel).as_dict()
        self.assertEqual(data['tunnel']['host'], 'tunnel-host')
        self.assertEqual(data['tunnel']['port'], 7777)
