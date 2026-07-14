# -*- coding: utf-8 -*-

#
# Copyright (c) 2022 Virtual Cable S.L.
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
import logging
import typing

from django.utils.translation import gettext_noop as _

from uds import models
from uds.core import consts, transports, types
from uds.core.ui import gui
from uds.core.util import fields

# Not imported at runtime, just for type checking
if typing.TYPE_CHECKING:
    from uds.core.types.requests import ExtendedHttpRequestWithUser

logger = logging.getLogger(__name__)

READY_CACHE_TIMEOUT = 30


class HTML5SSHTransport(transports.Transport):
    """
    Provides access via SSH to service.
    """

    type_name = _('HTML5 SSH')
    type_type = 'HTML5SSHTransport'
    type_description = _('SSH protocol using HTML5 client')
    icon_file = 'html5ssh.png'

    own_link = True
    supported_oss = consts.os.ALL_OS_LIST
    # pylint: disable=no-member  # ??? SSH is there, but pylint does not see it ???
    PROTOCOL = types.transports.Protocol.SSH
    group = types.transports.Grouping.TUNNELED

    tunnel = fields.tunnel_field()

    username = gui.TextField(
        label=_('Username'),
        order=20,
        tooltip=_('Username for SSH connection authentication.'),
        tab=types.ui.Tab.CREDENTIALS,
    )

    ssh_command = gui.TextField(
        label=_('SSH Command (not used right now)'),
        order=30,
        tooltip=_(
            'Command to execute on the remote server. If not provided, an interactive shell will be executed.'
        ),
        tab=types.ui.Tab.PARAMETERS,
    )

    max_upload_size = gui.NumericField(
        label=_('Max Upload Size (MB)'),
        order=33,
        default=32,
        tooltip=_('Maximum size of uploaded files in MB. 0 means no limit (or server default).'),
        tab=types.ui.Tab.PARAMETERS,
    )

    enable_file_sharing = gui.ChoiceField(
        label=_('File Sharing'),
        order=24,
        tooltip=_('File upload/download redirection policy'),
        default='false',
        choices=[
            gui.choice_item('false', _('Disable file sharing')),
            gui.choice_item('down', _('Allow download only')),
            gui.choice_item('up', _('Allow upload only')),
            gui.choice_item('true', _('Allow both upload and download')),
        ],
        tab=types.ui.Tab.PARAMETERS,
    )

    filesharing_root = gui.TextField(
        label=_('File Sharing Root'),
        order=34,
        tooltip=_('Root path for file sharing. If not provided, root directory will be used.'),
        tab=types.ui.Tab.PARAMETERS,
    )
    ssh_port = gui.NumericField(
        length=40,
        label=_('SSH Server port'),
        default=22,
        order=35,
        tooltip=_('Port of the SSH server.'),
        required=True,
        tab=types.ui.Tab.PARAMETERS,
    )
    ssh_host_key = gui.TextField(
        label=_('SSH Host Key'),
        length=512,
        order=36,
        tooltip=_(
            'Host key of the SSH server. If not provided, no verification of host identity is done. (as the line in known_hosts file)'
        ),
        tab=types.ui.Tab.PARAMETERS,
    )
    server_keep_alive = gui.NumericField(
        length=3,
        label=_('Server Keep Alive'),
        default=30,
        order=37,
        tooltip=_(
            'Time in seconds between keep alive messages sent to server. If not provided, no keep alive messages are sent.'
        ),
        required=True,
        min_value=0,
        tab=types.ui.Tab.PARAMETERS,
    )

    enable_clipboard = gui.CheckBoxField(
        label=_('Enable Clipboard'),
        order=25,
        tooltip=_(
            'If checked, the clipboard will be redirected to remote session (if client browser supports it)'
        ),
        tab=types.ui.Tab.PARAMETERS,
        default=True,
    )

    ticket_validity = fields.tunnel_ticket_validity_field()

    force_new_window = gui.ChoiceField(
        label=_('New window'),
        order=40,
        choices=[
            gui.choice_item(consts.TRUE_STR, _('Always')),
            gui.choice_item(consts.FALSE_STR, _('Never')),
            gui.choice_item('overwrite', _('Overwrite')),
        ],
        default=consts.TRUE_STR,
        tooltip=_('If true, the transport will always open in a new window'),
        tab=types.ui.Tab.PARAMETERS,
    )

    @typing.override
    def is_ip_allowed(self, userservice: 'models.UserService', ip: str) -> bool:
        """
        Checks if the transport is available for the requested destination ip
        Override this in yours transports
        """
        logger.debug('Checking availability for %s', ip)
        ready = self.cache.get(ip)
        if not ready:
            # Check again for readyness
            if self.test_connectivity(userservice, ip, self.ssh_port.value) is True:
                self.cache.put(ip, 'Y', READY_CACHE_TIMEOUT)
                return True
            self.cache.put(ip, 'N', READY_CACHE_TIMEOUT)
        return ready == 'Y'

    @typing.override
    def get_link(
        self,
        userservice: 'models.UserService',
        transport: 'models.Transport',
        ip: str,
        os: 'types.os.DetectedOsInfo',
        user: 'models.User',
        password: str,
        request: 'ExtendedHttpRequestWithUser',
    ) -> str:
        # Build extra params dict for sshhtml5 gateway
        extra = {
            'username': self.username.value.strip(),
            'command': self.ssh_command.value.strip(),
            'host_key': self.ssh_host_key.value.strip(),
            'keepalive_interval': self.server_keep_alive.as_int(),
            'enable_sftp': self.enable_file_sharing.value in ('up', 'down', 'true'),
            'sftp_root': self.filesharing_root.value.strip(),
            'allow_upload': self.enable_file_sharing.value in ('up', 'true'),
            'allow_download': self.enable_file_sharing.value in ('down', 'true'),
            'allow_clipboard': self.enable_clipboard.value,
            'max_upload_size': self.max_upload_size.as_int() * 1024 * 1024,
            'title': f'UDS SSH {ip}',
        }

        ticket = models.TicketStore.create_for_tunnel(
            userservice=userservice,
            port=self.ssh_port.as_int(),
            extra=extra,
            validity=self.ticket_validity.as_int(),
        )

        onw = f'&{consts.transports.ON_NEW_WINDOW_VAR}={transport.uuid}'
        if self.force_new_window.value == consts.TRUE_STR:
            onw = f'&{consts.transports.ON_NEW_WINDOW_VAR}={userservice.deployed_service.uuid}'
        elif self.force_new_window.value == 'overwrite':
            onw = f'&{consts.transports.ON_SAME_WINDOW_VAR}=yes'

        tunnel_server = fields.get_tunnel_from_field(self.tunnel)
        return f'https://{tunnel_server.host}:{tunnel_server.port}/ssh/?ticket={ticket}{onw}'
