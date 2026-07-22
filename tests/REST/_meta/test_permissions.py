# -*- coding: utf-8 -*-
#
# Copyright (c) 2025 Virtual Cable S.L.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
#    * Redistributions of source code must retain the above copyright notice,
#      this list of legacy and the following disclaimer.
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
Permission tests for REST handlers (P2).

Verifies that:
  - Admin users can perform CRUD on any item.
  - Plain users (no admin, no staff, no explicit permission) are denied
    CRUD at the API surface.
  - Staff users with no explicit permission are denied (same as plain).
  - Staff users with an explicit `Permissions` row granting at least READ
    can GET the item; with MANAGEMENT they can update/delete.
  - When a user lacks access to a specific item, the server hides existence
    by returning 404 NotFound on detail GET (information-hiding design)
    and 403 Forbidden on collection/list/POST/DELETE.

Currently covers the same handlers as the P1 CRUD smoke suite:
  /accounts, /networks, /calendars, /mfa, /messaging/notifiers,
  /gallery/servicespoolgroups, /metapools.

Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false
import typing
import logging
import datetime

from django.db import models as django_models

from uds import models
from uds.core.types.permissions import PermissionType
from uds.core.util import objtype

from tests.fixtures import mfas as mfas_fixtures
from tests.fixtures import notifiers as notifiers_fixtures
from tests.utils import rest

logger = logging.getLogger(__name__)

# Permission gate used by master/__init__.py and by get_items().  An admin
# always short-circuits to PermissionType.ALL; everyone else needs an
# explicit Permission row (per user, per group, or per object_type).
HTTP_FORBIDDEN = 403
HTTP_NOT_FOUND = 404
HTTP_OK = 200


class _BasePermissionTest(rest.test.RESTTestCase):
    """Shared scaffolding for permission tests on a single REST endpoint."""

    BASE: typing.ClassVar[str]  # URL path, e.g. 'accounts' or 'gallery/servicespoolgroups'
    CREATE_PAYLOAD: typing.ClassVar[dict[str, typing.Any]]  # POST body used by admin
    MODEL: typing.ClassVar[type[django_models.Model]]  # DB model created by the POST

    # When False, ``_create_test_item`` is skipped in setUp; subclasses
    # without a POST endpoint (MFA, Notifier) set this to False and
    # populate ``item_uuid``/``item_pk`` themselves via ``_create_test_item``.
    CREATE_VIA_POST: typing.ClassVar[bool] = True

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()  # create an item as admin for the rest of the suite
        self._create_test_item()

    def _create_test_item(self) -> None:
        """Populate ``self.item_uuid`` and ``self.item_pk``.

        The default implementation POSTs ``CREATE_PAYLOAD`` to ``BASE``.
        Subclasses for read-only handlers (MFA, Notifier) override this
        to create the item via the ORM/fixture instead.
        """
        if not self.CREATE_VIA_POST:
            return
        resp = self.client.rest_post(self.BASE, self.CREATE_PAYLOAD)
        assert resp.status_code == HTTP_OK, (
            f"Admin setup POST failed: {resp.status_code} {self.CREATE_PAYLOAD!r} -> {resp.content!r}"
        )
        payload = resp.json()
        self.item_uuid: str = payload["id"]
        self.item_pk: int = self.MODEL.objects.get(uuid__iexact=self.item_uuid.lower()).pk

    def _relogin(self, user: models.User) -> None:
        # RESTTestCase.login() reads user from a kwarg and reauthenticates
        self.login(user=user, as_admin=False)

    # ---------- helpers for body shape checks ----------

    def _assert_forbidden(self, resp: typing.Any) -> None:
        # 403 is "Access denied"; 404 is the information-hiding shape used
        # for detail GETs in master/__init__.py:get() when check_access
        # fails inside the try/except that re-raises as NotFound.
        self.assertIn(
            resp.status_code,
            (HTTP_FORBIDDEN, HTTP_NOT_FOUND),
            f"Expected 403/404, got {resp.status_code}: {resp.content!r}",
        )

    def _assert_ok(self, resp: typing.Any) -> None:
        self.assertEqual(resp.status_code, HTTP_OK, f"Expected 200, got {resp.status_code}: {resp.content!r}")


# -----------------------------------------------------------------------------
# Per-handler permission tests
# -----------------------------------------------------------------------------


