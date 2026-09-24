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

import typing

from uds import models
from uds.core import consts
from uds.core.audit.immutable import ImmutableLogger
from uds.core.types import notifiers

# Import for REST using this module can access constants easily
# pylint: disable=unused-import
from uds.core.util.log import LogLevel, LogSource, log

if typing.TYPE_CHECKING:
    from .handlers import Handler
    from uds.core.types.requests import ExtendedHttpRequest

# This structct allows us to perform the following:
#   If path has ".../providers/[uuid]/..." we will replace uuid with "provider nanme" sourrounded by []
#   If path has ".../services/[uuid]/..." we will replace uuid with "service name" sourrounded by []
#   If path has ".../users/[uuid]/..." we will replace uuid with "user name" sourrounded by []
#   If path has ".../groups/[uuid]/..." we will replace uuid with "group name" sourrounded by []
UUID_REPLACER: tuple[
    tuple[
        str,
        type[
            models.Provider
            | models.Service
            | models.ServicePool
            | models.MetaPool
            | models.User
            | models.Group
            | models.Authenticator
            | models.Transport
            | models.OSManager
            | models.Network
            | models.Calendar
            | models.Account
            | models.MFA
            | models.Image
            | models.Notifier
        ],
    ],
    ...,
] = (
    ("providers", models.Provider),
    ("services", models.Service),
    ("servicespools", models.ServicePool),
    ("metapools", models.MetaPool),
    ("users", models.User),
    ("groups", models.Group),
    ("authenticators", models.Authenticator),
    ("transports", models.Transport),
    ("osmanagers", models.OSManager),
    ("networks", models.Network),
    ("calendars", models.Calendar),
    ("accounts", models.Account),
    ("mfa", models.MFA),
    ("gallery", models.Image),
    ("messaging", models.Notifier),
)


def replace_path(path: str) -> str:
    """Replaces uuids in path with names
    All paths are in the form .../type/uuid/...
    """
    for type, model in UUID_REPLACER:
        if f"/{type}/" in path:
            try:
                uuid = path.split(f"/{type}/")[1].split("/")[0]
                name = model.objects.get(uuid=uuid).name
                path = path.replace(uuid, f"[{name}]")
            except Exception:  # nosec: intentionally broad exception
                pass

    return path


# HTTP method -> event for "canonical" mutations on model handlers
_REST_METHOD_EVENT_TYPES: typing.Final[dict[str, notifiers.EventType]] = {
    "POST": notifiers.EventType.REST_CREATE,
    "PUT": notifiers.EventType.REST_UPDATE,
    "PATCH": notifiers.EventType.REST_UPDATE,
    "DELETE": notifiers.EventType.REST_DELETE,
}


def _rest_event_type(handler: "Handler") -> notifiers.EventType | None:
    """
    Resolves the REST event type for a successful (mutating) request.

    Only model handler based requests produce events. Canonical CRUD shapes
    (collection/item paths, master or detail) map to create/update/delete;
    any other mutation (custom methods such as publish, token, ...) maps to
    REST_OPERATION. Non model handlers (auth, system, actor, ...) produce
    no event.
    """
    method = (getattr(handler.request, "method", "") or "").upper()
    event_type = _REST_METHOD_EVENT_TYPES.get(method)
    if event_type is None:
        return None

    # Duck typed check: ModelHandler (at dispatcher level, detail operations
    # are still dispatched through the master handler) has MODEL/DETAIL.
    # Avoids a circular import with the model package.
    if not hasattr(handler, "MODEL"):
        return None

    args: list[str] = list(getattr(handler, "_args", None) or [])
    details = set(getattr(handler, "DETAIL", None) or {})
    is_detail = len(args) >= 2 and args[1] in details

    if method == "POST":
        if not args:
            return notifiers.EventType.REST_CREATE
        if is_detail and len(args) == 2:
            return notifiers.EventType.REST_CREATE  # detail create
    elif method in ("PUT", "PATCH"):
        if len(args) == 1:
            return notifiers.EventType.REST_UPDATE
        if not args:
            return notifiers.EventType.REST_CREATE  # legacy create
        if is_detail:
            return notifiers.EventType.REST_CREATE if len(args) == 2 else notifiers.EventType.REST_UPDATE
    elif method == "DELETE":
        if len(args) == 1:
            return notifiers.EventType.REST_DELETE
        if is_detail and len(args) == 3:
            return notifiers.EventType.REST_DELETE  # detail delete

    return notifiers.EventType.REST_OPERATION


