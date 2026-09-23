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

import datetime
import hashlib
import hmac
import json
import logging
import time
import typing

from django.utils.translation import gettext_noop as _

from uds.core import exceptions, messaging, types
from uds.core.types.notifiers import NotificationGroup
from uds.core.ui import gui
from uds.core.util import validators

from . import queue

logger: logging.Logger = logging.getLogger(__name__)

WEBHOOK_TYPE: typing.Final[str] = "webhookNotifications"

HMAC_PLACEHOLDER: typing.Final[str] = "{{hmac}}"

# Tab for "non basic" fields, so basic configuration is as simple as possible
ADVANCED_TAB: typing.Final[str] = _("Advanced")

PLACEHOLDERS_HELP: typing.Final[str] = _(
    "Placeholders: {{group}}, {{identificator}}, {{level}}, {{message}}, {{timestamp}} "
    "and {{hmac}}. Values are JSON-escaped, so JSON templates are always valid."
)


class WebhookNotifier(messaging.Notifier):
    """
    Webhook notifier for event notifications.

    Delivers EVENT notifications to a configured HTTP endpoint. Rendering and
    signing happen synchronously (cheap, local), while the HTTP delivery is
    performed asynchronously by the WebhookDispatcherJob from a durable queue,
    in strict FIFO order per notifier.
    """

    type_name = _("Webhook notifications")
    type_type = WEBHOOK_TYPE
    type_description = _("Webhook notifications (events) delivery")
    icon_file = "webhook.png"

    # Webhooks are intended for events, not for logs
    accepts = frozenset((NotificationGroup.EVENT,))

    sensitive_fields = ("hmac_secret",)

    # : Basic configuration fields

    url = gui.TextField(
        length=256,
        label=_("URL"),
        order=1,
        tooltip=_("Destination URL that will receive the notifications"),
        required=True,
        pattern=types.ui.FieldPatternType.URL,
    )

    verb = gui.ChoiceField(
        label=_("HTTP verb"),
        order=2,
        tooltip=_("HTTP verb used on the request"),
        choices=[
            gui.choice_item("POST", "POST"),
            gui.choice_item("PUT", "PUT"),
            gui.choice_item("PATCH", "PATCH"),
            gui.choice_item("GET", "GET"),
        ],
        default="POST",
        required=True,
    )

    data_template = gui.TextField(
        length=4096,
        label=_("Data template"),
        order=3,
        lines=8,
        tooltip=_(
            "Template of the data to send. If empty, a default JSON payload is sent. "
            "With GET, all lines are joined into a single line. "
        )
        + PLACEHOLDERS_HELP,
        default="",
    )

    # : Advanced configuration fields

    content_type = gui.TextField(
        length=128,
        label=_("Content type"),
        order=10,
        tooltip=_("Content-Type header sent with the request (headers defined below take precedence)"),
        default="application/json",
        tab=ADVANCED_TAB,
    )

    headers = gui.TextField(
        length=2048,
        label=_("Headers"),
        order=11,
        lines=8,
        tooltip=_(
            "Extra headers, one 'Header: value' per line. "
            + PLACEHOLDERS_HELP
            + " If {{hmac}} is not used on any header and an HMAC secret is set, "
            "an 'X-UDS-Signature' header is added automatically."
        ),
        default="",
        tab=ADVANCED_TAB,
    )

    verify_ssl = gui.CheckBoxField(
        label=_("Verify SSL"),
        order=12,
        tooltip=_("If checked, the SSL certificate of the destination is verified"),
        default=True,
        tab=ADVANCED_TAB,
    )

    timeout = gui.NumericField(
        length=3,
        label=_("Timeout"),
        order=13,
        tooltip=_("Request timeout, in seconds"),
        default=10,
        min_value=1,
        max_value=60,
        required=True,
        tab=ADVANCED_TAB,
    )

    max_retention = gui.NumericField(
        length=5,
        label=_("Queue retention"),
        order=14,
        tooltip=_(
            "Maximum time (in hours) an undelivered notification is kept on the "
            "delivery queue before being discarded"
        ),
        default=24,
        min_value=1,
        max_value=24 * 7,
        required=True,
        tab=ADVANCED_TAB,
    )

    backoff_cap = gui.NumericField(
        length=5,
        label=_("Retry backoff cap"),
        order=15,
        tooltip=_(
            "Maximum delay (in seconds) between delivery retries after consecutive failures "
            "(starts at 5 seconds and doubles on each failure)"
        ),
        default=300,
        min_value=5,
        max_value=3600,
        required=True,
        tab=ADVANCED_TAB,
    )

    hmac_secret = gui.PasswordField(
        length=128,
        label=_("HMAC secret"),
        order=16,
        tooltip=_(
            "Shared secret used to compute the HMAC-SHA256 signature, exposed through "
            "the {{hmac}} placeholder. If empty, no signature is computed and {{hmac}} "
            "resolves to an empty string. In 'content' mode, the signature is computed "
            "over 'name=value' pairs of all placeholder values (sorted by name, joined "
            "with newlines, UTF-8). In 'body' mode, it is computed over the raw body "
            "bytes ({{hmac}} cannot be used inside the data template on this mode)."
        ),
        default="",
        tab=ADVANCED_TAB,
    )

    hmac_mode = gui.ChoiceField(
        label=_("HMAC mode"),
        order=17,
        tooltip=_("What is signed: the substituted values (content) or the raw body (body)"),
        choices=[
            gui.choice_item("content", _("Content")),
            gui.choice_item("body", _("Body")),
        ],
        default="content",
        tab=ADVANCED_TAB,
    )

    @typing.override
    def initialize(self, values: "types.core.ValuesType" = None) -> None:
        """
        Validates the configuration when saved from the administration interface.
        """
        if not values:
            return

        validators.validate_url(self.url.value)

        # Every non empty header line must contain a ':' separator
        for line in self.headers.value.splitlines():
            line = line.strip()
            if line and ":" not in line:
                raise exceptions.ui.ValidationError(
                    _("Invalid header line (missing ':' separator): {}").format(line)
                )

        # Signing the body with a placeholder embedded on it is impossible
        # (the signature would be part of the signed payload)
        if self.hmac_mode.value == "body" and HMAC_PLACEHOLDER in self.data_template.value:
            raise exceptions.ui.ValidationError(
                _("{{hmac}} placeholder cannot be used inside the data template when HMAC mode is 'body'")
            )

    @staticmethod
    def _json_escape(value: str) -> str:
        """
        Escapes a value so it can be safely embedded inside a JSON string.
        """
        return json.dumps(value, ensure_ascii=False)[1:-1]

    @staticmethod
    def _substitute(template: str, values: dict[str, str], hmac_value: str) -> str:
        """
        Substitutes placeholders on template. Values must be already escaped.
        """
        for name, value in values.items():
            template = template.replace("{{" + name + "}}", value)
        return template.replace(HMAC_PLACEHOLDER, hmac_value)

    def _values(
        self, group: messaging.NotificationGroup, identificator: str, level: messaging.LogLevel, message: str
    ) -> dict[str, str]:
        """
        Raw (unescaped) placeholder values for a notification.
        """
        return {
            "group": group.kind,
            "identificator": identificator,
            "level": str(level),
            "message": message,
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        }

    def _default_body(self, values: dict[str, str]) -> str:
        """
        Default JSON payload, used when no data template is provided.
        """
        return json.dumps(
            {
                "group": values["group"],
                "identificator": values["identificator"],
                "level": values["level"],
                "message": values["message"],
                "timestamp": values["timestamp"],
            },
            ensure_ascii=False,
        )

    def _render_body(self, values: dict[str, str], hmac_value: str) -> str:
        """
        Renders the request body from the data template (or the default payload).
        """
        template = self.data_template.value.strip()
        if not template:
            return self._default_body(values)
        if not hmac_value and HMAC_PLACEHOLDER in template:
            logger.warning(
                "Webhook notifier %s: {{hmac}} used on data template but no HMAC secret is configured",
                self.type_name,
            )
        escaped = {name: self._json_escape(value) for name, value in values.items()}
        return self._substitute(template, escaped, hmac_value)

    def _render_headers(self, values: dict[str, str], hmac_value: str, secret_present: bool) -> dict[str, str]:
        """
        Renders the request headers.

        If the secret is set but {{hmac}} is not referenced on any header,
        the signature is added automatically as 'X-UDS-Signature'.
        """
        escaped = {name: self._json_escape(value) for name, value in values.items()}
        headers: dict[str, str] = {}
        for line in self.headers.value.splitlines():
            line = line.strip()
            if not line:
                continue
            name, _, value = line.partition(":")
            headers[name.strip()] = self._substitute(value.strip(), escaped, hmac_value)
        headers.setdefault("Content-Type", self.content_type.value)
        if secret_present and HMAC_PLACEHOLDER not in self.headers.value:
            headers["X-UDS-Signature"] = hmac_value
        return headers

    def _compute_hmac(self, secret: str, values: dict[str, str], body: str) -> str:
        """
        Computes the HMAC-SHA256 signature.

        - 'content' mode: over 'name=value' pairs of the raw placeholder values,
          sorted by name and joined with newlines (UTF-8 encoded).
        - 'body' mode: over the raw body bytes.
        """
        if self.hmac_mode.value == "body":
            payload = body.encode("utf-8")
        else:
            canonical = "\n".join(f"{name}={value}" for name, value in sorted(values.items()))
            payload = canonical.encode("utf-8")
        return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()

    def _build_request(
        self, group: messaging.NotificationGroup, identificator: str, level: messaging.LogLevel, message: str
    ) -> queue.QueuedRequest:
        """
        Renders and signs a notification, returning the queue element.
        """
        values = self._values(group, identificator, level, message)
        secret = self.hmac_secret.value.strip()

        body = ""
        if secret and self.hmac_mode.value == "body":
            # {{hmac}} cannot be inside the body (validated), render it first
            body = self._render_body(values, hmac_value="")
            hmac_value = self._compute_hmac(secret, values, body)
        else:
            hmac_value = self._compute_hmac(secret, values, "") if secret else ""
            body = self._render_body(values, hmac_value)

        headers = self._render_headers(values, hmac_value, secret_present=bool(secret))

        if self.verb.value.upper() == "GET":
            # GET with a multiline body makes no sense; join all lines into one
            body = " ".join(body.splitlines())

        return queue.QueuedRequest(
            notifier_uuid=self.db_obj().uuid,
            created=time.time(),
            attempts=0,
            method=self.verb.value.upper(),
            url=self.url.value,
            headers=headers,
            body=body,
            timeout=self.timeout.as_int(),
            verify_ssl=self.verify_ssl.as_bool(),
            retention_seconds=self.max_retention.as_int() * 3600,
            backoff_cap_seconds=self.backoff_cap.as_int(),
        )

    @typing.override
    def notify(
        self, group: messaging.NotificationGroup, identificator: str, level: messaging.LogLevel, message: str
    ) -> None:
        """
        Renders, signs and enqueues the notification. Delivery is performed
        asynchronously by the WebhookDispatcherJob (never blocks the caller).
        """
        element = self._build_request(group, identificator, level, message)
        if not queue.enqueue(element):
            logger.error(
                "Webhook queue is full (%d pending elements), notification to %s dropped",
                queue.pending_count(),
                self.url.value,
            )
