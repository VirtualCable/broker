# -*- coding: utf-8 -*-
#
# Copyright (c) 2026 Virtual Cable S.L.U.
# All rights reserved.
"""
Dashboard KPI drilldowns are exposed as read-only custom methods of the
servicespools / authenticators handlers (so they share those menu groups instead
of being separate top-level endpoints). This checks the routing, the returned
rows and the admin-only gating.

Author: Adolfo Gómez, dkmaster at dkmon dot com
"""
import logging

from uds import models
from uds.core.types.states import State

from ...utils import rest

logger = logging.getLogger(__name__)

# (endpoint, admin_only). Pools are permission objects, so the restrained
# listing is filtered per item instead of gated behind admin.
_DRILLDOWNS = [
    ('servicespools/restrained', False),
    ('servicespools/all_user_services', True),
    ('servicespools/all_assigned_services', True),
    ('authenticators/users_with_services', True),
    ('authenticators/all_groups', True),
]


class KpiDrilldownTest(rest.test.RESTTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.login()  # admin by default

    def test_drilldowns_return_rows(self) -> None:
        for url, _admin_only in _DRILLDOWNS:
            response = self.client.rest_get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertIsInstance(response.json(), list, url)

    def test_users_with_services_is_a_subset(self) -> None:
        rows = self.client.rest_get('authenticators/users_with_services').json()
        expected = models.User.objects.filter(userServices__state__in=State.VALID_STATES).distinct().count()
        self.assertEqual(len(rows), expected)
        self.assertLessEqual(len(rows), models.User.objects.count())

    def test_all_groups_lists_every_group(self) -> None:
        rows = self.client.rest_get('authenticators/all_groups').json()
        self.assertEqual(len(rows), models.Group.objects.count())

    def test_assigned_services_excludes_cache(self) -> None:
        all_us = self.client.rest_get('servicespools/all_user_services').json()
        assigned = self.client.rest_get('servicespools/all_assigned_services').json()
        self.assertTrue(all(r.get('owner') for r in assigned))
        self.assertLessEqual(len(assigned), len(all_us))

    def test_restrained_is_a_subset_of_pools(self) -> None:
        restrained = self.client.rest_get('servicespools/restrained').json()
        expected = models.ServicePool.restraineds_queryset().count()
        self.assertEqual(len(restrained), expected)

    def test_state_is_a_translated_literal(self) -> None:
        # No TableInfo behind a custom method, so states must arrive resolved
        # (a raw 'A' would reach the GUI table as-is).
        for row in self.client.rest_get('authenticators/all_groups').json():
            self.assertNotIn(row['state'], (State.ACTIVE, State.INACTIVE))

    def test_admin_only_gating(self) -> None:
        self.login(as_admin=False)  # staff, not admin
        for url, admin_only in _DRILLDOWNS:
            response = self.client.rest_get(url)
            self.assertEqual(response.status_code, 403 if admin_only else 200, url)