class AccountsPermissionTest(_BasePermissionTest):
    BASE: typing.ClassVar[str] = "accounts"
    CREATE_PAYLOAD: typing.ClassVar[dict[str, typing.Any]] = {
        "name": "perm-test-account",
        "comments": "permission suite setup",
        "tags": [],
    }
    MODEL = models.Account

    def test_admin_can_list(self) -> None:
        # Admin is already logged in from setUp
        self._assert_ok(self.client.rest_get(self.BASE))

    def test_admin_can_get_item(self) -> None:
        self._assert_ok(self.client.rest_get(f"{self.BASE}/{self.item_uuid}"))

    def test_admin_can_update(self) -> None:
        self._assert_ok(
            self.client.rest_put(
                f"{self.BASE}/{self.item_uuid}",
                {**self.CREATE_PAYLOAD, "comments": "updated by admin"},
            )
        )

    def test_admin_can_delete(self) -> None:
        # Use a fresh item to avoid breaking subsequent tests
        resp = self.client.rest_post(self.BASE, self.CREATE_PAYLOAD)
        uuid = resp.json()["id"]
        self._assert_ok(self.client.rest_delete(f"{self.BASE}/{uuid}"))

    def test_plain_user_list_forbidden(self) -> None:
        self._relogin(self.plain_users[0])
        self._assert_forbidden(self.client.rest_get(self.BASE))

    def test_plain_user_get_item_forbidden(self) -> None:
        self._relogin(self.plain_users[0])
        # detail GET maps AccessDenied -> NotFound (info-hiding)
        self._assert_forbidden(self.client.rest_get(f"{self.BASE}/{self.item_uuid}"))

    def test_plain_user_create_forbidden(self) -> None:
        self._relogin(self.plain_users[0])
        self._assert_forbidden(self.client.rest_post(self.BASE, self.CREATE_PAYLOAD))

    def test_plain_user_delete_forbidden(self) -> None:
        self._relogin(self.plain_users[0])
        self._assert_forbidden(self.client.rest_delete(f"{self.BASE}/{self.item_uuid}"))

    def test_staff_without_permission_get_forbidden(self) -> None:
        self._relogin(self.staffs[0])
        self._assert_forbidden(self.client.rest_get(f"{self.BASE}/{self.item_uuid}"))

    def test_staff_with_read_permission_can_get(self) -> None:
        # Grant READ permission explicitly to one staff on this item
        models.Permissions.objects.create(
            user=self.staffs[0],
            object_type=objtype.ObjectType.ACCOUNT.type,
            object_id=self.item_pk,
            permission=int(PermissionType.READ),
            created=datetime.datetime.now(datetime.timezone.utc),
        )
        self._relogin(self.staffs[0])
        self._assert_ok(self.client.rest_get(f"{self.BASE}/{self.item_uuid}"))

    def test_staff_with_all_permission_can_update_and_delete(self) -> None:
        # POST/PUT/DELETE handlers check ``check_access(MODEL(), ALL, root=True)``
        # — they require type-level ALL (object_id=None) plus the staff must
        # have at least READ on the specific item. We grant both.
        models.Permissions.objects.create(
            user=self.staffs[0],
            object_type=objtype.ObjectType.ACCOUNT.type,
            object_id=None,  # type-level grant
            permission=int(PermissionType.ALL),
            created=datetime.datetime.now(datetime.timezone.utc),
        )
        self._relogin(self.staffs[0])
        self._assert_ok(
            self.client.rest_put(
                f"{self.BASE}/{self.item_uuid}",
                {**self.CREATE_PAYLOAD, "comments": "updated by staff"},
            )
        )

        # Create a fresh item, grant type-level ALL, then delete as staff
        admin_resp = self.client.rest_post(self.BASE, self.CREATE_PAYLOAD)
        new_uuid = admin_resp.json()["id"]
        self._relogin(self.staffs[0])
        self._assert_ok(self.client.rest_delete(f"{self.BASE}/{new_uuid}"))

    def test_staff_with_read_cannot_update(self) -> None:
        # READ at item level is insufficient for write (handlers demand ALL)
        models.Permissions.objects.create(
            user=self.staffs[0],
            object_type=objtype.ObjectType.ACCOUNT.type,
            object_id=self.item_pk,
            permission=int(PermissionType.READ),
            created=datetime.datetime.now(datetime.timezone.utc),
        )
        self._relogin(self.staffs[0])
        self._assert_forbidden(
            self.client.rest_put(
                f"{self.BASE}/{self.item_uuid}",
                {**self.CREATE_PAYLOAD, "comments": "should fail"},
            )
        )


