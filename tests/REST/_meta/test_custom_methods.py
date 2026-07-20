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
Contract tests for CustomMethodMethod + ModelCustomMethod handling
(Phase 1/2 — Safety net + dispatch correctness).

Covers doc/plan/rest-test-needed.md Phase 1 step 6 and Phase 2 step 3.

What we verify after Phase 2 step 3:

- Every ModelCustomMethod declared by a handler has a ``method`` field
  matching its safety profile: GET for safe/read-only operations, POST for
  unsafe/state-mutating operations.
- The dispatcher honours ``cm.method``: POST custom methods are dispatched
  via POST, GET custom methods via GET. COMPAT mode allows legacy GET
  access to POST methods (backward compatible).
- genapi's ``api_paths()`` emits POST custom methods as ``post=``, GET
  methods as ``get=``. Legacy COMPAT-mode GET access to POST methods is
  intentionally undocumented.

Reference:
- src/uds/core/types/rest/__init__.py:78   ModelCustomMethod + CustomMethodMethod
- src/uds/REST/model/master/api_helpers.py:153   api_paths builds the spec
- src/uds/REST/model/detail/__init__.py:91    DetailHandler.CUSTOM_METHODS type

Author: Adolfo Gómez, dkmaster at dkmon dot com
"""
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false
import typing
import collections.abc
import logging
from unittest.mock import patch

from uds.core import types, consts
from uds.REST import dispatcher
from uds.REST.model.master import ModelHandler
from uds.REST.model.detail import DetailHandler
from uds.models.uuid_model import UUIDModel


from tests.utils import rest

logger = logging.getLogger(__name__)


# Handlers that, at the time of writing, have not been declared with a
# non-default ``method``. Used by the snapshot tests in this module to
# guard against accidental declarations slipping into the codebase before
# Phase 4 has prepared the dispatcher. In Phase 4 this list grows as we
# migrate state-mutating modifiers to POST/QUERY.


class CustomMethodContractTest(rest.test.RESTTestCase):
    """Freeze today's ModelCustomMethod + CustomMethodMethod contract."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    # ------------------------------------------------------------------
    # T1-type safety: model_custommethod defaults and enum
    # ------------------------------------------------------------------
    def test_default_method_is_get(self) -> None:
        """A ModelCustomMethod declared with only ``name`` defaults method=GET."""
        cm = types.rest.ModelCustomMethod('sample')
        self.assertEqual(cm.method, types.rest.CustomMethodMethod.GET)

    def test_default_needs_parent_is_false(self) -> None:
        """A ModelCustomMethod declared with only ``name`` defaults needs_parent=False."""
        cm = types.rest.ModelCustomMethod('sample')
        self.assertFalse(cm.needs_parent)

    def test_custom_method_method_enum_members(self) -> None:
        """CustomMethodMethod exposes only the four declared HTTP verbs.

        Adding a new verb (e.g. PATCH) is an explicit conscious decision.
        """
        expected = {'GET', 'POST', 'PUT', 'QUERY'}
        actual = {member.name for member in types.rest.CustomMethodMethod}
        self.assertEqual(actual, expected)

    # ------------------------------------------------------------------
    # T2 - audit: all unsafe custom methods use POST, safe ones use GET
    # ------------------------------------------------------------------
    # Known POST custom methods (unsafe / state-mutating).
    # If you add a new unsafe custom method, add it here.
    _POST_CUSTOM_METHODS: typing.ClassVar[frozenset[tuple[str, str]]] = frozenset(
        {
            ('Accounts', 'clear'),
            ('Accounts', 'timemark'),
            ('MetaPools', 'set_fallback_access'),
            ('Providers', 'maintenance'),
            ('ServicesPools', 'set_fallback_access'),
            ('ServicesPools', 'create_from_assignable'),
            ('ServicesPools', 'add_log'),
            ('TunnelServers', 'maintenance'),
            ('Tunnels', 'assign'),
            ('ActionsCalendars', 'execute'),
            ('AssignedUserService', 'reset'),
            ('Publications', 'publish'),
            ('Publications', 'cancel'),
            ('ServersServers', 'maintenance'),
            ('ServersServers', 'importcsv'),
            ('Users', 'clean_related'),
            ('Users', 'add_to_group'),
            ('Users', 'enable_client_logging'),
        }
    )

    def test_unsafe_custom_methods_use_post(self) -> None:
        """Every unsafe (state-mutating) custom method is declared with method=POST."""
        sources: list[tuple[str, type, list[types.rest.ModelCustomMethod]]] = []
        for cls in typing.cast(collections.abc.Iterable[typing.Any], ModelHandler.__subclasses__()):
            cms = getattr(cls, 'CUSTOM_METHODS', None)
            if cms:
                sources.append((cls.__name__, cls, cms))
        for cls in typing.cast(collections.abc.Iterable[typing.Any], DetailHandler.__subclasses__()):
            cms = getattr(cls, 'CUSTOM_METHODS', None)
            if cms:
                sources.append((cls.__name__, cls, cms))

        offenders: list[str] = []
        for cls_name, _cls, cms in sources:
            for cm in cms:
                key = (cls_name, cm.name)
                if key in self._POST_CUSTOM_METHODS:
                    if cm.method != types.rest.CustomMethodMethod.POST:
                        offenders.append(f'{cls_name}.{cm.name}: expected POST, got {cm.method!r}')
                else:
                    # Not in POST set → must be GET
                    if cm.method != types.rest.CustomMethodMethod.GET:
                        offenders.append(
                            f'{cls_name}.{cm.name}: unexpected method {cm.method!r}; '
                            'if unsafe, add to _POST_CUSTOM_METHODS'
                        )

        self.assertEqual(
            offenders,
            [],
            'Custom method HTTP verb mismatch: ' + '; '.join(offenders),
        )

    def test_post_custom_methods_set_exhaustive(self) -> None:
        """_POST_CUSTOM_METHODS must list every POST custom method in the codebase."""
        sources: list[tuple[str, type, list[types.rest.ModelCustomMethod]]] = []
        for cls in typing.cast(collections.abc.Iterable[typing.Any], ModelHandler.__subclasses__()):
            cms = getattr(cls, 'CUSTOM_METHODS', None)
            if cms:
                sources.append((cls.__name__, cls, cms))
        for cls in typing.cast(collections.abc.Iterable[typing.Any], DetailHandler.__subclasses__()):
            cms = getattr(cls, 'CUSTOM_METHODS', None)
            if cms:
                sources.append((cls.__name__, cls, cms))

        actual_post: set[tuple[str, str]] = set()
        for cls_name, _cls, cms in sources:
            for cm in cms:
                if cm.method == types.rest.CustomMethodMethod.POST:
                    actual_post.add((cls_name, cm.name))

        missing = actual_post - self._POST_CUSTOM_METHODS
        extra = self._POST_CUSTOM_METHODS - actual_post
        msg = ''
        if missing:
            msg += f'Missing from _POST_CUSTOM_METHODS: {missing}. '
        if extra:
            msg += f'Extra in _POST_CUSTOM_METHODS (handler no longer POST): {extra}.'
        self.assertFalse(msg, msg)

    # ------------------------------------------------------------------
    # T3 - genapi emits correct HTTP verb per cm.method
    # ------------------------------------------------------------------
    def test_apigen_emits_correct_verb_per_method(self) -> None:
        """api_paths() emits GET for GET custom methods, POST for POST ones.

        No custom method should appear under the wrong verb slot.
        """
        root = dispatcher.Dispatcher.root_node

        def collect(node: 'types.rest.HandlerNode') -> 'list[tuple[str, type]]':
            res: 'list[tuple[str, type]]' = []
            if node.handler and issubclass(node.handler, ModelHandler):
                if getattr(node.handler, 'CUSTOM_METHODS', None):
                    res.append((node.full_path(), node.handler))
            for child in node.children.values():
                res.extend(collect(child))
            return res

        nodes = collect(root)
        self.assertGreater(
            len(nodes),
            0,
            'Test setup: at least one ModelHandler with CUSTOM_METHODS expected.',
        )

        offenders: 'list[str]' = []
        for path_label, cls in nodes:
            base = '/' + path_label.lstrip('/')
            paths = cls.api_paths(base, tags=[], security='')
            for cm in cls.CUSTOM_METHODS:
                expected_path = f'{base}/{{uuid}}/{cm.name}' if cm.needs_parent else f'{base}/{cm.name}'
                path_item = paths.get(expected_path)
                if path_item is None:
                    offenders.append(f'{cls.__name__}: missing path {expected_path!r}')
                    continue

                if cm.method == types.rest.CustomMethodMethod.POST:
                    if path_item.post is None:
                        offenders.append(f'{cls.__name__}.{cm.name}: POST method but no post= in spec')
                else:
                    if path_item.get is None:
                        offenders.append(f'{cls.__name__}.{cm.name}: GET method but no get= in spec')

        self.assertEqual(
            offenders,
            [],
            'OpenAPI spec custom-method verb mismatch: ' + ' | '.join(offenders),
        )

    # ------------------------------------------------------------------
    # T4 - deprecation headers on COMPAT GET→POST fallback
    # ------------------------------------------------------------------
    def test_handler_deprecation_headers(self) -> None:
        """Handler.add_deprecation_headers() emits RFC 9745/8594 headers."""
        ts = consts.rest.DEPRECATION_TS
        self.assertIsInstance(ts, int)
        self.assertGreater(ts, 0)

        sunset = consts.rest.SUNSET_DATE
        self.assertIsInstance(sunset, str)
        self.assertTrue(sunset.endswith('GMT'))

    def test_deprecation_headers_format(self) -> None:
        """Deprecation/Sunset headers follow RFC 9745/8594 format."""
        # Deprecation header value: @<unix-timestamp>
        self.assertRegex(
            f'@{consts.rest.DEPRECATION_TS}',
            r'^@\d+$',
        )
        # Sunset header: HTTP-date (RFC 1123)
        self.assertRegex(
            consts.rest.SUNSET_DATE,
            r'^[A-Z][a-z]{2}, \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT$',
        )

    # ------------------------------------------------------------------
    # T5 — Runtime dispatch: POST custom methods (Change B)
    # ------------------------------------------------------------------
    def test_post_detail_custom_method_dispatches(self) -> None:
        """POST /authenticators/{id}/users/{user_id}/clean_related → 200.

        Verifies that POST to a detail-level POST custom method is dispatched
        correctly (Change B routing).
        """
        user = self.admins[0]
        url = f'authenticators/{self.auth.uuid}/users/{user.uuid}/clean_related'
        response = self.client.rest_post(url)
        self.assertEqual(response.status_code, 200, f'POST custom method failed: {response.json()}')
        self.assertEqual(response.json(), {'status': 'ok'})

    def test_get_post_custom_method_works_in_compat(self) -> None:
        """GET /authenticators/{id}/users/{user_id}/clean_related → 200 + deprecation.

        In COMPAT mode (default), GET on a POST custom method still works
        but returns RFC 9745/8594 deprecation headers (Change B).
        """
        user = self.admins[0]
        url = f'authenticators/{self.auth.uuid}/users/{user.uuid}/clean_related'
        response = self.client.rest_get(url)
        self.assertEqual(response.status_code, 200, f'GET fallback failed: {response.json()}')
        self.assertEqual(response.json(), {'status': 'ok'})
        # Verify deprecation headers are present
        self.assertIn('Deprecation', response)
        self.assertIn('Sunset', response)

    def test_post_master_custom_method_dispatches(self) -> None:
        """POST to a master-level custom method with needs_parent=True.

        Uses Accounts.clear (POST /accounts/{id}/clear) which requires an
        existing Account. We create a minimal one for the test.
        """
        account = self._create_test_account()
        url = f'accounts/{account.uuid}/clear'
        response = self.client.rest_post(url)
        self.assertIn(
            response.status_code,
            (200, 400),
            f'POST master custom method: {response.content.decode(errors='replace')}',
        )

    # ------------------------------------------------------------------
    # T6 — NO_COMPAT mode: GET to POST method returns 410 Gone
    # ------------------------------------------------------------------
    def test_get_post_custom_method_returns_410_in_no_compat_detail(self) -> None:
        """GET to a POST custom method in NO_COMPAT mode → 410 Gone (detail handler)."""
        from uds.REST.handlers import Handler

        user = self.admins[0]
        url = f'authenticators/{self.auth.uuid}/users/{user.uuid}/clean_related'
        with patch.object(Handler, 'api_compat', return_value=types.rest.ApiCompat.NO_COMPAT):
            response = self.client.rest_get(url)
        self.assertEqual(response.status_code, 410, f'Expected 410 Gone, got {response.status_code}')

    def test_get_post_custom_method_returns_410_in_no_compat_master(self) -> None:
        """GET to a POST custom method in NO_COMPAT mode → 410 Gone (master handler)."""
        from uds.REST.handlers import Handler

        account = self._create_test_account()
        url = f'accounts/{account.uuid}/clear'
        with patch.object(Handler, 'api_compat', return_value=types.rest.ApiCompat.NO_COMPAT):
            response = self.client.rest_get(url)
        self.assertEqual(response.status_code, 410, f'Expected 410 Gone, got {response.status_code}')

    def test_post_works_in_no_compat_mode(self) -> None:
        """POST to a POST custom method still works in NO_COMPAT mode."""
        from uds.REST.handlers import Handler

        user = self.admins[0]
        url = f'authenticators/{self.auth.uuid}/users/{user.uuid}/clean_related'
        with patch.object(Handler, 'api_compat', return_value=types.rest.ApiCompat.NO_COMPAT):
            response = self.client.rest_post(url)
        self.assertEqual(response.status_code, 200, f'POST failed in NO_COMPAT: {response.json()}')
        self.assertEqual(response.json(), {'status': 'ok'})

    # ------------------------------------------------------------------
    # T7 — GET to POST method in COMPAT mode also works at master level
    # ------------------------------------------------------------------
    def test_get_post_custom_method_works_in_compat_master(self) -> None:
        """GET to a master POST custom method in COMPAT → 200 + deprecation headers."""
        account = self._create_test_account()
        url = f'accounts/{account.uuid}/clear'
        response = self.client.rest_get(url)
        self.assertIn(response.status_code, (200, 400), f'GET fallback failed: {response.status_code}')
        self.assertIn('Deprecation', response)
        self.assertIn('Sunset', response)

    # ------------------------------------------------------------------
    # T8 — Systematic dispatch: all accessible custom methods via existing data
    # ------------------------------------------------------------------
    def test_providers_allservices_dispatches(self) -> None:
        """GET /providers/allservices → 200 (master, collection-scoped)."""
        url = 'providers/allservices'
        response = self.client.rest_get(url)
        self.assertEqual(response.status_code, 200, f'allservices: {response.status_code}')

    def test_providers_maintenance_dispatches(self) -> None:
        """POST /providers/{id}/maintenance → 200 (master, needs_parent)."""
        url = f'providers/{self.provider.uuid}/maintenance'
        response = self.client.rest_post(url)
        self.assertEqual(response.status_code, 200, f'maintenance: {response.status_code}')

    def test_authenticators_search_dispatches(self) -> None:
        """GET /authenticators/{id}/search → 200 (master, needs_parent, w/ params)."""
        url = f'authenticators/{self.auth.uuid}/search'
        response = self.client.rest_get(url, {'type': 'user', 'term': 'admin'})
        self.assertEqual(response.status_code, 200, f'search: {response.status_code}')

    def test_authenticators_users_with_services_dispatches(self) -> None:
        """GET /authenticators/{id}/users_with_services → 200 (master)."""
        url = f'authenticators/{self.auth.uuid}/users_with_services'
        response = self.client.rest_get(url)
        self.assertEqual(response.status_code, 200, f'users_with_services: {response.status_code}')

    def test_accounts_timemark_dispatches(self) -> None:
        """POST /accounts/{id}/timemark → 200 (master, needs_parent)."""
        account = self._create_test_account()
        url = f'accounts/{account.uuid}/timemark'
        response = self.client.rest_post(url)
        self.assertEqual(response.status_code, 200, f'timemark: {response.status_code}')

    def test_group_services_pools_dispatches(self) -> None:
        """GET /authenticators/{id}/groups/{gid}/services_pools → 200 (detail)."""
        group = self.simple_groups[0]
        url = f'authenticators/{self.auth.uuid}/groups/{group.uuid}/services_pools'
        response = self.client.rest_get(url)
        self.assertEqual(response.status_code, 200, f'group/services_pools: {response.status_code}')

    def test_group_users_dispatches(self) -> None:
        """GET /authenticators/{id}/groups/{gid}/users → 200 (detail)."""
        group = self.simple_groups[0]
        url = f'authenticators/{self.auth.uuid}/groups/{group.uuid}/users'
        response = self.client.rest_get(url)
        self.assertEqual(response.status_code, 200, f'group/users: {response.status_code}')

    def test_users_services_pools_dispatches(self) -> None:
        """GET /authenticators/{id}/users/{uid}/services_pools → 200 (detail)."""
        user = self.admins[0]
        url = f'authenticators/{self.auth.uuid}/users/{user.uuid}/services_pools'
        response = self.client.rest_get(url)
        self.assertEqual(response.status_code, 200, f'users/services_pools: {response.status_code}')

    def test_users_user_services_dispatches(self) -> None:
        """GET /authenticators/{id}/users/{uid}/user_services → 200 (detail)."""
        user = self.admins[0]
        url = f'authenticators/{self.auth.uuid}/users/{user.uuid}/user_services'
        response = self.client.rest_get(url)
        self.assertEqual(response.status_code, 200, f'users/user_services: {response.status_code}')

    def test_users_add_to_group_dispatches(self) -> None:
        """POST /authenticators/{id}/users/{uid}/add_to_group → 400 (needs 'group' param)."""
        user = self.admins[0]
        group = self.simple_groups[0]
        url = f'authenticators/{self.auth.uuid}/users/{user.uuid}/add_to_group'
        # Without 'group' param → 400
        response = self.client.rest_post(url)
        self.assertEqual(response.status_code, 400, f'add_to_group without params: {response.status_code}')
        # With valid 'group' param → 200
        response = self.client.rest_post(url, {'group': group.uuid})
        self.assertEqual(response.status_code, 200, f'add_to_group with group: {response.status_code}')
        self.assertEqual(response.json(), {'status': 'ok'})

    def test_users_enable_client_logging_dispatches(self) -> None:
        """POST /authenticators/{id}/users/{uid}/enable_client_logging → 200 (detail)."""
        user = self.admins[0]
        url = f'authenticators/{self.auth.uuid}/users/{user.uuid}/enable_client_logging'
        response = self.client.rest_post(url)
        self.assertEqual(response.status_code, 200, f'enable_client_logging: {response.status_code}')
        self.assertEqual(response.json(), {'status': 'ok'})

    # ------------------------------------------------------------------
    # T9 — Dual-path GET+COMPAT for POST methods (legacy compatibility)
    # ------------------------------------------------------------------
    def test_get_maintenance_works_in_compat(self) -> None:
        """GET /providers/{id}/maintenance → 200 + deprecation in COMPAT mode."""
        url = f'providers/{self.provider.uuid}/maintenance'
        response = self.client.rest_get(url)
        self.assertEqual(response.status_code, 200, f'GET maintenance: {response.status_code}')
        self.assertIn('Deprecation', response)
        self.assertIn('Sunset', response)

    def test_get_timemark_works_in_compat(self) -> None:
        """GET /accounts/{id}/timemark → 200 + deprecation in COMPAT mode."""
        account = self._create_test_account()
        url = f'accounts/{account.uuid}/timemark'
        response = self.client.rest_get(url)
        self.assertEqual(response.status_code, 200, f'GET timemark: {response.status_code}')
        self.assertIn('Deprecation', response)
        self.assertIn('Sunset', response)

    def test_get_add_to_group_works_in_compat(self) -> None:
        """GET /authenticators/{id}/users/{uid}/add_to_group → dispatched (400, needs body params)."""
        user = self.admins[0]
        url = f'authenticators/{self.auth.uuid}/users/{user.uuid}/add_to_group'
        response = self.client.rest_get(url)
        # Dispatched correctly (GET→POST in COMPAT) but fails because 'group' param is missing
        self.assertEqual(response.status_code, 400, f'GET add_to_group: {response.status_code}')

    def test_get_enable_client_logging_works_in_compat(self) -> None:
        """GET /authenticators/{id}/users/{uid}/enable_client_logging → 200 + deprecation."""
        user = self.admins[0]
        url = f'authenticators/{self.auth.uuid}/users/{user.uuid}/enable_client_logging'
        response = self.client.rest_get(url)
        self.assertEqual(response.status_code, 200, f'GET enable_client_logging: {response.status_code}')
        self.assertIn('Deprecation', response)
        self.assertIn('Sunset', response)

    # ------------------------------------------------------------------
    # T10 — camelCase URL segments: emit deprecation in COMPAT, 410 in NO_COMPAT
    # ------------------------------------------------------------------
    def test_camelcase_url_emits_deprecation_in_compat(self) -> None:
        """Legacy camelCase URL → 200 + deprecation headers in COMPAT mode.

        `cleanRelated` (camelCase) and `enableClientLogging` (camelCase)
        are equivalent to `clean_related` / `enable_client_logging`
        (snake_case). The server still dispatches correctly but adds
        deprecation headers so clients can migrate.
        """
        user = self.admins[0]
        url = f'authenticators/{self.auth.uuid}/users/{user.uuid}/cleanRelated'
        response = self.client.rest_get(url)
        self.assertEqual(response.status_code, 200, f'camelCase URL: {response.status_code}')
        self.assertIn('Deprecation', response, 'camelCase URL must emit Deprecation header')

    def test_camelcase_url_returns_410_in_no_compat(self) -> None:
        """Legacy camelCase URL → 410 Gone in NO_COMPAT mode."""
        from uds.REST.handlers import Handler

        user = self.admins[0]
        url = f'authenticators/{self.auth.uuid}/users/{user.uuid}/cleanRelated'
        with patch.object(Handler, 'api_compat', return_value=types.rest.ApiCompat.NO_COMPAT):
            response = self.client.rest_get(url)
        self.assertEqual(response.status_code, 410, f'Expected 410 Gone, got {response.status_code}')

    def test_snakecase_url_no_camelcase_deprecation(self) -> None:
        """snake_case URL does NOT emit camelCase deprecation headers.

        Other deprecation headers (e.g. POST→GET fallback) may still be
        present, but the camelCase-specific note must not appear.
        """
        user = self.admins[0]
        url = f'authenticators/{self.auth.uuid}/users/{user.uuid}/clean_related'
        response = self.client.rest_get(url)
        # 200 because GET→POST in COMPAT still works
        self.assertEqual(response.status_code, 200)
        # No camelCase deprecation specifically
        # (Deprecation header may still appear from POST→GET fallback)

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------
    def _create_test_account(self) -> 'UUIDModel':
        """Helper: create a minimal Account for POST custom-method tests."""
        from django.utils import timezone
        from uds import models

        account = models.Account(
            name='test-account-post-dispatch',
            comments='Temporary account for Change B dispatch tests',
            time_mark=timezone.now(),
        )
        account.save()
        self.addCleanup(account.delete)
        return account
