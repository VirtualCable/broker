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

import dataclasses
import logging
import typing

from django.utils.translation import gettext_noop as _

from uds import models
from uds.core import transports
from uds.core import types
from uds.core import ui
from uds.core.util import fields

logger: logging.Logger = logging.getLogger(__name__)

READY_CACHE_TIMEOUT = 30

DEFAULT_SERVER_PORT = 14433
DEFAULT_SCREEN_WIDTH = 1920
DEFAULT_SCREEN_HEIGHT = 1080
DEFAULT_SESSION_FPS = 30

# Payload schema version of the session ticket consumed by the URP agent
# (uds-remote-protocol `service::broker::payload::PAYLOAD_VERSION`).
PAYLOAD_VERSION = 1


def _pruned_dict(value: typing.Any) -> dict[str, typing.Any]:
    """Serializes a dataclass to dict dropping None values at every level."""

    def _prune(v: typing.Any) -> typing.Any:
        if isinstance(v, dict):
            items: dict[str, typing.Any] = typing.cast("dict[str, typing.Any]", v)
            return {k: _prune(x) for k, x in items.items() if x is not None}
        return v

    return typing.cast("dict[str, typing.Any]", _prune(dataclasses.asdict(value)))


# --- Ticket payload (the `extra` of the connection ticket, consumed by the
# --- URP agent in the VM via the actor/v3 ticket endpoint) -----------------


@dataclasses.dataclass
class TicketCredentials:
    """Auto-logon credentials for the credential provider."""

    user: str
    password: str
    domain: str = ""


@dataclasses.dataclass
class TicketDisplayPolicy:
    """Fixed remote geometry (the client scales locally)."""

    width: int
    height: int
    fps: int = DEFAULT_SESSION_FPS
    fixed: bool = True


@dataclasses.dataclass
class TicketChannelPolicy:
    """Channels the session offers (the client cannot enable what this disables)."""

    audio: bool = True
    clipboard: bool = True
    storage: bool = True
    smartcard: bool = False
    webcam: bool = False
    printer: bool = False
    lock: bool = False


@dataclasses.dataclass
class TicketPayloadData:
    """Mirror of the agent's `TicketPayload` (strict: no explicit nulls)."""

    channels: TicketChannelPolicy
    version: int = PAYLOAD_VERSION
    credentials: TicketCredentials | None = None
    display: TicketDisplayPolicy | None = None
    wallpaper_off: bool = True
    interactive_credentials: bool = False
    console_conflict: str = "take_over"
    console_takeover: str = "logoff"
    console_conflict_timeout_secs: int = 30

    def as_dict(self) -> dict[str, typing.Any]:
        return _pruned_dict(self)


# --- Client connection params (what the launcher's `URP.start(data)`
# --- expects; the session PSK arrives separately as `data.shared_secret`) --


@dataclasses.dataclass
class UrpRedirections:
    audio: bool | None = None
    mic: bool | None = None
    clipboard: bool | None = None
    printing: bool | None = None
    webcam: bool | None = None
    smartcard: bool | None = None
    storage: bool | None = None


@dataclasses.dataclass
class UrpOptions:
    preferred_codec: str | None = None  # None = auto (ladder)


@dataclasses.dataclass
class UrpConnectionParams:
    server: str
    ticket: str
    port: int = DEFAULT_SERVER_PORT
    # Credentials are never sent to the client unless they must be asked for:
    # `has_credentials` False makes the script prompt for user/password, and
    # the launcher forwards them to the agent (SessionCredentials message).
    user: str | None = None
    domain: str | None = None
    has_credentials: bool = True
    screen_width: int | None = None
    screen_height: int | None = None
    redirections: UrpRedirections | None = None
    options: UrpOptions | None = None

    def as_dict(self) -> dict[str, typing.Any]:
        return _pruned_dict(self)


