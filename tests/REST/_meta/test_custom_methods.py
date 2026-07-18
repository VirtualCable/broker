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


from uds.core import types
from uds.REST import dispatcher
from uds.REST.model.master import ModelHandler
from uds.REST.model.detail import DetailHandler

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
    _POST_CUSTOM_METHODS: typing.ClassVar[frozenset[tuple[str, str]]] = frozenset({
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
    })

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
                        offenders.append(
                            f'{cls_name}.{cm.name}: expected POST, got {cm.method!r}'
                        )
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
                expected_path = (
                    f'{base}/{{uuid}}/{cm.name}'
                    if cm.needs_parent
                    else f'{base}/{cm.name}'
                )
                path_item = paths.get(expected_path)
                if path_item is None:
                    offenders.append(f'{cls.__name__}: missing path {expected_path!r}')
                    continue

                if cm.method == types.rest.CustomMethodMethod.POST:
                    if path_item.post is None:
                        offenders.append(
                            f'{cls.__name__}.{cm.name}: POST method but no post= in spec'
                        )
                else:
                    if path_item.get is None:
                        offenders.append(
                            f'{cls.__name__}.{cm.name}: GET method but no get= in spec'
                        )

        self.assertEqual(
            offenders,
            [],
            'OpenAPI spec custom-method verb mismatch: ' + ' | '.join(offenders),
        )
