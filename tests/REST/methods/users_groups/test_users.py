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

import collections.abc
import datetime
import functools
import logging
import typing

from django.utils import timezone

from uds import models
from uds.core import types
from uds.core.util import objtype
from uds.models.user import hash_api_token

from ....fixtures import rest as rest_fixtures
from ....utils import rest

logger: logging.Logger = logging.getLogger(__name__)

MUST_HAVE_FIELDS: typing.Final = {"name", "role", "real_name", "comments", "state", "last_access", "token"}


class UsersTest(rest.test.RESTActorTestCase):
    """
    Test users group rest api
    """

    @typing.override
    def setUp(self) -> None:
        timezone.activate(datetime.timezone.utc)
        super().setUp()
        self.login()

    def test_users(self) -> None:
        url = f"authenticators/{self.auth.uuid}/users"

        # Now, will work
        response = self.client.rest_get(f"{url}/overview")
        self.assertEqual(response.status_code, 200)
        users = response.json()
        self.assertEqual(
            len(users), rest.test.NUMBER_OF_ITEMS_TO_CREATE * 3
        )  # 3 because will create admins, staff and plain users
        # Ensure values are correct
        user: dict[str, typing.Any]
        for user in users:
            # Locate the user in the auth
            self.assertTrue(rest.assertions.assert_user_is(self.auth.users.get(name=user["name"]), user))

    def test_users_overview_groups(self) -> None:
        url = f"authenticators/{self.auth.uuid}/users"

        response = self.client.rest_get(f"{url}/overview")
        self.assertEqual(response.status_code, 200)
        for user in response.json():
            db_user = self.auth.users.get(name=user["name"])
            self.assertEqual(
                set(user["groups"]),
                {g.uuid for g in db_user.get_groups()},
                f"Groups of user {user['name']} do not match",
            )

    def test_users_tableinfo(self) -> None:
        url = f"authenticators/{self.auth.uuid}/users/tableinfo"

        # Now, will work
        response = self.client.rest_get(url)
        self.assertEqual(response.status_code, 200)
        tableinfo = response.json()
        self.assertIn("title", tableinfo)
        self.assertIn("subtitle", tableinfo)
        self.assertIn("fields", tableinfo)
        self.assertIn("row_style", tableinfo)

        # Ensure at least name, role, real_name comments, state and last_access are present on tableinfo['fields']
        fields: list[collections.abc.Mapping[str, typing.Any]] = tableinfo["fields"]

        self.assertTrue(
            functools.reduce(
                lambda x, y: x and y,  # pyright: ignore
                typing.cast(
                    collections.abc.Iterable[bool],
                    (next(iter(field.keys())) in MUST_HAVE_FIELDS for field in fields),
                ),
            )
        )

    def test_user(self) -> None:
        url = f"authenticators/{self.auth.uuid}/users"
        # Now, will work
        for i in self.users:
            response = self.client.rest_get(f"{url}/{i.uuid}")
            self.assertEqual(response.status_code, 200)
            user = response.json()
            self.assertTrue(
                rest.assertions.assert_user_is(i, user),
                f"User {i} {models.User.objects.filter(uuid=i.uuid).values()[0]} is not correct",
            )

        # invalid user
        response = self.client.rest_get(f"{url}/invalid")
        self.assertEqual(response.status_code, 400)

    def test_user_api_token_lifecycle(self) -> None:
        """Admins can create and revoke user API tokens via the same ``token`` endpoint."""
        user = self.users[0]
        token_url = f"authenticators/{self.auth.uuid}/users/{user.uuid}/token"

        created = self.client.rest_post(token_url)
        self.assertEqual(created.status_code, 200, created.content)
        created_data = created.json()
        self.assertTrue(created_data["token"].startswith("uat-"))
        self.assertEqual(created_data["token_hint"], user.properties["token_hint"])
        self.assertNotIn("token_hash", created_data)
        user.refresh_from_db()
        self.assertEqual(user.token_hash, hash_api_token(created_data["token"]))

        self.client.uds_headers["Authorization"] = f"Bearer {created_data['token']}"
        authenticated = self.client.rest_get("providers/overview")
        self.assertEqual(authenticated.status_code, 200, authenticated.content)

        replaced = self.client.rest_post(token_url)
        self.assertEqual(replaced.status_code, 400, replaced.content)

        deleted = self.client.rest_delete(token_url)
        self.assertEqual(deleted.status_code, 200, deleted.content)
        self.assertIsNone(deleted.json()["token"])
        user.refresh_from_db()
        self.assertIsNone(user.token_hash)
        self.assertNotIn("token_hint", user.properties)

        revoked_access = self.client.rest_get("providers/overview")
        self.assertEqual(revoked_access.status_code, 403, revoked_access.content)

    def test_user_token_hint_keeps_the_prefix_and_four_real_characters(self) -> None:
        """The prefix identifies the token as a user one, but must not eat the four cut characters."""
        user = self.users[0]
        token_url = f"authenticators/{self.auth.uuid}/users/{user.uuid}/token"

        created = self.client.rest_post(token_url)
        self.assertEqual(created.status_code, 200, created.content)
        raw_token, hint = created.json()["token"], created.json()["token_hint"]

        self.assertTrue(hint.startswith("uat-"))
        self.assertEqual(hint, f"uat-{raw_token[4:8]}...{raw_token[-4:]}")

    def test_user_token_only_for_admins(self) -> None:
        """Staff never issues nor revokes tokens, not even holding every permission."""
        user = self.users[0]
        token_url = f"authenticators/{self.auth.uuid}/users/{user.uuid}/token"

        staff = self.staffs[0]
        # The endpoint checks permissions on the type (root=True), so an object permission would not reach it
        models.Permissions.add_permission(
            user=staff,
            object_type=objtype.ObjectType.from_model(self.auth),
            permission=types.permissions.PermissionType.ALL,
        )
        self.login(user=staff)
        self.assertEqual(self.client.rest_get(f"authenticators/{self.auth.uuid}/users/overview").status_code, 200)
        self.assertEqual(self.client.rest_post(token_url).status_code, 403)
        self.assertEqual(self.client.rest_delete(token_url).status_code, 403)
        user.refresh_from_db()
        self.assertIsNone(user.token_hash)

        self.login(as_admin=True)
        self.assertEqual(self.client.rest_post(token_url).status_code, 200)

    def test_users_token_column(self) -> None:
        """The list shows the hint of whoever has a token, and never the hash."""
        url = f"authenticators/{self.auth.uuid}/users"
        with_token, without_token = self.users[0], self.users[1]

        created = self.client.rest_post(f"{url}/{with_token.uuid}/token")
        self.assertEqual(created.status_code, 200, created.content)
        hint = created.json()["token_hint"]

        listed = {i["name"]: i for i in self.client.rest_get(f"{url}/overview").json()}
        self.assertEqual(listed[with_token.name]["token"], hint)
        self.assertEqual(listed[without_token.name]["token"], "")
        self.assertNotIn("token_hash", listed[with_token.name])

        single = self.client.rest_get(f"{url}/{with_token.uuid}").json()
        self.assertEqual(single["token"], hint)

    def test_users_token_column_is_sortable(self) -> None:
        """The hint lives in a property, so sorting by it needs the annotated subquery."""
        url = f"authenticators/{self.auth.uuid}/users"
        first, second = self.users[0], self.users[1]
        hints: dict[str, str] = {}
        for user in (first, second):
            created = self.client.rest_post(f"{url}/{user.uuid}/token")
            self.assertEqual(created.status_code, 200, created.content)
            hints[user.name] = created.json()["token_hint"]

        listed = self.client.rest_get(f"{url}/overview?$orderby=token")
        listed_descending = self.client.rest_get(f"{url}/overview?$orderby=-token")
        self.assertEqual(listed.status_code, 200, listed.content)
        self.assertEqual(listed_descending.status_code, 200, listed_descending.content)

        ascending = [i["name"] for i in listed.json() if i["token"]]
        descending = [i["name"] for i in listed_descending.json() if i["token"]]

        self.assertEqual(ascending, sorted(hints, key=lambda name: hints[name]))
        self.assertEqual(descending, list(reversed(ascending)))

        # Whoever has no token must stay together at one end, not interleaved
        full_ascending = [i["token"] for i in listed.json()]
        self.assertEqual(full_ascending, sorted(full_ascending, key=lambda hint: (hint != "", hint)))

    def test_users_log(self) -> None:
        url = f"authenticators/{self.auth.uuid}/users/"
        # Now, will work
        for user in self.users:
            response = self.client.rest_get(url + f"{user.uuid}/log")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.json()), 4)  # INFO, WARN, ERROR, DEBUG

        # invalid user
        response = self.client.rest_get(url + "invalid/log")
        self.assertEqual(response.status_code, 400)

    def test_user_create_edit(self) -> None:
        url = f"authenticators/{self.auth.uuid}/users"
        user_dct = rest_fixtures.createUser(
            groups=[self.simple_groups[0].uuid, self.simple_groups[1].uuid, self.meta_groups[0].uuid]
        )
        # Now, will work
        response = self.client.rest_put(
            url,
            user_dct,
        )
        self.assertEqual(response.status_code, 200)
        # Get user from database and ensure values are correct
        dbusr = self.auth.users.get(name=user_dct["name"])

        # Fix user_dct to remove it for comparison. Meta groups cannot be directly "assigned" to users
        user_dct["groups"] = user_dct["groups"][:-1]
        self.assertTrue(rest.assertions.assert_user_is(dbusr, user_dct))

        self.assertEqual(response.status_code, 200)
        # Returns nothing

        # Now, will fail because name is already in use
        response = self.client.rest_put(
            url,
            user_dct,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

        user_dct = rest_fixtures.createUser(  # nosec: test password, also, "fixme" means "create a random password" in this case
            id=dbusr.uuid,
            groups=[self.simple_groups[2].uuid],
            password="fixme",
            mfa_data="mfadata",
        )

        response = self.client.rest_put(
            url,
            user_dct,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        # Get user from database and ensure values are correct
        dbusr = self.auth.users.get(name=user_dct["name"])
        self.assertTrue(rest.assertions.assert_user_is(dbusr, user_dct, compare_password=True))

    def test_user_delete(self) -> None:
        url = f"authenticators/{self.auth.uuid}/users"
        # Now, will work
        response = self.client.rest_delete(url + f"/{self.plain_users[0].uuid}")
        self.assertEqual(response.status_code, 200)
        # Returns nothing

        # Now, will fail because user does not exist
        response = self.client.rest_delete(url + f"/{self.plain_users[0].uuid}")
        self.assertEqual(response.status_code, 404)

    def test_user_userservices_and_servicepools(self) -> None:
        url = f"authenticators/{self.auth.uuid}/users/{self.plain_users[0].uuid}/userServices"
        # Now, will work
        response = self.client.rest_get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 2)

        # Same with service pools
        url = f"authenticators/{self.auth.uuid}/users/{self.plain_users[0].uuid}/servicesPools"
        response = self.client.rest_get(url)
        self.assertEqual(response.status_code, 200)
        groups = self.plain_users[0].groups.all()
        count = len(list(models.ServicePool.get_pools_for_groups(groups)))

        self.assertEqual(len(response.json()), count)
