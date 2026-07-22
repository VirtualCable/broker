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

from uds.core import consts
from uds.transports.HTML5SSH.html5ssh import HTML5SSHTransport

from tests.utils.test import UDSTestCase


class HTML5SSHTest(UDSTestCase):
    def test_transport_get_link(self) -> None:
        transport = HTML5SSHTransport(self.create_environment(), None)
        # Set field values
        transport.username.value = "testuser"
        transport.ssh_command.value = "ls -l"
        transport.ssh_port.value = 2222
        transport.enable_file_sharing.value = "true"  # both upload and download
        transport.enable_clipboard.value = True
        transport.max_upload_size.value = 64
        transport.server_keep_alive.value = 15
        transport.ssh_host_key.value = "ssh-rsa AAAA..."
        transport.filesharing_root.value = "/home/user"
        transport.force_new_window.value = consts.FALSE_STR

        userservice = mock.MagicMock()
        userservice.uuid = "userservice-uuid"
        userservice.deployed_service.uuid = "ds-uuid"

        user = mock.MagicMock()
        user.uuid = "user-uuid"

        transport_model = mock.MagicMock()
        transport_model.uuid = "transport-uuid"

        # Mock TicketStore
        with mock.patch("uds.models.TicketStore.create_for_tunnel") as create_for_tunnel:
            create_for_tunnel.return_value = "test-ticket-00000000000000000000000000000000000000"

            # Mock tunnel field
            with mock.patch("uds.core.util.fields.get_tunnel_from_field") as get_tunnel:
                get_tunnel.return_value = mock.MagicMock(host="tunnel-host", port=443)

                link = transport.get_link(
                    userservice=userservice,
                    transport=transport_model,
                    ip="1.2.3.4",
                    os=mock.MagicMock(),
                    user=user,
                    password="password",
                    request=mock.MagicMock(),
                )

                # Check create_for_tunnel call
                create_for_tunnel.assert_called_once()
                _args, kwargs = create_for_tunnel.call_args
                self.assertEqual(kwargs["port"], 2222)

                extra = kwargs["extra"]
                self.assertEqual(extra["username"], "testuser")
                self.assertEqual(extra["command"], "ls -l")
                self.assertEqual(extra["host_key"], "ssh-rsa AAAA...")
                self.assertEqual(extra["keepalive_interval"], 15)
                self.assertEqual(extra["enable_sftp"], True)
                self.assertEqual(extra["sftp_root"], "/home/user")
                self.assertEqual(extra["allow_upload"], True)
                self.assertEqual(extra["allow_download"], True)
                self.assertEqual(extra["allow_clipboard"], True)
                self.assertEqual(extra["max_upload_size"], 64 * 1024 * 1024)
                self.assertEqual(extra["title"], "UDS SSH 1.2.3.4")

                # Check link format
                self.assertIn("https://tunnel-host:443/ssh/?ticket=", link)
                self.assertIn(f"{consts.transports.ON_NEW_WINDOW_VAR}=transport-uuid", link)

    def test_transport_file_sharing_modes(self) -> None:
        """Test that enable_file_sharing correctly maps to allow_upload/download/enable_sftp booleans"""
        transport = HTML5SSHTransport(self.create_environment(), None)
        transport.username.value = "user"
        transport.ssh_port.value = 22

        userservice = mock.MagicMock()
        user = mock.MagicMock()
        transport_model = mock.MagicMock()
        transport_model.uuid = "transport-uuid"

        test_cases = [
            ("false", False, False, False),  # disabled
            ("up", True, True, False),  # upload only
            ("down", True, False, True),  # download only
            ("true", True, True, True),  # both
        ]

        for sharing_value, expect_sftp, expect_up, expect_down in test_cases:
            transport.enable_file_sharing.value = sharing_value
            with (
                mock.patch("uds.models.TicketStore.create_for_tunnel") as create_for_tunnel,
                mock.patch("uds.core.util.fields.get_tunnel_from_field") as get_tunnel,
            ):
                create_for_tunnel.return_value = "x" * 48
                get_tunnel.return_value = mock.MagicMock(host="h", port=443)

                transport.get_link(
                    userservice, transport_model, "1.2.3.4", mock.MagicMock(), user, "", mock.MagicMock()
                )

                extra = create_for_tunnel.call_args[1]["extra"]
                self.assertEqual(extra["enable_sftp"], expect_sftp, f"enable_sftp wrong for '{sharing_value}'")
                self.assertEqual(extra["allow_upload"], expect_up, f"allow_upload wrong for '{sharing_value}'")
                self.assertEqual(extra["allow_download"], expect_down, f"allow_download wrong for '{sharing_value}'")

    def test_tickets_serialization(self) -> None:
        from uds.core.types.tickets import TunnelTicket, TunnelTicketRemote

        remote = TunnelTicketRemote(host="1.1.1.1", port=22, extra={"test": "data"})
        ticket = TunnelTicket(userservice=None, remotes=[remote])

        # Serialize
        serialized = ticket.as_dict()
        self.assertIn('"extra":', serialized["remotes"])

        # Deserialize
        with mock.patch("uds.models.UserService.objects.filter") as filter:
            filter.return_value.first.return_value = None
            deserialized = TunnelTicket.from_dict(serialized)

            self.assertEqual(len(deserialized.remotes), 1)
            self.assertEqual(deserialized.remotes[0].host, "1.1.1.1")
            self.assertEqual(deserialized.remotes[0].extra["test"], "data")

    def test_tickets_backward_compatibility(self) -> None:
        from uds.core.types.tickets import TunnelTicket

        # Old format remotes string: "host,port#host2,port2"
        old_data = {
            "userservice_uuid": "uuid",
            "remotes": "1.2.3.4,22#5.6.7.8,80",
            "started": "2024-01-01T00:00:00",
            "shared_secret": "",
        }

        with mock.patch("uds.models.UserService.objects.filter") as filter:
            filter.return_value.first.return_value = None
            deserialized = TunnelTicket.from_dict(old_data)

            self.assertEqual(len(deserialized.remotes), 2)
            self.assertEqual(deserialized.remotes[0].host, "1.2.3.4")
            self.assertEqual(deserialized.remotes[0].port, 22)
            self.assertEqual(deserialized.remotes[1].host, "5.6.7.8")
            self.assertEqual(deserialized.remotes[1].port, 80)
            self.assertEqual(deserialized.remotes[0].extra, {})

    def test_ticket_response_includes_extra(self) -> None:
        """Verify TunnelTicketResponse.as_dict() preserves extra in remotes"""
        from uds.core.types.tickets import TunnelTicketRemote, TunnelTicketResponse

        extra = {
            "username": "root",
            "enable_sftp": True,
            "allow_upload": True,
            "allow_download": False,
            "allow_clipboard": True,
        }
        remote = TunnelTicketRemote(host="10.0.0.1", port=22, extra=extra)
        response = TunnelTicketResponse(remotes=[remote], notify="notify-ticket", shared_secret="")

        d = response.as_dict()
        self.assertEqual(len(d["remotes"]), 1)
        self.assertEqual(d["remotes"][0]["extra"]["username"], "root")
        self.assertEqual(d["remotes"][0]["extra"]["enable_sftp"], True)
        self.assertEqual(d["remotes"][0]["extra"]["allow_upload"], True)
        self.assertEqual(d["remotes"][0]["extra"]["allow_download"], False)

        # Round-trip
        restored = TunnelTicketResponse.from_dict(d)
        self.assertEqual(restored.remotes[0].extra["username"], "root")
        self.assertEqual(restored.remotes[0].extra["allow_download"], False)
