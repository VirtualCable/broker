# -*- coding: utf-8 -*-
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
import datetime
import logging
import typing

from django.http import HttpResponseForbidden
from django.utils import timezone

from uds.core.util import os_detector as OsDetector
from uds.core.util.config import GlobalConfig
from uds.core import consts, types
from uds.core.auths.auth import (
    root_user,
    weblogout,
)
from uds.models import User


from . import builder

if typing.TYPE_CHECKING:
    from django.http import HttpResponse
    from uds.core.types.requests import ExtendedHttpRequest


logger = logging.getLogger(__name__)

# How often to check the requests cache for stuck objects
CHECK_SECONDS = 3600 * 24  # Once a day is more than enough


def _fill_ips(request: 'ExtendedHttpRequest') -> None:
    """
    Obtains the IP of a Django Request, even behind a proxy.
    """
    from uds.core.util import net

    info = net.recover_ips(
        request.META.get('REMOTE_ADDR', ''),
        request.headers.get(consts.auth.X_FORWARDED_FOR_HEADER, ''),
    )
    request.ip = info.ip
    request.ip_proxy = info.ip_proxy
    request.ip_version = info.ip_version
    logger.debug('ip: %s, ip_proxy: %s', request.ip, request.ip_proxy)


def _get_user(request: 'ExtendedHttpRequest') -> None:
    """
    Ensures request user is the correct user
    """
    user_id = request.session.get(consts.auth.SESSION_USER_KEY)
    user: User | None = None
    if user_id:
        try:
            if user_id == consts.auth.ROOT_ID:
                user = root_user()
            else:
                user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            user = None
    if user and user.state != types.states.State.ACTIVE:
        user = None

    logger.debug('User at Middleware: %s %s', user_id, user)

    request.user = user


def _process_request(request: 'ExtendedHttpRequest') -> 'HttpResponse | None':
    # Add IP to request, user, ...
    # Add IP to request
    _fill_ips(request)
    request.authorized = request.session.get(consts.auth.SESSION_AUTHORIZED_KEY, False)

    # Ensures request contains os
    request.os = OsDetector.detect_os(request.headers)

    # Ensures that requests contains the valid user
    _get_user(request)

    # Now, check if session is timed out...
    if request.user:
        # return HttpResponse(content='Session Expired', status=403, content_type='text/plain')
        now = timezone.now()
        try:
            expiry = datetime.datetime.fromisoformat(request.session.get(consts.auth.SESSION_EXPIRY_KEY, ''))
            expiry = timezone.make_aware(expiry)
        except ValueError:
            expiry = now
        if expiry < now:
            try:
                return weblogout(request=request)
            except Exception:  # nosec: intentionaly catching all exceptions and ignoring them
                pass  # If fails, we don't care, we just want to logout
            return HttpResponseForbidden(content='Session Expired', content_type='text/plain')
        # Update session timeout..self.
        request.session[consts.auth.SESSION_EXPIRY_KEY] = (
            now
            + datetime.timedelta(
                seconds=(
                    GlobalConfig.SESSION_DURATION_ADMIN.as_int()
                    if request.user.is_staff()
                    else GlobalConfig.SESSION_DURATION_USER.as_int()
                )
            )
        ).isoformat()  # store as ISO format, str, json serilizable

    return None


def _process_response(request: 'ExtendedHttpRequest', response: 'HttpResponse') -> 'HttpResponse':
    # Update authorized on session
    if hasattr(request, 'session'):
        request.session[consts.auth.SESSION_AUTHORIZED_KEY] = request.authorized
    return response


# Compatibility with old middleware, so we can use it in settings.py as it was
GlobalRequestMiddleware = builder.build_middleware(_process_request, _process_response)
