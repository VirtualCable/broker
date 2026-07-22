# pylint: disable=no-member  # For some reason, pylint does not detect the Tab member of gui

#
# Copyright (c) 2012-2022 Virtual Cable S.L.
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
from uds.core import consts
from uds.core import transports
from uds.core import types
from uds.core import ui
from uds.core.util import fields

# Not imported at runtime, just for type checking
if typing.TYPE_CHECKING:
    from uds.core.types.requests import ExtendedHttpRequestWithUser

logger = logging.getLogger(__name__)

READY_CACHE_TIMEOUT = 30


class HTML5RDPTransport(transports.Transport):
    """
    Provides access via RDP to service.
    This transport can use an domain. If username processed by authenticator contains '@', it will split it and left-@-part will be username, and right password
    """

    type_name = _("HTML5 RDP")
    type_type = "HTML5RDPTransport"
    type_description = _("RDP protocol using HTML5 client")
    icon_file = "html5.png"

    own_link = True
    supported_oss = consts.os.ALL_OS_LIST
    PROTOCOL = types.transports.Protocol.RDP
    group = types.transports.Grouping.TUNNELED

    tunnel = fields.tunnel_field()

    force_empty_creds = ui.gui.CheckBoxField(
        label=_("Empty creds"),
        order=3,
        tooltip=_("If checked, the credentials used to connect will be emtpy"),
        tab=types.ui.Tab.CREDENTIALS,
    )
    forced_username = ui.gui.TextField(
        label=_("Username"),
        order=4,
        tooltip=_("If not empty, this username will be always used as credential"),
        tab=types.ui.Tab.CREDENTIALS,
    )
    forced_password = ui.gui.PasswordField(
        label=_("Password"),
        order=5,
        tooltip=_("If not empty, this password will be always used as credential"),
        tab=types.ui.Tab.CREDENTIALS,
    )
    force_no_domain = ui.gui.CheckBoxField(
        label=_("Without Domain"),
        order=6,
        tooltip=_("If checked, the domain part will always be emptied (to connecto to xrdp for example is needed)"),
        tab=types.ui.Tab.CREDENTIALS,
    )
    forced_domain = ui.gui.TextField(
        label=_("Domain"),
        order=7,
        tooltip=_("If not empty, this domain will be always used as credential (used as DOMAIN\\user)"),
        tab=types.ui.Tab.CREDENTIALS,
    )

    best_experience = ui.gui.CheckBoxField(
        label=_("Best experience"),
        order=18,
        tooltip=_(
            "If checked, wallpaper, desktop composition and font smoothing will be enabled "
            "(better user experience, more bandwidth)"
        ),
        tab=types.ui.Tab.PARAMETERS,
        default=True,
    )
    enable_audio = ui.gui.CheckBoxField(
        label=_("Enable Audio"),
        order=21,
        tooltip=_("If checked, the audio will be redirected to remote session (if client browser supports it)"),
        tab=types.ui.Tab.PARAMETERS,
        default=True,
    )
    enable_microphone = ui.gui.CheckBoxField(
        label=_("Enable Microphone"),
        order=22,
        tooltip=_("If checked, the microphone will be redirected to remote session (if client browser supports it)"),
        tab=types.ui.Tab.PARAMETERS,
    )
    enable_printing = ui.gui.CheckBoxField(
        label=_("Enable Printing"),
        order=23,
        tooltip=_("If checked, the printing will be redirected to remote session (if client browser supports it)"),
        tab=types.ui.Tab.PARAMETERS,
    )
    enable_file_sharing = ui.gui.ChoiceField(
        label=_("File Sharing"),
        order=24,
        tooltip=_("File upload/download redirection policy"),
        default="false",
        choices=[
            ui.gui.choice_item("false", _("Disable file sharing")),
            ui.gui.choice_item("down", _("Allow download only")),
            ui.gui.choice_item("up", _("Allow upload only")),
            ui.gui.choice_item("true", _("Enable file sharing")),
        ],
        tab=types.ui.Tab.PARAMETERS,
    )
    allow_clipboard = ui.gui.CheckBoxField(
        label=_("Allow clipboard"),
        order=25,
        tooltip=_("If checked, clipboard redirection will be enabled"),
        default=True,
        tab=types.ui.Tab.PARAMETERS,
    )

    enable_webcam = ui.gui.CheckBoxField(
        label=_("Enable Webcam"),
        order=26,
        tooltip=_(
            "If checked, the local webcam/camera will be redirected to the remote session "
            "(if client browser supports it)"
        ),
        default=False,
        tab=types.ui.Tab.PARAMETERS,
    )
    webcam_codec = ui.gui.ChoiceField(
        label=_("Webcam codec"),
        order=27,
        tooltip=_("Codec used to stream the webcam to the remote session"),
        default="best",
        choices=[
            ui.gui.choice_item("best", _("Best (H264, bandwidth efficient)")),
            ui.gui.choice_item("fastest", _("Fastest (MJPEG, low latency)")),
            ui.gui.choice_item("mjpeg", _("MJPEG")),
            ui.gui.choice_item("h264", _("H264")),
        ],
        tab=types.ui.Tab.PARAMETERS,
    )
    webcam_quality = ui.gui.NumericField(
        label=_("Webcam quality"),
        order=28,
        length=3,  # max 100
        tooltip=_("Webcam image quality (1-100). Only applies to MJPEG. Defaults to 80."),
        default=80,
        tab=types.ui.Tab.PARAMETERS,
    )
    webcam_fps = ui.gui.NumericField(
        label=_("Webcam FPS"),
        order=29,
        length=3,
        tooltip=_("Maximum webcam frames per second. Defaults to 15."),
        default=15,
        tab=types.ui.Tab.PARAMETERS,
    )
    webcam_max_width = ui.gui.NumericField(
        label=_("Webcam max width"),
        order=30,
        length=5,
        tooltip=_("Cap webcam width in pixels, keeping aspect ratio. 0 = original size."),
        default=0,
        tab=types.ui.Tab.PARAMETERS,
    )
    webcam_max_height = ui.gui.NumericField(
        label=_("Webcam max height"),
        order=31,
        length=5,
        tooltip=_("Cap webcam height in pixels, keeping aspect ratio. 0 = original size."),
        default=0,
        tab=types.ui.Tab.PARAMETERS,
    )

    ticket_validity = fields.tunnel_ticket_validity_field()

    force_new_window = ui.gui.ChoiceField(
        order=91,
        label=_("Force new HTML Window"),
        tooltip=_("Select windows behavior for new connections on HTML5"),
        required=True,
        choices=[
            ui.gui.choice_item(
                "false",
                _("Open every connection on the same window, but keeps UDS window."),
            ),
            ui.gui.choice_item("true", _("Force every connection to be opened on a new window.")),
            ui.gui.choice_item(
                "overwrite",
                _("Override UDS window and replace it with the connection."),
            ),
        ],
        default="true",
        tab=types.ui.Tab.ADVANCED,
    )

    nla = ui.gui.CheckBoxField(
        order=92,
        label=_("NLA authentication"),
        tooltip=_(
            "If checked, Network Level Authentication will be used. "
            "Requires valid credentials or the connection will fail. "
            "Uncheck to disable NLA (useful for credential providers or xrdp)."
        ),
        default=True,
        tab=types.ui.Tab.ADVANCED,
    )

    rdp_port = ui.gui.NumericField(
        order=93,
        length=5,  # That is, max allowed value is 65535
        label=_("RDP Port"),
        tooltip=_("Use this port as RDP port. Defaults to 3389."),
        required=True,  #: Numeric fields have always a value, so this not really needed
        default=3389,
        tab=types.ui.Tab.ADVANCED,
    )

    session_quality = ui.gui.ChoiceField(
        label=_("Session Quality"),
        order=94,
        tooltip=_("Quality of the session. Higher values mean better quality but more bandwidth."),
        required=True,
        choices=[
            ui.gui.choice_item("0", _("Ultra Performance")),
            ui.gui.choice_item("1", _("Performance")),
            ui.gui.choice_item("2", _("Balanced")),
            ui.gui.choice_item("3", _("High Quality")),
            ui.gui.choice_item("4", _("Lossless")),
        ],
        default="2",
        tab=types.ui.Tab.ADVANCED,
    )

    allow_quality_switch = ui.gui.CheckBoxField(
        label=_("Allow quality switch"),
        order=96,
        tooltip=_("If checked, users can change the image quality from the side menu during the session"),
        default=True,
        tab=types.ui.Tab.ADVANCED,
    )

    @typing.override
    def initialize(self, values: "types.core.ValuesType") -> None:
        if not values:
            return

    # Same check as normal RDP transport
    @typing.override
    def is_ip_allowed(self, userservice: "models.UserService", ip: str) -> bool:
        """
        Checks if the transport is available for the requested destination ip
        Override this in yours transports
        """
        logger.debug("Checking availability for %s", ip)
        ready = self.cache.get(ip)
        if not ready:
            # Check again for readyness
            if self.test_connectivity(userservice, ip, self.rdp_port.as_int()) is True:
                self.cache.put(ip, "Y", READY_CACHE_TIMEOUT)
                return True
            self.cache.put(ip, "N", READY_CACHE_TIMEOUT)
        return ready == "Y"

    @typing.override
    def processed_username(self, userservice: "models.UserService", user: "models.User") -> str:
        v = self.get_connection_info(userservice, user, "")
        return v.username

    @typing.override
    def get_connection_info(
        self,
        userservice: "models.UserService | models.ServicePool",
        user: "models.User",
        password: str,
        *,
        for_notify: bool = False,
    ) -> types.connections.ConnectionData:
        username = user.get_username_for_auth()

        # Maybe this is called from another provider, as for example WYSE, that need all connections BEFORE
        if isinstance(userservice, models.UserService):
            cdata = userservice.get_instance().get_connection_data()
            if cdata:
                username = cdata.username or username
                password = cdata.password or password

        if self.forced_password.value:
            password = self.forced_password.value

        if self.forced_username.value:
            username = self.forced_username.value

        proc = username.split("@", 1)
        if len(proc) > 1:
            domain = proc[1]
        else:
            domain = ""
        username = proc[0]

        for_azure = False
        forced_domain = self.forced_domain.value.strip().lower()
        if forced_domain:
            if forced_domain == "azuread":
                for_azure = True
            else:
                domain = forced_domain

        if self.force_empty_creds.as_bool():
            username, password, domain = "", "", ""

        if self.force_no_domain.as_bool():
            domain = ""

        if "." in domain:  # FQDN domain form
            username = username + "@" + domain
            domain = ""

        # If AzureAD, include it on username
        if for_azure:
            username = "AzureAD\\" + username

        # Fix username/password acording to os manager
        username, password = userservice.process_user_password(username, password)

        return types.connections.ConnectionData(
            protocol=self.PROTOCOL,
            username=username,
            service_type=types.services.ServiceType.VDI,
            password=password,
            domain=domain,
        )

    @typing.override
    def get_link(
        self,
        userservice: "models.UserService",
        transport: "models.Transport",
        ip: str,
        os: "types.os.DetectedOsInfo",  # pylint: disable=unused-argument
        user: "models.User",
        password: str,
        request: "ExtendedHttpRequestWithUser",  # pylint: disable=unused-argument
    ) -> str:
        creds_info = self.get_connection_info(userservice, user, password)

        # Build extra params dict for rdphtml5 gateway (matches Rust ConnectionData)
        extra: dict[str, typing.Any] = {
            "user": creds_info.username or None,
            "password": creds_info.password or None,
            "domain": creds_info.domain or None,
            "best_experience": self.best_experience.as_bool(),
            "options": {
                "nla": self.nla.as_bool(),
                "verify_ssl": False,
            },
            "redirections": {
                "clipboard": self.allow_clipboard.as_bool(),
                "audio": self.enable_audio.as_bool(),
                "mic": self.enable_microphone.as_bool(),
            },
            "allow_upload": self.enable_file_sharing.value in ("up", "true"),
            "allow_download": self.enable_file_sharing.value in ("down", "true"),
            "session_quality": self.session_quality.as_int(),
            "allow_quality_switch": self.allow_quality_switch.as_bool(),
            "title": f"RDP {ip}",
        }

        # Webcam redirection (matches Rust WebcamSettings). Only sent when enabled,
        # so the gateway keeps redirections.webcam = None otherwise. WebcamSettings is
        # #[serde(default)] on the gateway, so omitted keys fall back to its defaults.
        # cam_width/cam_height are filled at runtime from the browser's getUserMedia.
        if self.enable_webcam.as_bool():
            extra["redirections"]["webcam"] = {
                "enabled": True,
                "codec": self.webcam_codec.value,
                "quality": self.webcam_quality.as_int(),
                "fps": self.webcam_fps.as_int(),
                "max_width": self.webcam_max_width.as_int(),
                "max_height": self.webcam_max_height.as_int(),
            }

        ticket = models.TicketStore.create_for_tunnel(
            userservice=userservice,
            port=self.rdp_port.as_int(),
            extra=extra,
            validity=self.ticket_validity.as_int(),
        )

        onw = f"&{consts.transports.ON_NEW_WINDOW_VAR}={transport.uuid}"
        if self.force_new_window.value == consts.TRUE_STR:
            onw = f"&{consts.transports.ON_NEW_WINDOW_VAR}={userservice.deployed_service.uuid}"
        elif self.force_new_window.value == "overwrite":
            onw = f"&{consts.transports.ON_SAME_WINDOW_VAR}=yes"

        tunnel_server = fields.get_tunnel_from_field(self.tunnel)
        return f"https://{tunnel_server.host}:{tunnel_server.port}/rdp/?ticket={ticket}{onw}"
