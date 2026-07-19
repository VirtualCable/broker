# -*- coding: utf-8 -*-
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
import logging
import typing

from uds.core import mfas, exceptions
from uds import models

if typing.TYPE_CHECKING:
    from uds.core.types.requests import ExtendedHttpRequest

logger = logging.getLogger(__name__)


class TestMFA(mfas.MFA):
    """
    MFA provider for testing purposes with a deterministic code '123456'
    """

    type_name = 'Test MFA'
    type_type = 'testMFA'
    type_description = 'MFA for testing purposes'
    icon_file = 'mfa.png'

    @typing.override
    def send_code(
        self, request: 'ExtendedHttpRequest', userid: str, username: str, identifier: str, code: str
    ) -> mfas.MFA.RESULT:
        return mfas.MFA.RESULT.OK

    @typing.override
    def process(
        self,
        request: 'ExtendedHttpRequest',
        userid: str,
        username: str,
        identifier: str,
        validity: int | None = None,
    ) -> mfas.MFA.RESULT:
        # Store a deterministic code for testing
        self._put_data(request, userid, '123456')
        return mfas.MFA.RESULT.OK

    @typing.override
    def validate(
        self,
        request: 'ExtendedHttpRequest',
        userid: str,
        username: str,
        identifier: str,
        code: str,
        validity: int | None = None,
    ) -> None:
        data = self._get_data(request, userid)
        if data and data[1] == code:
            self._remove_data(request, userid)
            return
        raise exceptions.auth.MFAError('Invalid code')

    @typing.override
    def label(self) -> str:
        return 'Test Code'


def create_db_mfa(remember_device: int = 0, validity: int = 300) -> models.MFA:
    # Register TestMFA in the factory if not already registered
    factory = mfas.factory()
    if not factory.has(TestMFA.type_type):
        factory.insert(TestMFA)
    mfa = models.MFA()
    mfa.name = 'Testing MFA'
    mfa.data_type = TestMFA.type_type
    mfa.remember_device = remember_device
    mfa.validity = validity
    mfa.save()
    mfa.data = mfa.get_instance().serialize()
    mfa.save()
    return mfa
