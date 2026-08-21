# pylint: disable=no-member   # ldap module gives errors to pylint
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
Runs against a real Active Directory, so it is skipped unless the [ad] section of
test-vars.ini is filled in and enabled. Expected keys:

    host, port, ssl, username, password  -- how to reach and bind the parent DC
    base                                 -- search base of the parent domain
    child_host, child_port, child_base   -- same, for the child domain
    user, user_password, group           -- an account on the parent domain and one
                                            of its groups
    child_user                           -- an account living on the child domain
    groupname_attr                       -- optional, overrides the stock AD default

Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""

import typing
from unittest import mock

from tests.utils import vars
from tests.utils.test import UDSTransactionTestCase
from uds import models
from uds.auths.RegexLdap import authenticator
from uds.core import auths, types
from uds.core.util import ldaputil

# Defaults for a stock Active Directory, so the ini only needs the site specific values
DEFAULT_USER_CLASS: typing.Final[str] = "user"
DEFAULT_USERID_ATTR: typing.Final[str] = "sAMAccountName"
DEFAULT_USERNAME_ATTR: typing.Final[str] = "displayName"
DEFAULT_GROUPNAME_ATTR: typing.Final[str] = "memberOf=^CN=([^,]+),.*"


class TestRegexLdapLiveDirectory(UDSTransactionTestCase):
    """
    Walks the whole authentication process against a real directory: bind, user
    lookup, group resolution across the forest (which the parent DC answers with
    a referral) and an effective login.

    Skipped unless the [ad] section of test-vars.ini is enabled. That section
    needs host, username, password, base, user, user_password, group,
    child_base, child_host and child_user.
    """

    vars: dict[str, str]
    db_auth: models.Authenticator
    auth: authenticator.RegexLdap

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.vars = vars.get_vars("ad")
        if not self.vars:
            self.skipTest("No ad vars")

        self.db_auth = models.Authenticator()
        self.db_auth.name = "Live directory"
        self.db_auth.data_type = authenticator.RegexLdap.type_type
        self.db_auth.save()  # Needs an id before serializing the instance

        self.auth = typing.cast(authenticator.RegexLdap, self.db_auth.get_instance())
        self.auth.host.value = self.vars["host"]
        self.auth.port.value = int(self.vars.get("port", "389"))
        self.auth.use_ssl.value = self.vars.get("ssl", "false") == "true"
        self.auth.username.value = self.vars["username"]
        self.auth.password.value = self.vars["password"]
        self.auth.ldap_base.value = self.vars["base"]
        self.auth.user_class.value = self.vars.get("user_class", DEFAULT_USER_CLASS)
        self.auth.userid_attr.value = self.vars.get("userid_attr", DEFAULT_USERID_ATTR)
        self.auth.username_attr.value = self.vars.get("username_attr", DEFAULT_USERNAME_ATTR)
        self.auth.groupname_attr.value = self.vars.get("groupname_attr", DEFAULT_GROUPNAME_ATTR)

        self.db_auth.data = self.auth.serialize()
        self.db_auth.save()

    def _connection(self, host: str, port: int) -> "ldaputil.LDAPConnection":
        return ldaputil.connection(
            self.vars["username"],
            self.vars["password"],
            host,
            port=port,
            use_ssl=self.vars.get("ssl", "false") == "true",
        )

    def _request(self) -> typing.Any:
        request = mock.Mock()
        request.ip = "127.0.0.1"
        request.os = types.os.DetectedOsInfo(types.os.KnownOS.LINUX, types.os.KnownBrowser.FIREFOX, "Linux")
        request.META = {}
        return request

    def test_bind_reaches_the_directory(self) -> None:
        con = self._connection(self.vars["host"], int(self.vars.get("port", "389")))
        self.assertTrue(con.bound)
        # Root DSE is the one entry any bound connection is allowed to read
        self.assertIsNotNone(ldaputil.get_root_dse(con))

    def test_user_is_found_on_parent_domain(self) -> None:
        user = self.auth._get_user(self.vars["user"])  # pyright: ignore[reportPrivateUsage]
        self.assertIsNotNone(user)
        self.assertTrue(typing.cast(ldaputil.LDAPResultType, user)["dn"])

    def test_user_groups_are_resolved(self) -> None:
        user = self.auth._get_user(self.vars["user"])  # pyright: ignore[reportPrivateUsage]
        groups = self.auth._get_groups(typing.cast(ldaputil.LDAPResultType, user))  # pyright: ignore[reportPrivateUsage]
        self.assertIn(self.vars["group"].lower(), [g.lower() for g in groups])

    def test_child_domain_search_returns_referral(self) -> None:
        # The parent DC does not hold the child domain, it points at it. This is
        # what has to be followed to reach users living on the child domain.
        con = self._connection(self.vars["host"], int(self.vars.get("port", "389")))
        # ldap3 chases referrals on its own by default, which consumes them before
        # they reach us. Turn it off so the referral surfaces on the result.
        con.auto_referrals = False
        with self.assertRaises(ldaputil.LDAPReferralError) as ctx:
            list(
                ldaputil.as_dict(
                    con,
                    self.vars["child_base"],
                    "(objectClass=user)",
                    raise_on_referrals=True,
                )
            )
        self.assertTrue(ctx.exception.referrals)

    def test_child_user_is_found_on_referred_domain(self) -> None:
        con = self._connection(self.vars["child_host"], int(self.vars.get("child_port", "389")))
        user = ldaputil.first(
            con=con,
            base=self.vars["child_base"],
            object_class=self.vars.get("user_class", DEFAULT_USER_CLASS),
            field=self.vars.get("userid_attr", DEFAULT_USERID_ATTR),
            value=self.vars["child_user"],
        )
        self.assertIsNotNone(user)

    def test_login_succeeds_and_validates_groups(self) -> None:
        self.db_auth.groups.create(name=self.vars["group"], comments="", is_meta=False)
        groups_manager = auths.GroupsManager(self.db_auth)

        result = self.auth.authenticate(
            self.vars["user"], self.vars["user_password"], groups_manager, self._request()
        )

        self.assertEqual(result.success, types.auth.AuthenticationState.SUCCESS)
        self.assertTrue(groups_manager.has_valid_groups())

    def test_login_fails_with_wrong_password(self) -> None:
        groups_manager = auths.GroupsManager(self.db_auth)

        result = self.auth.authenticate(self.vars["user"], "not the password", groups_manager, self._request())

        self.assertEqual(result.success, types.auth.AuthenticationState.FAIL)