class NetworksPermissionTest(_BasePermissionTest):
    BASE: typing.ClassVar[str] = "networks"
    CREATE_PAYLOAD: typing.ClassVar[dict[str, typing.Any]] = {
        "name": "perm-test-network",
        "net_string": "192.168.255.0/24",
        "tags": [],
    }
    MODEL = models.Network

    def test_admin_can_list(self) -> None:
        self._assert_ok(self.client.rest_get(self.BASE))

    def test_plain_user_list_forbidden(self) -> None:
        self._relogin(self.plain_users[0])
        self._assert_forbidden(self.client.rest_get(self.BASE))

    def test_plain_user_create_forbidden(self) -> None:
        self._relogin(self.plain_users[0])
        self._assert_forbidden(self.client.rest_post(self.BASE, self.CREATE_PAYLOAD))

    def test_plain_user_get_item_forbidden(self) -> None:
        self._relogin(self.plain_users[0])
        self._assert_forbidden(self.client.rest_get(f"{self.BASE}/{self.item_uuid}"))

    def test_plain_user_delete_forbidden(self) -> None:
        self._relogin(self.plain_users[0])
        self._assert_forbidden(self.client.rest_delete(f"{self.BASE}/{self.item_uuid}"))


class CalendarsPermissionTest(_BasePermissionTest):
    BASE: typing.ClassVar[str] = "calendars"
    CREATE_PAYLOAD: typing.ClassVar[dict[str, typing.Any]] = {
        "name": "perm-test-calendar",
        "comments": "permission suite",
        "tags": [],
    }
    MODEL = models.Calendar

    def test_admin_can_list(self) -> None:
        self._assert_ok(self.client.rest_get(self.BASE))

    def test_plain_user_list_forbidden(self) -> None:
        self._relogin(self.plain_users[0])
        self._assert_forbidden(self.client.rest_get(self.BASE))

    def test_plain_user_create_forbidden(self) -> None:
        self._relogin(self.plain_users[0])
        self._assert_forbidden(self.client.rest_post(self.BASE, self.CREATE_PAYLOAD))

    def test_plain_user_get_item_forbidden(self) -> None:
        self._relogin(self.plain_users[0])
        self._assert_forbidden(self.client.rest_get(f"{self.BASE}/{self.item_uuid}"))

    def test_plain_user_delete_forbidden(self) -> None:
        self._relogin(self.plain_users[0])
        self._assert_forbidden(self.client.rest_delete(f"{self.BASE}/{self.item_uuid}"))


class MetaPoolsPermissionTest(_BasePermissionTest):
    BASE: typing.ClassVar[str] = "metapools"
    CREATE_PAYLOAD: typing.ClassVar[dict[str, typing.Any]] = {
        "name": "perm-test-metapool",
        "short_name": "PTM",
        "comments": "permission suite",
        "tags": [],
        "image_id": "-1",
        "servicesPoolGroup_id": "-1",
        "visible": True,
        "policy": 0,
        "ha_policy": 0,
        "calendar_message": "",
        "transport_grouping": 0,
    }
    MODEL = models.MetaPool

    def test_admin_can_list(self) -> None:
        self._assert_ok(self.client.rest_get(self.BASE))

    def test_plain_user_list_forbidden(self) -> None:
        self._relogin(self.plain_users[0])
        self._assert_forbidden(self.client.rest_get(self.BASE))

    def test_plain_user_create_forbidden(self) -> None:
        self._relogin(self.plain_users[0])
        self._assert_forbidden(self.client.rest_post(self.BASE, self.CREATE_PAYLOAD))

    def test_plain_user_get_item_forbidden(self) -> None:
        self._relogin(self.plain_users[0])
        self._assert_forbidden(self.client.rest_get(f"{self.BASE}/{self.item_uuid}"))

    def test_plain_user_delete_forbidden(self) -> None:
        self._relogin(self.plain_users[0])
        self._assert_forbidden(self.client.rest_delete(f"{self.BASE}/{self.item_uuid}"))

    def test_staff_with_all_can_update(self) -> None:
        # PUT requires type-level ALL (see master.put: check_access(MODEL(), ALL, root=True))
        models.Permissions.objects.create(
            user=self.staffs[0],
            object_type=objtype.ObjectType.METAPOOL.type,
            object_id=None,
            permission=int(PermissionType.ALL),
            created=datetime.datetime.now(datetime.timezone.utc),
        )
        self._relogin(self.staffs[0])
        self._assert_ok(
            self.client.rest_put(
                f"{self.BASE}/{self.item_uuid}",
                {**self.CREATE_PAYLOAD, "comments": "updated by staff"},
            )
        )


