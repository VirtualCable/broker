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

import re
import logging
import typing


from django.http import HttpResponseForbidden

from uds.core import consts
from uds.core.util.config import GlobalConfig
from uds.core.auths.auth import is_trusted_source, weblogout

from . import builder

logger = logging.getLogger(__name__)

if typing.TYPE_CHECKING:
    from django.http import HttpResponse
    from uds.core.types.requests import ExtendedHttpRequest

# Simple Bot detection
bot = re.compile(r'bot|spider', re.IGNORECASE)


def _process_request(request: 'ExtendedHttpRequest') -> 'HttpResponse | None':
    ua = request.META.get('HTTP_USER_AGENT', '') or 'Unknown'
    # If bot, break now
    if bot.search(ua) or (ua == 'Unknown' and not is_trusted_source(request.ip)):
        # Return emty response if bot is detected
        logger.info(
            'Denied Bot %s from %s to %s',
            ua,
            request.META.get(
                'REMOTE_ADDR',
                request.META.get('HTTP_X_FORWARDED_FOR', '').split(",")[-1],
            ),
            request.path,
        )
        return HttpResponseForbidden(content='Forbbiden', content_type='text/plain')

    if GlobalConfig.ENHANCED_SECURITY.as_bool():
        # Check that ip stored in session is the same as the one that is requesting if user is logged in
        session_ip = request.session.get(consts.auth.SESSION_IP_KEY, None)
        if request.user and session_ip and session_ip != request.ip:
            logger.info(
                'Denied request from %s to %s. User %s is logged in from a different IP (%s)',
                request.ip,
                request.path,
                request.user,
                request.session.get('ip', None),
            )

            # Clear session and redirect to login, skipping manager
            weblogout(request)

    return None


def _process_response(
    request: 'ExtendedHttpRequest',
    response: 'HttpResponse',
) -> 'HttpResponse':
    if GlobalConfig.ENHANCED_SECURITY.as_bool():
        # Security headers
        response['X-Content-Type-Options'] = 'nosniff'
        response['Referrer-Policy'] = 'strict-origin-when-cross-origin'

        # Content Security Policy
        csp_parts = [
            "default-src 'self' udssv2:;",
            "script-src 'self' 'unsafe-inline' udssv2:;",
            "style-src 'self' 'unsafe-inline';",
            "img-src 'self' https: data:;",
            "font-src 'self' data:;",
            "connect-src 'self' *;",
            "object-src 'none';",
            "base-uri 'self';",
            "frame-ancestors 'none';",
        ]
        response['Content-Security-Policy'] = " ".join(csp_parts)

        if request.is_secure():
            response['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'

    return response


# Content-Security-Policy: default-src 'self' udssv2:; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' https: data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none';


# Compatibility with old middleware, so we can use it in settings.py as it was
UDSSecurityMiddleware = builder.build_middleware(_process_request, _process_response)
