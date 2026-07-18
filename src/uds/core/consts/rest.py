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
import typing

# REST API requests (..../REST/.../overview, ..../REST/.../types, etc)
OVERVIEW: typing.Final[str] = 'overview'
TYPES: typing.Final[str] = 'types'
TABLEINFO: typing.Final[str] = 'tableinfo'
GUI: typing.Final[str] = 'gui'
LOG: typing.Final[str] = 'log'
POSITION: typing.Final[str] = 'position'

SYSTEM: typing.Final[str] = 'system'  # Defined on system class, here for reference

# -- Deprecation headers (RFC 9745 / RFC 8594) --

# Unix timestamp of when legacy GET-modifier endpoints were first
# marked deprecated.  Used in the ``Deprecation`` response header.
DEPRECATION_TS: typing.Final[int] = 1752854400  # 2025-07-18T00:00:00Z

# HTTP-date for ``Sunset`` header (RFC 8594).  Points to the
# estimated v7 removal window.  The value is informational only.
SUNSET_DATE: typing.Final[str] = 'Sat, 01 Jan 2030 00:00:00 GMT'

# HTTP methods the REST dispatcher understands.
# Used to gate incoming requests and compute the ``Allow`` header.
KNOWN_METHODS: typing.Final[tuple[str, ...]] = ('get', 'post', 'put', 'delete', 'options')

# Standard ``Allow`` header value returned by OPTIONS (uppercase).
DEFAULT_ALLOW: typing.Final[str] = ', '.join(m.upper() for m in KNOWN_METHODS)


class _NotFound:
    pass


# Not found guard, unique
NOT_FOUND: typing.Final[_NotFound] = _NotFound()

ITEMS_LIMIT: typing.Final[int] = 4400
