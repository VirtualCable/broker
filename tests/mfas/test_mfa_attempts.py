#
# Copyright (c) 2026 Virtual Cable S.L.
# All rights reserved.
#
"""
Author: Andres Schumann, aschumann at virtualcable dot es

The MFA attempts budget belongs to the user and provider, not to the HTTP session or the IP:
new codes do not reset it, a success resets it, and once spent it blocks new codes for a while.
"""

import datetime
import typing

from uds.core import exceptions, mfas
from uds.core.mfas.mfa import _MFA_LOCK_SECONDS
from uds.core.util import config
from uds.core.util.model import sql_now

from tests.utils.test import UDSTestCase

USERID: typing.Final[str] = "user-1"
WRONG: typing.Final[str] = "000000"


class _FakeMFA(mfas.MFA):
    """Minimal provider: sending always succeeds, nothing leaves the test."""

    def send_code(self, request, userid, username, identifier, code):  # type: ignore[override]
        return mfas.MFA.RESULT.OK


class _FakeRequest:
    def __init__(self, ip: str = "192.0.2.10") -> None:
        self.ip = ip


class MFAAttemptsTest(UDSTestCase):
    def setUp(self) -> None:
        self.budget = 3
        config.GlobalConfig.MAX_LOGIN_TRIES.set(str(self.budget))
        self.mfa = _FakeMFA(self.create_environment(), None)
        self.request = _FakeRequest()

    def _issue(self, request: _FakeRequest | None = None) -> str:
        self.mfa.process(request or self.request, USERID, "user", "id")  # type: ignore[arg-type]
        state = self.mfa._get_data(USERID)
        assert state is not None
        return state[1]

    def _validate(self, code: str, request: _FakeRequest | None = None) -> None:
        self.mfa.validate(request or self.request, USERID, "user", "id", code)  # type: ignore[arg-type]

    def _attempts(self) -> int:
        return self.mfa._budget(USERID)

    def test_correct_code_is_accepted_and_forgets_the_attempts(self) -> None:
        code = self._issue()
        with self.assertRaises(exceptions.auth.MFAError):
            self._validate(WRONG)
        self._validate(code)
        self.assertIsNone(self.mfa._get_data(USERID))

    def test_failed_attempts_are_stored_per_user_and_provider(self) -> None:
        self._issue()
        for _ in range(2):
            with self.assertRaises(exceptions.auth.MFAError):
                self._validate(WRONG)
        self.assertEqual(self._attempts(), 2)

    def test_budget_spent_voids_the_code_even_if_it_is_right(self) -> None:
        code = self._issue()
        for _ in range(self.budget):
            with self.assertRaises(exceptions.auth.MFAError):
                self._validate(WRONG)
        with self.assertRaises(exceptions.auth.MFAError):
            self._validate(code)
        self.assertIsNone(self.mfa._get_data(USERID))

    def test_new_code_does_not_reset_the_budget(self) -> None:
        self._issue()
        for _ in range(self.budget - 1):
            with self.assertRaises(exceptions.auth.MFAError):
                self._validate(WRONG)
        # The user asks for a new code: the attempts already spent are kept
        self.assertEqual(self._attempts(), self.budget - 1)
        code = self._issue()
        with self.assertRaises(exceptions.auth.MFAError):
            self._validate(WRONG)
        self.assertEqual(self._attempts(), self.budget)
        with self.assertRaises(exceptions.auth.MFAError):
            self._validate(code)

    def test_budget_is_not_per_ip(self) -> None:
        self._issue()
        for index in range(self.budget):
            with self.assertRaises(exceptions.auth.MFAError):
                self._validate(WRONG, _FakeRequest(ip=f"192.0.2.{index + 50}"))
        self.assertEqual(self._attempts(), self.budget)

    def test_no_new_code_while_the_budget_is_spent(self) -> None:
        self.mfa.storage.save_pickled(self.mfa._budget_key(USERID), (self.budget, sql_now()))
        with self.assertRaises(exceptions.auth.MFAError):
            self.mfa.process(self.request, USERID, "user", "id")  # type: ignore[arg-type]

    def test_budget_starts_again_after_the_lock(self) -> None:
        old_failure = sql_now() - datetime.timedelta(seconds=_MFA_LOCK_SECONDS + 1)
        self.mfa.storage.save_pickled(self.mfa._budget_key(USERID), (self.budget, old_failure))
        self._issue()
        self.assertEqual(self._attempts(), 0)

    def test_success_resets_the_budget(self) -> None:
        code = self._issue()
        with self.assertRaises(exceptions.auth.MFAError):
            self._validate(WRONG)
        self._validate(code)
        code = self._issue()
        self.assertEqual(self._attempts(), 0)
        self._validate(code)

    def test_record_written_before_this_change_is_still_read(self) -> None:
        self.mfa.storage.save_pickled(USERID, (sql_now(), "123456", 2, sql_now()))
        state = self.mfa._get_data(USERID)
        assert state is not None
        self.assertEqual(state[1], "123456")

    def test_expired_code_is_voided_and_the_attempts_are_kept(self) -> None:
        self.mfa.storage.save_pickled(USERID, (sql_now() - datetime.timedelta(seconds=600), "123456"))
        self.mfa.storage.save_pickled(self.mfa._budget_key(USERID), (2, sql_now()))
        with self.assertRaises(exceptions.auth.MFAError):
            self.mfa.validate(self.request, USERID, "user", "id", "123456", validity=60)  # type: ignore[arg-type]
        self.assertIsNone(self.mfa._get_data(USERID))
        self.assertEqual(self._attempts(), 2)

    def test_failure_recorded_by_providers_that_validate_on_their_own(self) -> None:
        # TOTP and RADIUS do not use the stored code: they count rejections through _record_failure
        for _ in range(self.budget - 1):
            self.mfa._record_failure(USERID)  # type: ignore[attr-defined]
        self.assertEqual(self._attempts(), self.budget - 1)
        with self.assertRaises(exceptions.auth.MFAError):
            self.mfa._record_failure(USERID)  # type: ignore[attr-defined]
        self.assertEqual(self._attempts(), self.budget)
        with self.assertRaises(exceptions.auth.MFAError):
            self.mfa._check_budget(USERID)  # type: ignore[attr-defined]