def log_operation(handler: "Handler | None", response_code: int, level: LogLevel = LogLevel.INFO) -> None:
    """
    Logs a request
    """
    if not handler:
        return  # Nothing to log

    path = handler.request.path

    # If a common request, and no error, we don't log it because it's useless and a waste of resources
    if response_code < 400 and any(
        x in path
        for x in (
            consts.rest.OVERVIEW,
            consts.rest.GUI,
            consts.rest.TABLEINFO,
            consts.rest.TYPES,
            consts.rest.SYSTEM,
        )
    ):
        return

    path = replace_path(path)

    # Maybe user is not set already, this may be called
    user: typing.Any = handler.request.user

    username = user.pretty_name if user else "Unknown"
    log(
        None,  # > None Objects goes to SYSLOG (global log)
        level=level,
        message=f"{handler.request.ip} [{username}]: [{handler.request.method}/{response_code}] {path}"[:4096],
        source=LogSource.REST,
    )

    method = (handler.request.method or "").upper()

    # Denied (401/403) requests on model handlers are kept on the immutable
    # audit as evidence of the attempt ("e": true, i.e. privilege probing
    # from a valid session). They produce no event: the event stream carries
    # business facts (failed logins are the exception, having their own
    # LOGIN_FAILED type)
    if response_code in (401, 403) and hasattr(handler, "MODEL"):
        if ImmutableLogger.is_enabled():
            ImmutableLogger.append_object(
                {
                    "t": "rest",
                    "m": method,
                    "p": path,
                    "c": response_code,
                    "i": handler.request.ip,
                    "u": username,
                    "e": True,
                }
            )
        return

    # Immutable audit and event notification derive from the very same
    # decision, so they cannot diverge: only successful mutations on model
    # handlers produce both a "rest" audit entry and a rest.* event.
    # Handlers without model (actor, auth, system, tickets, ...) and read
    # methods are skipped here; their semantic facts, when they exist, are
    # emitted (and audited) from their own emission points (i.e. log_login
    # writes the "login"/"logout" audit entries and the user.* events)
    event_type = _rest_event_type(handler) if response_code < 400 else None
    if event_type is None:
        return

    if ImmutableLogger.is_enabled():
        ImmutableLogger.append_object(
            {
                "t": "rest",
                "m": method,
                "p": path,
                "c": response_code,
                "i": handler.request.ip,
                "u": username,
            }
        )

    event_type.notify(f"{username} ({handler.request.ip}): {method} {path}")


def log_denied_request(request: "ExtendedHttpRequest") -> None:
    """
    Logs a REST request denied while authenticating (no handler was ever
    created: invalid, expired or missing credentials).

    Deliberately cheap, because this runs on the path that mitigates
    invalid-auth floods: raw path (no uuid lookups on the database), one
    syslog entry and, if the immutable log is enabled, one entry flagged as
    denied.
    """
    method = (request.method or "").upper()
    # Defensive: some callers may provide plain HttpRequest-ish objects
    ip = getattr(request, "ip", request.META.get("REMOTE_ADDR", "unknown"))
    user: typing.Any = getattr(request, "user", None)
    username = user.pretty_name if user else "Unknown"

    log(
        None,  # > None Objects goes to SYSLOG (global log)
        level=LogLevel.ERROR,
        message=f"{ip} [{username}]: [{method}/403] {request.path}"[:4096],
        source=LogSource.REST,
    )

    if ImmutableLogger.is_enabled():
        ImmutableLogger.append_object(
            {
                "t": "rest",
                "m": method,
                "p": request.path,
                "c": 403,
                "i": ip,
                "u": username,
                "e": True,
            }
        )


def log_audit(handler: "Handler | None", action: str, level: LogLevel = LogLevel.INFO) -> None:
    """
    Logs a request
    """
    if not handler:
        return  # Nothing to log

    path = handler.request.path

    path = replace_path(path)

    # Maybe user is not set already, this may be called
    user: typing.Any = handler.request.user

    username = user.pretty_name if user else "Unknown"
    log(
        None,  # > None Objects goes to SYSLOG (global log)
        level=level,
        message=f"{handler.request.ip} [{username}]: {action} {path}"[:4096],
        source=LogSource.REST,
    )

    if ImmutableLogger.is_enabled():
        ImmutableLogger.append_object(
            {
                "t": "rest",
                "a": action,
                "p": path,
                "i": handler.request.ip,
                "u": username,
            }
        )
