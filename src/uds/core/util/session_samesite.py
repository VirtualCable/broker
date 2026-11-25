#
# Copyright (c) 2026 Virtual Cable S.L.U.
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
#    * Neither the name of Virtual Cable S.L.U. nor the names of its contributors
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
Author: Andres Schumann, aschumann at virtualcable dot es
"""

import logging
import typing

from django.conf import LazySettings, global_settings, settings

from uds.core.util.config import (
    SESSION_COOKIE_SAMESITE_DEFAULT,
    SESSION_COOKIE_SAMESITE_VALUES,
    GlobalConfig,
)

logger: logging.Logger = logging.getLogger(__name__)

SETTING_NAME: typing.Final[str] = "SESSION_COOKIE_SAMESITE"

_django_getattr: typing.Callable[[typing.Any, str], typing.Any] | None = None


def configured_value() -> str:
    # Cached per process like the rest of GlobalConfig: a change saved from the web applies to the
    # worker that saved it at once, and to the others after restarting uds and udsweb.
    value = GlobalConfig.SESSION_COOKIE_SAMESITE.as_str()
    if value in SESSION_COOKIE_SAMESITE_VALUES:
        return value
    logger.warning(
        "Invalid %s '%s' in global configuration, using %s",
        SETTING_NAME,
        value,
        SESSION_COOKIE_SAMESITE_DEFAULT,
    )
    return SESSION_COOKIE_SAMESITE_DEFAULT


def _getattr(wrapped: typing.Any, name: str) -> typing.Any:
    if name == SETTING_NAME:
        return configured_value()
    assert _django_getattr is not None
    return _django_getattr(wrapped, name)


def _verify() -> bool:
    # Django caches attribute lookups in the settings __dict__; a cached entry would bypass _getattr.
    return settings.SESSION_COOKIE_SAMESITE == configured_value()


def install() -> None:
    global _django_getattr
    if _django_getattr is not None:
        return

    _django_getattr = LazySettings.__getattr__
    del settings.SESSION_COOKIE_SAMESITE  # Removes the file value, so the lookup reaches _getattr
    LazySettings.__getattr__ = _getattr  # type: ignore[method-assign]  # Django offers no hook for per-key lookups

    if not _verify():
        logger.error("%s override is not effective, reverting to the Django default", SETTING_NAME)
        uninstall()


def uninstall() -> None:
    global _django_getattr
    if _django_getattr is None:
        return

    LazySettings.__getattr__ = _django_getattr  # type: ignore[method-assign]
    _django_getattr = None
    settings.SESSION_COOKIE_SAMESITE = global_settings.SESSION_COOKIE_SAMESITE