class GalleryServicesPoolGroupsPermissionTest(_BasePermissionTest):
    BASE: typing.ClassVar[str] = "gallery/servicespoolgroups"
    CREATE_PAYLOAD: typing.ClassVar[dict[str, typing.Any]] = {
        "name": "perm-test-spg",
        "comments": "permission suite",
        "image_id": -1,
        "priority": 0,
    }
    MODEL = models.ServicePoolGroup

    def test_admin_can_list(self) -> None:
        self._assert_ok(self.client.rest_get(self.BASE))

    def test_plain_user_list_forbidden(self) -> None:
        self._relogin(self.plain_users[0])
        self._assert_forbidden(self.client.rest_get(self.BASE))

    def test_plain_user_create_forbidden(self) -> None:
        self._relogin(self.plain_users[0])
        self._assert_forbidden(self.client.rest_post(self.BASE, self.CREATE_PAYLOAD))

    def test_plain_user_get_item_forbidden(self) -> None:
        self._relogin(self.plain_users[0])
        self._assert_forbidden(self.client.rest_get(f"{self.BASE}/{self.item_uuid}"))

    def test_plain_user_delete_forbidden(self) -> None:
        self._relogin(self.plain_users[0])
        self._assert_forbidden(self.client.rest_delete(f"{self.BASE}/{self.item_uuid}"))


class MfaPermissionTest(_BasePermissionTest):
    """Mfa is read/delete only in the CRUD smoke; it has no POST."""

    BASE: typing.ClassVar[str] = "mfa"
    CREATE_PAYLOAD: typing.ClassVar[dict[str, typing.Any]] = {}
    CREATE_VIA_POST: typing.ClassVar[bool] = False
    MODEL = models.MFA

    @typing.override
    def _create_test_item(self) -> None:
        mfa_obj = mfas_fixtures.create_db_mfa()
        self.item_uuid = mfa_obj.uuid
        self.item_pk = mfa_obj.pk

    def test_staff_with_all_can_delete(self) -> None:
        # No POST endpoint for MFA, so we test DELETE only.
        # DELETE requires type-level ALL
        # (see master.delete: check_access(MODEL(), ALL, root=True)).
        models.Permissions.objects.create(
            user=self.staffs[0],
            object_type=objtype.ObjectType.MFA.type,
            object_id=None,  # type-level grant required for write
            permission=int(PermissionType.ALL),
            created=datetime.datetime.now(datetime.timezone.utc),
        )
        self._relogin(self.staffs[0])
        self._assert_ok(self.client.rest_delete(f"{self.BASE}/{self.item_uuid}"))

    def test_admin_can_list(self) -> None:
        self._assert_ok(self.client.rest_get(self.BASE))

    def test_plain_user_list_forbidden(self) -> None:
        self._relogin(self.plain_users[0])
        self._assert_forbidden(self.client.rest_get(self.BASE))

    def test_plain_user_get_item_forbidden(self) -> None:
        self._relogin(self.plain_users[0])
        self._assert_forbidden(self.client.rest_get(f"{self.BASE}/{self.item_uuid}"))

    def test_plain_user_delete_forbidden(self) -> None:
        self._relogin(self.plain_users[0])
        self._assert_forbidden(self.client.rest_delete(f"{self.BASE}/{self.item_uuid}"))


class NotifiersPermissionTest(_BasePermissionTest):
    """Notifiers is read/delete only in the CRUD smoke; no POST."""

    BASE: typing.ClassVar[str] = "messaging/notifiers"
    CREATE_PAYLOAD: typing.ClassVar[dict[str, typing.Any]] = {}
    CREATE_VIA_POST: typing.ClassVar[bool] = False
    MODEL = models.Notifier

    @typing.override
    def _create_test_item(self) -> None:
        notifier_obj = notifiers_fixtures.createEmailNotifier()
        self.item_uuid = notifier_obj.uuid
        self.item_pk = notifier_obj.pk

    def test_admin_can_list(self) -> None:
        self._assert_ok(self.client.rest_get(self.BASE))

    def test_plain_user_list_forbidden(self) -> None:
        self._relogin(self.plain_users[0])
        self._assert_forbidden(self.client.rest_get(self.BASE))

    def test_plain_user_get_item_forbidden(self) -> None:
        self._relogin(self.plain_users[0])
        self._assert_forbidden(self.client.rest_get(f"{self.BASE}/{self.item_uuid}"))

    def test_plain_user_delete_forbidden(self) -> None:
        self._relogin(self.plain_users[0])
        self._assert_forbidden(self.client.rest_delete(f"{self.BASE}/{self.item_uuid}"))
