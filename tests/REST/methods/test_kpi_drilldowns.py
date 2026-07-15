# -*- coding: utf-8 -*-
#
# Copyright (c) 2026 Virtual Cable S.L.U.
# All rights reserved.
"""
Dashboard KPI drilldowns are exposed as read-only custom methods of the
servicespools / authenticators handlers (so they share those menu groups instead
of being separate top-level endpoints). This checks the routing, the
rows/tableinfo branches and the admin-only gating.

Author: Adolfo Gómez, dkmaster at dkmon dot com
"""
import logging

from uds import models
from uds.core.types.states import State

from ...utils import rest

logger = logging.getLogger(__name__)

# (endpoint, admin_only)
_POOL_DRILLDOWNS = [
    ('servicespools/restrained', False),
    ('servicespools/all_user_services', True),
    ('servicespools/all_assigned_services', True),
]
_AUTH_DRILLDOWNS = [
    ('authenticators/all_users', True),
    ('authenticators/users_with_services', True),
    ('authenticators/all_groups', True),
]


class KpiDrilldownTest(rest.test.RESTTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.login()  # admin by default

    def test_rows_and_tableinfo(self) -> None:
        for url, _ in _POOL_DRILLDOWNS + _AUTH_DRILLDOWNS:
            rows = self.client.rest_get(f'{url}/overview')
            self.assertEqual(rows.status_code, 200, url)
            self.assertIsInstance(rows.json(), list, url)

            info = self.client.rest_get(f'{url}/tableinfo')
            self.assertEqual(info.status_code, 200, url)
            self.assertIn('fields', info.json(), url)

            # Untyped listing: types must be an empty list, not the rows
            types_resp = self.client.rest_get(f'{url}/types')
            self.assertEqual(types_resp.json(), [], url)

    def test_all_users_lists_every_user(self) -> None:
        rows = self.client.rest_get('authenticators/all_users/overview').json()
        self.assertEqual(len(rows), models.User.objects.count())

    def test_users_with_services_is_a_subset(self) -> None:
        rows = self.client.rest_get('authenticators/users_with_services/overview').json()
        expected = models.User.objects.filter(userServices__state__in=State.VALID_STATES).distinct().count()
        self.assertEqual(len(rows), expected)

    def test_assigned_services_excludes_cache(self) -> None:
        all_us = self.client.rest_get('servicespools/all_user_services/overview').json()
        assigned = self.client.rest_get('servicespools/all_assigned_services/overview').json()
        self.assertTrue(all(r.get('owner') for r in assigned))
        self.assertLessEqual(len(assigned), len(all_us))

    def test_admin_only_gating(self) -> None:
        self.login(as_admin=False)  # staff, not admin
        for url, admin_only in _POOL_DRILLDOWNS + _AUTH_DRILLDOWNS:
            resp = self.client.rest_get(f'{url}/overview')
            if admin_only:
                self.assertEqual(resp.status_code, 403, url)
            else:
                self.assertEqual(resp.status_code, 200, url)