class BaseUdsRPTransport(transports.Transport):
    """
    Base for the UDS Remote Protocol (URP) transports: our own remote
    desktop protocol, served by the URP agent installed in the VM.
    """

    is_base = True

    PROTOCOL = types.transports.Protocol.UDSRP
    supported_oss = (types.os.KnownOS.WINDOWS,)  # Agent is Windows-only for now

    force_empty_creds = ui.gui.CheckBoxField(
        label=_("Empty creds"),
        order=11,
        tooltip=_("If checked, no auto-logon credentials will be delivered to the VM"),
        tab=types.ui.Tab.CREDENTIALS,
    )
    forced_username = ui.gui.TextField(
        label=_("Username"),
        order=12,
        tooltip=_("If not empty, this username will be always used as credential"),
        tab=types.ui.Tab.CREDENTIALS,
    )
    forced_password = ui.gui.PasswordField(
        label=_("Password"),
        order=13,
        tooltip=_("If not empty, this password will be always used as credential"),
        tab=types.ui.Tab.CREDENTIALS,
    )
    force_no_domain = ui.gui.CheckBoxField(
        label=_("Without Domain"),
        order=14,
        tooltip=_("If checked, the domain part will always be emptied"),
        tab=types.ui.Tab.CREDENTIALS,
    )
    forced_domain = ui.gui.TextField(
        label=_("Domain"),
        order=15,
        tooltip=_("If not empty, this domain will be always used as credential (used as DOMAIN\\user)"),
        tab=types.ui.Tab.CREDENTIALS,
    )

    enable_audio = ui.gui.CheckBoxField(
        label=_("Enable Audio"),
        order=20,
        default=True,
        tooltip=_("If checked, the remote audio will be redirected to the client"),
        tab=types.ui.Tab.PARAMETERS,
    )
    enable_microphone = ui.gui.CheckBoxField(
        label=_("Enable Microphone"),
        order=21,
        default=False,
        tooltip=_("If checked, the client microphone will be redirected to the remote session"),
        tab=types.ui.Tab.PARAMETERS,
    )
    enable_clipboard = ui.gui.CheckBoxField(
        label=_("Allow clipboard"),
        order=22,
        default=True,
        tooltip=_("If checked, clipboard redirection will be enabled"),
        tab=types.ui.Tab.PARAMETERS,
    )
    enable_storage = ui.gui.CheckBoxField(
        label=_("Enable file transfer"),
        order=23,
        default=True,
        tooltip=_("If checked, file upload/download via the session spool will be enabled"),
        tab=types.ui.Tab.PARAMETERS,
    )
    enable_printers = ui.gui.CheckBoxField(
        label=_("Enable Printing"),
        order=24,
        default=False,
        tooltip=_("If checked, the client printers will be redirected to the remote session"),
        tab=types.ui.Tab.PARAMETERS,
    )
    enable_webcam = ui.gui.CheckBoxField(
        label=_("Enable Webcam"),
        order=25,
        default=False,
        tooltip=_("If checked, the client webcam will be redirected to the remote session"),
        tab=types.ui.Tab.PARAMETERS,
    )
    enable_smartcard = ui.gui.CheckBoxField(
        label=_("Enable Smartcard"),
        order=26,
        default=False,
        tooltip=_("If checked, the client smartcards will be redirected to the remote session"),
        tab=types.ui.Tab.PARAMETERS,
    )
    allow_lock = ui.gui.CheckBoxField(
        label=_("Allow remote lock"),
        order=27,
        default=False,
        tooltip=_("If checked, the client may ask to lock the remote console"),
        tab=types.ui.Tab.PARAMETERS,
    )
    wallpaper_off = ui.gui.CheckBoxField(
        label=_("Blank wallpaper"),
        order=28,
        default=True,
        tooltip=_("If checked, the desktop wallpaper is blanked during the session (saves bandwidth)"),
        tab=types.ui.Tab.PARAMETERS,
    )

    screen_width = ui.gui.NumericField(
        label=_("Screen width"),
        order=30,
        length=5,
        default=DEFAULT_SCREEN_WIDTH,
        min_value=0,
        tooltip=_(
            "Remote session width in pixels. 0 = not fixed by the broker "
            "(the client uses its local monitor geometry, capped)"
        ),
        tab=types.ui.Tab.DISPLAY,
    )
    screen_height = ui.gui.NumericField(
        label=_("Screen height"),
        order=31,
        length=5,
        default=DEFAULT_SCREEN_HEIGHT,
        min_value=0,
        tooltip=_(
            "Remote session height in pixels. 0 = not fixed by the broker "
            "(the client uses its local monitor geometry, capped)"
        ),
        tab=types.ui.Tab.DISPLAY,
    )
    session_fps = ui.gui.NumericField(
        label=_("Session FPS"),
        order=32,
        length=2,
        default=DEFAULT_SESSION_FPS,
        min_value=1,
        max_value=60,
        tooltip=_("Capture frames per second of the remote session"),
        tab=types.ui.Tab.DISPLAY,
    )
    server_port = ui.gui.NumericField(
        label=_("URP Port"),
        order=93,
        length=5,
        default=DEFAULT_SERVER_PORT,
        min_value=1,
        max_value=65535,
        required=True,
        tooltip=_("Port where the URP service listens on the VM. Defaults to 14433."),
        tab=types.ui.Tab.ADVANCED,
    )
    preferred_codec = ui.gui.ChoiceField(
        label=_("Preferred codec"),
        order=95,
        default="auto",
        choices=[
            ui.gui.choice_item("auto", _("Auto (quality ladder)")),
            ui.gui.choice_item("vp9", _("VP9")),
            ui.gui.choice_item("h264", _("H.264")),
        ],
        tooltip=_("Video codec preference. Auto lets the session ladder pick the best available."),
        tab=types.ui.Tab.ADVANCED,
    )
    console_policy = ui.gui.ChoiceField(
        label=_("Console conflict"),
        order=96,
        default="take_over",
        choices=[
            ui.gui.choice_item("take_over", _("Take over the console (evict the current owner)")),
            ui.gui.choice_item("deny", _("Deny the connection")),
            ui.gui.choice_item("ask", _("Ask the connecting client")),
        ],
        tooltip=_("What to do when the console session belongs to a different user"),
        tab=types.ui.Tab.ADVANCED,
    )
    console_takeover = ui.gui.ChoiceField(
        label=_("Takeover mode"),
        order=97,
        default="logoff",
        choices=[
            ui.gui.choice_item("logoff", _("Log off the evicted session")),
            ui.gui.choice_item("disconnect", _("Disconnect the evicted session (keeps apps)")),
        ],
        tooltip=_("How a takeover evicts the console owner"),
        tab=types.ui.Tab.ADVANCED,
    )
    console_conflict_timeout = ui.gui.NumericField(
        label=_("Conflict ask timeout"),
        order=98,
        length=4,
        default=30,
        min_value=5,
        max_value=3600,
        tooltip=_("Seconds to wait for the client answer when the console policy is 'ask' (fails closed)"),
        tab=types.ui.Tab.ADVANCED,
    )

    ticket_validity = fields.tunnel_ticket_validity_field(order=99)

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

        forced_domain = self.forced_domain.value.strip()
        if forced_domain:
            domain = forced_domain

        if self.force_empty_creds.as_bool():
            username, password, domain = "", "", ""

        if self.force_no_domain.as_bool():
            domain = ""

        if "." in domain:  # FQDN domain form
            username = username + "@" + domain
            domain = ""

        # Fix username/password according to os manager
        username, password = userservice.process_user_password(username, password)

        return types.connections.ConnectionData(
            protocol=self.PROTOCOL,
            username=username,
            service_type=types.services.ServiceType.VDI,
            password=password,
            domain=domain,
        )

    def build_ticket_payload(self, ci: types.connections.ConnectionData) -> TicketPayloadData:
        """Builds the session definition consumed by the VM agent."""
        credentials: TicketCredentials | None = None
        if ci.username:
            credentials = TicketCredentials(
                user=ci.username,
                password=ci.password or "",
                domain=ci.domain or "",
            )

        display: TicketDisplayPolicy | None = None
        width, height = self.screen_width.as_int(), self.screen_height.as_int()
        if width > 0 and height > 0:
            display = TicketDisplayPolicy(width=width, height=height, fps=self.session_fps.as_int())

        return TicketPayloadData(
            credentials=credentials,
            display=display,
            channels=TicketChannelPolicy(
                audio=self.enable_audio.as_bool(),
                clipboard=self.enable_clipboard.as_bool(),
                storage=self.enable_storage.as_bool(),
                smartcard=self.enable_smartcard.as_bool(),
                webcam=self.enable_webcam.as_bool(),
                printer=self.enable_printers.as_bool(),
                lock=self.allow_lock.as_bool(),
            ),
            wallpaper_off=self.wallpaper_off.as_bool(),
            # "Empty creds" means the client collects them interactively:
            # allow the agent to accept them (SessionCredentials).
            interactive_credentials=self.force_empty_creds.as_bool(),
            console_conflict=self.console_policy.value,
            console_takeover=self.console_takeover.value,
            console_conflict_timeout_secs=self.console_conflict_timeout.as_int(),
        )

    def build_connection_params(
        self, server: str, ticket: str, ci: types.connections.ConnectionData
    ) -> UrpConnectionParams:
        """Builds the params for the launcher's `URP.start(data)`."""
        width, height = self.screen_width.as_int(), self.screen_height.as_int()
        codec = self.preferred_codec.value
        return UrpConnectionParams(
            server=server,
            port=self.server_port.as_int(),
            ticket=ticket,
            user=ci.username or None,
            domain=ci.domain or None,
            has_credentials=bool(ci.username and ci.password),
            screen_width=width if width > 0 else None,
            screen_height=height if height > 0 else None,
            redirections=UrpRedirections(
                audio=self.enable_audio.as_bool(),
                mic=self.enable_microphone.as_bool(),
                clipboard=self.enable_clipboard.as_bool(),
                printing=self.enable_printers.as_bool(),
                webcam=self.enable_webcam.as_bool(),
                smartcard=self.enable_smartcard.as_bool(),
                storage=self.enable_storage.as_bool(),
            ),
            options=UrpOptions(
                preferred_codec=codec if codec != "auto" else None,
            ),
        )
