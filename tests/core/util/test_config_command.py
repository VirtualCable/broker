#
# Copyright (c) 2026 Virtual Cable S.L.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
#    * Redistributions of source code must retain the above copyright notice,
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
# FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE
# OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#

"""
Tests for the ``config`` management command:

* Updating an existing PASSWORD-typed configuration value (e.g.
  ``Security.rootPass``, the only one operators set through the CLI) hashes
  the new value on write, so plaintext secrets never reach the database.
* New keys created through the command keep the plain (UNKNOWN) behavior.
"""

import io

from django.core.management import call_command

from uds.core.managers.crypto import CryptoManager
from uds.core.util import config
from uds.models.config import Config as DBConfigModel

from tests.utils.test import UDSTestCase


def _get_row(section: str, key: str) -> DBConfigModel | None:
    return DBConfigModel.objects.filter(section=section, key=key).first()


class ConfigCommandTest(UDSTestCase):
    def test_update_existing_password_row_hashes_and_rotates(self) -> None:
        # Materialize Security.rootPass first (as the running application
        # does): the row exists with FieldType.PASSWORD and a hashed random
        # value nobody knows. Note get() may return either the raw default
        # (on the process that materializes the row) or the stored hash, so
        # assertions below only rely on the stored row.
        config.GlobalConfig.SUPER_USER_PASS.get(True)
        row = _get_row("Security", "rootPass")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.field_type, config.Config.FieldType.PASSWORD)

        out = io.StringIO()
        call_command("config", "Security.rootPass=new-root-password", stdout=out)  # nosec: hardcoded test value
        self.assertNotIn("could not be processed", out.getvalue())

        row = _get_row("Security", "rootPass")
        assert row is not None
        # Rotated: the stored hash matches only the new password, never the
        # plaintext typed by the operator.
        self.assertNotEqual(row.value, "new-root-password")  # nosec: hardcoded test value
        self.assertTrue(CryptoManager.manager().check_hash("new-root-password", row.value))

    def test_new_key_without_type_stays_plain(self) -> None:
        out = io.StringIO()
        call_command("config", "Custom.testplain=plainvalue", stdout=out)
        self.assertNotIn("could not be processed", out.getvalue())

        row = _get_row("Custom", "testplain")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.field_type, config.Config.FieldType.UNKNOWN)
        self.assertEqual(row.value, "plainvalue")
