#
# Copyright (c) 2026 Virtual Cable S.L.
# All rights reserved.
#
"""
Author: Andres Schumann, aschumann at virtualcable dot es

TOTP and RADIUS validate on their own, but the attempts budget must stop them before they check a code.
"""

from unittest import mock

from uds.core import exceptions
from uds.core.util.config import GlobalConfig
from uds.core.util.model import sql_now
from uds.mfas.Radius.mfa import RadiusOTP
from uds.mfas.TOTP.mfa import TOTP_MFA

from tests.utils.test import UDSTestCase

USERID = "user1"


class ProvidersAttemptsBudgetTest(UDSTestCase):
    def _spend_budget(self, mfa: TOTP_MFA | RadiusOTP) -> None:
        mfa.storage.save_pickled(mfa._budget_key(USERID), (GlobalConfig.MAX_LOGIN_TRIES.as_int(), sql_now()))

    def test_totp_refuses_before_checking_the_code(self) -> None:
        mfa = TOTP_MFA(self.create_environment(), None)
        self._spend_budget(mfa)
        with (
            mock.patch.object(TOTP_MFA, "ask_for_otp", return_value=True),
            mock.patch.object(TOTP_MFA, "_user_data") as user_data,
            self.assertRaises(exceptions.auth.MFAError),
        ):
            mfa.validate(mock.MagicMock(), USERID, USERID, "ident", "000000")
        user_data.assert_not_called()

    def test_radius_refuses_before_connecting(self) -> None:
        mfa = RadiusOTP(self.create_environment(), None)
        self._spend_budget(mfa)
        with (
            mock.patch.object(RadiusOTP, "radius_client") as radius_client,
            self.assertRaises(exceptions.auth.MFAError),
        ):
            mfa.validate(mock.MagicMock(), USERID, USERID, "ident", "000000")
        radius_client.assert_not_called()
