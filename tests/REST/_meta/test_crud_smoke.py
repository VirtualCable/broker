# -*- coding: utf-8 -*-
#
# Copyright (c) 2025 Virtual Cable S.L.
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
# AND EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
# IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT,
# INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE
# OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF
# ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
CRUD smoke tests for handlers without dedicated test coverage (P1).

Tests the full create/read/update/delete lifecycle for handlers whose
models are simple enough to create with minimal fields.

Currently covers:
  - /accounts   (Account model — name, comments, tags)
  - /networks   (Network model — name, net_string, tags)
  - /calendars  (Calendar model — name, comments, tags)

Author: Adolfo Gómez, dkmaster at dkmon dot com
"""
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false
import typing
import logging

from tests.utils import rest

logger = logging.getLogger(__name__)


class AccountsCrudSmokeTest(rest.test.RESTTestCase):
    """CRUD lifecycle for /accounts."""

    BASE: typing.ClassVar[str] = 'accounts'

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    def test_create_and_read(self) -> None:
        """POST /accounts → create, then GET by uuid to verify."""
        payload = {'name': 'smoke-test-account', 'comments': 'Created by CRUD smoke test', 'tags': []}
        create_resp = self.client.rest_post(self.BASE, payload)
        self.assertEqual(
            create_resp.status_code, 200, f'POST create failed: {create_resp.content.decode(errors="replace")}'
        )
        data = create_resp.json()
        account_uuid = data.get('id')
        self.assertIsNotNone(account_uuid, f'No id in response: {data}')

        # GET the created item
        get_resp = self.client.rest_get(f'{self.BASE}/{account_uuid}')
        self.assertEqual(
            get_resp.status_code, 200, f'GET item failed: {get_resp.content.decode(errors="replace")}'
        )
        item = get_resp.json()
        self.assertEqual(item['name'], 'smoke-test-account')
        self.assertEqual(item['comments'], 'Created by CRUD smoke test')

    def test_list(self) -> None:
        """GET /accounts → list returns at least the created item."""
        payload = {'name': 'list-test-account', 'comments': '', 'tags': []}
        self.client.rest_post(self.BASE, payload)

        list_resp = self.client.rest_get(self.BASE)
        self.assertEqual(list_resp.status_code, 200)
        items = list_resp.json()
        self.assertIsInstance(items, list)
        names = [i.get('name') for i in items]
        self.assertIn('list-test-account', names)

    def test_update(self) -> None:
        """PUT /accounts/{uuid} → update, then GET to verify."""
        payload = {'name': 'update-test-account', 'comments': 'Before update', 'tags': []}
        create_resp = self.client.rest_post(self.BASE, payload)
        account_uuid = create_resp.json().get('id')

        update_payload = {'name': 'update-test-account', 'comments': 'After update', 'tags': []}
        put_resp = self.client.rest_put(f'{self.BASE}/{account_uuid}', update_payload)
        self.assertEqual(
            put_resp.status_code, 200, f'PUT update failed: {put_resp.content.decode(errors="replace")}'
        )

        get_resp = self.client.rest_get(f'{self.BASE}/{account_uuid}')
        self.assertEqual(get_resp.json()['comments'], 'After update')

    def test_delete(self) -> None:
        """DELETE /accounts/{uuid} → 200, then GET → 404."""
        payload = {'name': 'delete-test-account', 'comments': '', 'tags': []}
        create_resp = self.client.rest_post(self.BASE, payload)
        account_uuid = create_resp.json().get('id')

        del_resp = self.client.rest_delete(f'{self.BASE}/{account_uuid}')
        self.assertEqual(
            del_resp.status_code, 200, f'DELETE failed: {del_resp.content.decode(errors="replace")}'
        )

        get_resp = self.client.rest_get(f'{self.BASE}/{account_uuid}')
        self.assertEqual(get_resp.status_code, 404)


class NetworksCrudSmokeTest(rest.test.RESTTestCase):
    """CRUD lifecycle for /networks."""

    BASE: typing.ClassVar[str] = 'networks'

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    def test_create_and_read(self) -> None:
        """POST /networks → create, then GET by uuid."""
        payload = {'name': 'smoke-test-network', 'net_string': '192.168.1.0/24', 'tags': []}
        create_resp = self.client.rest_post(self.BASE, payload)
        self.assertEqual(
            create_resp.status_code, 200, f'POST create failed: {create_resp.content.decode(errors="replace")}'
        )
        net_uuid = create_resp.json().get('id')
        self.assertIsNotNone(net_uuid)

        get_resp = self.client.rest_get(f'{self.BASE}/{net_uuid}')
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()['name'], 'smoke-test-network')

    def test_list(self) -> None:
        """GET /networks → list."""
        payload = {'name': 'list-test-network', 'net_string': '10.0.0.0/8', 'tags': []}
        self.client.rest_post(self.BASE, payload)

        list_resp = self.client.rest_get(self.BASE)
        self.assertEqual(list_resp.status_code, 200)
        items = list_resp.json()
        self.assertIsInstance(items, list)
        names = [i.get('name') for i in items]
        self.assertIn('list-test-network', names)

    def test_update(self) -> None:
        """PUT /networks/{uuid} → update."""
        payload = {'name': 'update-test-network', 'net_string': '172.16.0.0/12', 'tags': []}
        create_resp = self.client.rest_post(self.BASE, payload)
        net_uuid = create_resp.json().get('id')

        update_payload = {'name': 'update-test-network', 'net_string': '192.168.0.0/16', 'tags': []}
        put_resp = self.client.rest_put(f'{self.BASE}/{net_uuid}', update_payload)
        self.assertEqual(put_resp.status_code, 200)

        get_resp = self.client.rest_get(f'{self.BASE}/{net_uuid}')
        self.assertEqual(get_resp.json()['net_string'], '192.168.0.0/16')

    def test_delete(self) -> None:
        """DELETE /networks/{uuid} → 200, then GET → 404."""
        payload = {'name': 'delete-test-network', 'net_string': '0.0.0.0/0', 'tags': []}
        create_resp = self.client.rest_post(self.BASE, payload)
        net_uuid = create_resp.json().get('id')

        self.client.rest_delete(f'{self.BASE}/{net_uuid}')

        get_resp = self.client.rest_get(f'{self.BASE}/{net_uuid}')
        self.assertEqual(get_resp.status_code, 404)


class CalendarsCrudSmokeTest(rest.test.RESTTestCase):
    """CRUD lifecycle for /calendars."""

    BASE: typing.ClassVar[str] = 'calendars'

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    def test_create_and_read(self) -> None:
        """POST /calendars → create, then GET by uuid."""
        payload = {'name': 'smoke-test-calendar', 'comments': 'Test calendar', 'tags': []}
        create_resp = self.client.rest_post(self.BASE, payload)
        self.assertEqual(
            create_resp.status_code, 200, f'POST create failed: {create_resp.content.decode(errors="replace")}'
        )
        cal_uuid = create_resp.json().get('id')
        self.assertIsNotNone(cal_uuid)

        get_resp = self.client.rest_get(f'{self.BASE}/{cal_uuid}')
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()['name'], 'smoke-test-calendar')

    def test_list(self) -> None:
        """GET /calendars → list."""
        payload = {'name': 'list-test-calendar', 'comments': '', 'tags': []}
        self.client.rest_post(self.BASE, payload)

        list_resp = self.client.rest_get(self.BASE)
        self.assertEqual(list_resp.status_code, 200)
        items = list_resp.json()
        self.assertIsInstance(items, list)
        names = [i.get('name') for i in items]
        self.assertIn('list-test-calendar', names)

    def test_update(self) -> None:
        """PUT /calendars/{uuid} → update."""
        payload = {'name': 'update-test-calendar', 'comments': 'Before', 'tags': []}
        create_resp = self.client.rest_post(self.BASE, payload)
        cal_uuid = create_resp.json().get('id')

        update_payload = {'name': 'update-test-calendar', 'comments': 'After', 'tags': []}
        put_resp = self.client.rest_put(f'{self.BASE}/{cal_uuid}', update_payload)
        self.assertEqual(put_resp.status_code, 200)

        get_resp = self.client.rest_get(f'{self.BASE}/{cal_uuid}')
        self.assertEqual(get_resp.json()['comments'], 'After')

    def test_delete(self) -> None:
        """DELETE /calendars/{uuid} → 200, then GET → 404."""
        payload = {'name': 'delete-test-calendar', 'comments': '', 'tags': []}
        create_resp = self.client.rest_post(self.BASE, payload)
        cal_uuid = create_resp.json().get('id')

        self.client.rest_delete(f'{self.BASE}/{cal_uuid}')

        get_resp = self.client.rest_get(f'{self.BASE}/{cal_uuid}')
        self.assertEqual(get_resp.status_code, 404)


class MfasCrudSmokeTest(rest.test.RESTTestCase):
    """CRUD lifecycle for /mfa (using fixture to create, REST for read/update/delete)."""

    BASE: typing.ClassVar[str] = 'mfa'

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()
        from tests.fixtures import mfas as mfas_fixtures

        self.mfa = mfas_fixtures.create_db_mfa()
        self.addCleanup(self.mfa.delete)

    def test_list(self) -> None:
        """GET /mfa → list includes fixture-created MFA."""
        resp = self.client.rest_get(self.BASE)
        self.assertEqual(resp.status_code, 200)
        items = resp.json()
        names = [i.get('name') for i in items]
        self.assertIn(self.mfa.name, names)

    def test_read(self) -> None:
        """GET /mfa/{uuid} → item matches fixture."""
        resp = self.client.rest_get(f'{self.BASE}/{self.mfa.uuid}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['name'], self.mfa.name)

    def test_update(self) -> None:
        """PUT /mfa/{uuid} → update comments."""
        payload = {
            'name': self.mfa.name,
            'comments': 'Updated by CRUD test',
            'tags': [],
            'remember_device': 0,
            'validity': 0,
        }
        resp = self.client.rest_put(f'{self.BASE}/{self.mfa.uuid}', payload)
        self.assertEqual(resp.status_code, 200)

        get_resp = self.client.rest_get(f'{self.BASE}/{self.mfa.uuid}')
        self.assertEqual(get_resp.json()['comments'], 'Updated by CRUD test')

    def test_delete(self) -> None:
        """DELETE /mfa/{uuid} → 200, then GET → 404."""
        from tests.fixtures import mfas as mfas_fixtures

        mfa = mfas_fixtures.create_db_mfa()
        resp = self.client.rest_delete(f'{self.BASE}/{mfa.uuid}')
        self.assertEqual(resp.status_code, 200)

        get_resp = self.client.rest_get(f'{self.BASE}/{mfa.uuid}')
        self.assertEqual(get_resp.status_code, 404)


class NotifiersCrudSmokeTest(rest.test.RESTTestCase):
    """CRUD lifecycle for /messaging/notifiers (REST read/update/delete on fixture-created instance)."""

    BASE: typing.ClassVar[str] = 'messaging/notifiers'

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()
        from tests.fixtures import notifiers as notifiers_fixtures

        self.notifier = notifiers_fixtures.createEmailNotifier()
        self.addCleanup(self.notifier.delete)

    def test_list(self) -> None:
        """GET /notifiers → list includes fixture-created notifier."""
        resp = self.client.rest_get(self.BASE)
        self.assertEqual(resp.status_code, 200)
        items = resp.json()
        names = [i.get('name') for i in items]
        self.assertIn(self.notifier.name, names)

    def test_read(self) -> None:
        """GET /notifiers/{uuid} → item matches fixture."""
        resp = self.client.rest_get(f'{self.BASE}/{self.notifier.uuid}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['name'], self.notifier.name)

    def test_update(self) -> None:
        """PUT /notifiers/{uuid} → update name."""
        payload = {'name': 'updated-notifier', 'comments': '', 'level': 30000, 'tags': [], 'enabled': True}
        resp = self.client.rest_put(f'{self.BASE}/{self.notifier.uuid}', payload)
        self.assertEqual(resp.status_code, 200)

        get_resp = self.client.rest_get(f'{self.BASE}/{self.notifier.uuid}')
        self.assertEqual(get_resp.json()['name'], 'updated-notifier')
