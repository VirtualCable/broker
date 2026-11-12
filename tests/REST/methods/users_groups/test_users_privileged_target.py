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
Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""
from uds import models
from uds.core import types
from uds.core.util import objtype

from ....fixtures import rest as rest_fixtures
from ....utils import rest

NEW_PASSWORD = "Str0ng!Pass-new"


class StaffEditingPrivilegedUsersTest(rest.test.RESTActorTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.staff = self.staffs[0]
        models.Permissions.add_permission(
            user=self.staff,
            object_type=objtype.ObjectType.from_model(self.auth),
            permission=types.permissions.PermissionType.ALL,
        )
        self.login(user=self.staff)

    def put_new_password(self, target: models.User) -> int:
        user_dct = rest_fixtures.createUser(id=target.uuid, name=target.name, password=NEW_PASSWORD)
        return self.client.rest_put(f"authenticators/{self.auth.uuid}/users/{target.uuid}", user_dct).status_code

    def assert_password_kept(self, target: models.User) -> None:
        old_hash = target.password
        target.refresh_from_db()
        self.assertEqual(target.password, old_hash)

    def assert_password_changed(self, target: models.User) -> None:
        old_hash = target.password
        target.refresh_from_db()
        self.assertNotEqual(target.password, old_hash)

    def test_staff_cannot_edit_admin(self) -> None:
        admin = self.admins[0]
        self.assertEqual(self.put_new_password(admin), 403)
        self.assert_password_kept(admin)

    def test_staff_cannot_edit_another_staff(self) -> None:
        other_staff = self.staffs[1]
        self.assertEqual(self.put_new_password(other_staff), 403)
        self.assert_password_kept(other_staff)

    def test_staff_can_edit_plain_user(self) -> None:
        plain_user = self.plain_users[0]
        self.assertEqual(self.put_new_password(plain_user), 200)
        self.assert_password_changed(plain_user)

    def test_staff_can_edit_itself(self) -> None:
        self.assertEqual(self.put_new_password(self.staff), 200)
        self.assert_password_changed(self.staff)


class AdminEditingPrivilegedUsersTest(rest.test.RESTActorTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.login(user=self.admins[0])

    def test_admin_can_edit_another_admin(self) -> None:
        target = self.admins[1]
        user_dct = rest_fixtures.createUser(id=target.uuid, name=target.name, password=NEW_PASSWORD)
        old_hash = target.password

        response = self.client.rest_put(f"authenticators/{self.auth.uuid}/users/{target.uuid}", user_dct)

        target.refresh_from_db()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotEqual(target.password, old_hash)
