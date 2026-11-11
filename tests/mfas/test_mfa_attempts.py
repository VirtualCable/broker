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
Author: Andres Schumann, aschumann at virtualcable dot es

Failed MFA code attempts are counted with the stored code, not with the HTTP session.
"""

import datetime
import typing

from uds.core import mfas
from uds.core import exceptions
from uds.core.util import config
from uds.core.util.model import sql_now

from tests.utils.test import UDSTestCase

CODE: typing.Final[str] = "123456"
WRONG: typing.Final[str] = "000000"


class _FakeMFA(mfas.MFA):
    """Minimal provider: sending always succeeds, nothing leaves the test."""

    def send_code(self, request, userid, username, identifier, code):  # type: ignore[override]
        return mfas.MFA.RESULT.OK


class _FakeRequest:
    ip = "192.0.2.10"


class MFAAttemptsTest(UDSTestCase):
    def setUp(self) -> None:
        self.budget = 3
        config.GlobalConfig.MAX_LOGIN_TRIES.set(str(self.budget))
        self.mfa = _FakeMFA(self.create_environment(), None)
        self.request = _FakeRequest()

    def _issue(self) -> None:
        self.mfa._put_data(self.request, "user-1", CODE)  # type: ignore[arg-type]

    def _validate(self, code: str) -> None:
        self.mfa.validate(self.request, "user-1", "user", "id", code)  # type: ignore[arg-type]

    def test_correct_code_is_accepted_and_removed(self) -> None:
        self._issue()
        self._validate(CODE)
        self.assertIsNone(self.mfa._get_data(self.request, "user-1"))  # type: ignore[arg-type]

    def test_failed_attempts_are_counted_in_storage(self) -> None:
        self._issue()
        for _ in range(2):
            with self.assertRaises(exceptions.auth.MFAError):
                self._validate(WRONG)
        data = self.mfa._get_data(self.request, "user-1")  # type: ignore[arg-type]
        self.assertIsNotNone(data)
        self.assertEqual(data[2], 2)  # type: ignore[index]

    def test_budget_exhausted_removes_the_code_even_if_it_is_right(self) -> None:
        self._issue()
        for _ in range(self.budget):
            with self.assertRaises(exceptions.auth.MFAError):
                self._validate(WRONG)
        # The right code no longer works: the budget is spent and the code is gone
        with self.assertRaises(exceptions.auth.MFAError):
            self._validate(CODE)
        self.assertIsNone(self.mfa._get_data(self.request, "user-1"))  # type: ignore[arg-type]

    def test_budget_survives_a_fresh_session(self) -> None:
        # A new HTTP session (new validate call on the same stored code) does not reset the count
        self._issue()
        for _ in range(self.budget - 1):
            with self.assertRaises(exceptions.auth.MFAError):
                self._validate(WRONG)
        self._validate(CODE)  # still inside budget: accepted
        self.assertIsNone(self.mfa._get_data(self.request, "user-1"))  # type: ignore[arg-type]

    def test_record_written_before_this_change_is_still_read(self) -> None:
        legacy = (sql_now(), CODE)
        self.mfa.storage.save_pickled(self.request.ip + "user-1", legacy)
        data = self.mfa._get_data(self.request, "user-1")  # type: ignore[arg-type]
        self.assertEqual(data[1:], (CODE, 0))  # type: ignore[index]

    def test_expired_code_is_rejected(self) -> None:
        self.mfa.storage.save_pickled(
            self.request.ip + "user-1",
            (sql_now() - datetime.timedelta(seconds=600), CODE, 0),
        )
        with self.assertRaises(exceptions.auth.MFAError):
            self.mfa.validate(self.request, "user-1", "user", "id", CODE, validity=60)  # type: ignore[arg-type]
