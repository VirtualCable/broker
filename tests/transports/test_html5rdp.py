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
from unittest import mock
import typing

from uds.core import types, consts
from uds.transports.HTML5RDP.html5rdp import HTML5RDPTransport

from tests.utils.test import UDSTestCase


def _make_mocks() -> tuple[mock.MagicMock, mock.MagicMock, mock.MagicMock]:
    """Helper to create userservice, user, and transport_model mocks."""
    userservice = mock.MagicMock()
    userservice.uuid = 'userservice-uuid'
    userservice.deployed_service.uuid = 'ds-uuid'
    userservice.process_user_password = mock.MagicMock(side_effect=lambda u, p: (u, p))

    user = mock.MagicMock()
    user.uuid = 'user-uuid'
    user.get_username_for_auth.return_value = 'testuser'

    transport_model = mock.MagicMock()
    transport_model.uuid = 'transport-uuid'

    return userservice, user, transport_model


class HTML5RDPTest(UDSTestCase):
    def _get_link_extra(
        self,
        transport: HTML5RDPTransport,
        userservice: mock.MagicMock,
        user: mock.MagicMock,
        transport_model: mock.MagicMock,
        ip: str = '1.2.3.4',
    ) -> tuple[str, dict[str, typing.Any]]:
        """Call get_link and return (link, extra_dict)."""
        with mock.patch('uds.models.TicketStore.create_for_tunnel') as create_for_tunnel, \
             mock.patch('uds.core.util.fields.get_tunnel_from_field') as get_tunnel:
            create_for_tunnel.return_value = 'x' * 48
            get_tunnel.return_value = mock.MagicMock(host='tunnel-host', port=443)

            link = transport.get_link(
                userservice=userservice,
                transport=transport_model,
                ip=ip,
                os=mock.MagicMock(),
                user=user,
                password='testpassword',
                request=mock.MagicMock(),
            )

            extra = create_for_tunnel.call_args[1]['extra']
            return link, extra

    def test_transport_get_link(self) -> None:
        transport = HTML5RDPTransport(self.create_environment(), None)
        transport.rdp_port.value = 3389
        transport.nla.value = True
        transport.best_experience.value = True
        transport.enable_audio.value = True
        transport.enable_microphone.value = False
        transport.enable_file_sharing.value = 'true'
        transport.allow_clipboard.value = True
        transport.session_quality.value = '3'  # High Quality
        transport.allow_quality_switch.value = True
        transport.force_new_window.value = consts.FALSE_STR

        userservice, user, transport_model = _make_mocks()
        link, extra = self._get_link_extra(transport, userservice, user, transport_model, ip='10.0.0.1')

        self.assertEqual(extra['user'], 'testuser')
        self.assertEqual(extra['options']['nla'], True)
        self.assertEqual(extra['options']['verify_ssl'], False)
        self.assertEqual(extra['best_experience'], True)
        self.assertEqual(extra['redirections']['audio'], True)
        self.assertEqual(extra['redirections']['mic'], False)
        self.assertEqual(extra['redirections']['clipboard'], True)
        self.assertEqual(extra['allow_upload'], True)
        self.assertEqual(extra['allow_download'], True)
        self.assertEqual(extra['session_quality'], 3)
        self.assertEqual(extra['allow_quality_switch'], True)
        self.assertEqual(extra['title'], 'RDP 10.0.0.1')

        # Check URL format
        self.assertIn('https://tunnel-host:443/rdp/?ticket=', link)
        self.assertIn(f'{consts.transports.ON_NEW_WINDOW_VAR}=transport-uuid', link)
        self.assertNotIn('guacamole', link)

    def test_transport_file_sharing_modes(self) -> None:
        """Test all 4 file sharing modes map correctly to allow_upload/allow_download."""
        transport = HTML5RDPTransport(self.create_environment(), None)
        userservice, user, transport_model = _make_mocks()

        test_cases = [
            ('false', False, False),
            ('up', True, False),
            ('down', False, True),
            ('true', True, True),
        ]

        for sharing_value, expect_up, expect_down in test_cases:
            transport.enable_file_sharing.value = sharing_value
            _, extra = self._get_link_extra(transport, userservice, user, transport_model)
            self.assertEqual(extra['allow_upload'], expect_up, f"allow_upload wrong for '{sharing_value}'")
            self.assertEqual(extra['allow_download'], expect_down, f"allow_download wrong for '{sharing_value}'")

    def test_nla_direct(self) -> None:
        """Test nla field maps directly to extra."""
        transport = HTML5RDPTransport(self.create_environment(), None)
        userservice, user, transport_model = _make_mocks()

        transport.nla.value = True
        _, extra = self._get_link_extra(transport, userservice, user, transport_model)
        self.assertTrue(extra['options']['nla'])

        transport.nla.value = False
        _, extra = self._get_link_extra(transport, userservice, user, transport_model)
        self.assertFalse(extra['options']['nla'])

    def test_clipboard_direct(self) -> None:
        """Test allow_clipboard field maps directly to extra."""
        transport = HTML5RDPTransport(self.create_environment(), None)
        userservice, user, transport_model = _make_mocks()

        transport.allow_clipboard.value = True
        _, extra = self._get_link_extra(transport, userservice, user, transport_model)
        self.assertTrue(extra['redirections']['clipboard'])

        transport.allow_clipboard.value = False
        _, extra = self._get_link_extra(transport, userservice, user, transport_model)
        self.assertFalse(extra['redirections']['clipboard'])

    def test_best_experience_direct(self) -> None:
        """Test best_experience field maps directly to extra."""
        transport = HTML5RDPTransport(self.create_environment(), None)
        userservice, user, transport_model = _make_mocks()

        transport.best_experience.value = True
        _, extra = self._get_link_extra(transport, userservice, user, transport_model)
        self.assertTrue(extra['best_experience'])

        transport.best_experience.value = False
        _, extra = self._get_link_extra(transport, userservice, user, transport_model)
        self.assertFalse(extra['best_experience'])

    def test_credential_empty_creds(self) -> None:
        """Test that empty creds results in empty user/password/domain."""
        transport = HTML5RDPTransport(self.create_environment(), None)
        transport.force_empty_creds.value = True
        userservice, user, transport_model = _make_mocks()

        _, extra = self._get_link_extra(transport, userservice, user, transport_model)
        self.assertIsNone(extra['user'])  # empty string → None
        self.assertIsNone(extra['password'])
        self.assertIsNone(extra['domain'])

    def test_extra_keys_match_rust_connection_data(self) -> None:
        """Verify that all keys in extra are valid Rust ConnectionData fields."""
        transport = HTML5RDPTransport(self.create_environment(), None)
        userservice, user, transport_model = _make_mocks()
        _, extra = self._get_link_extra(transport, userservice, user, transport_model)

        # Top-level fields in rdphtml5 ConnectionData (excluding host/port/notify_ticket set by broker)
        valid_rust_fields = {
            'user_id', 'host', 'port', 'user', 'password', 'domain',
            'best_experience', 'options', 'redirections',
            'allow_upload', 'allow_download', 'session_quality',
            'allow_quality_switch', 'notify_ticket', 'target_fps', 'rail_app',
            'rail_args', 'rail_working_dir', 'title',
        }
        valid_options_fields = {'nla', 'verify_ssl'}
        valid_redirections_fields = {'clipboard', 'audio', 'mic', 'webcam'}

        for key in extra:
            self.assertIn(key, valid_rust_fields, f"Extra key '{key}' not found in Rust ConnectionData")
        for key in extra['options']:
            self.assertIn(key, valid_options_fields, f"Options key '{key}' not found in Rust ConnectionData")
        for key in extra['redirections']:
            self.assertIn(key, valid_redirections_fields, f"Redirections key '{key}' not found in Rust ConnectionData")
