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
(Phase 1 — Safety net extension).

Covers doc/plan/rest-test-needed.md Phase 1 step 6 (Pytest contract pin)
and the prereqs for Phase 4 (GET-modifier → POST migration).

What we freeze today (Phase 1):

- Every ModelCustomMethod declared by a handler has ``method == 'GET'``
  or was constructed with the default — no call site overrides it yet.
- The dispatcher accepts a handler with an empty CUSTOM_METHODS list and
  treats it the same as a ModelHandler.get() that does not match a
  custom method (no AttributeError surprise).
- genapi's ``api_paths()`` exposes every ModelHandler custom method as
  a GET operation (this is the current behaviour, even for state-mutating
  modifiers). Phase 4 will:
    (a) teach the dispatcher to route by ``cm.method``,
    (b) extend PathItem to carry non-GET verbs, and
    (c) have genapi emit the correct Operation for each method.
  Until then, all custom methods are GET-shaped in the spec.

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
    # T2 - audit handlers currently exist with default methods
    # ------------------------------------------------------------------
    def test_no_modelhandler_uses_non_default_method_yet(self) -> None:
        """Every ModelCustomMethod declared in the codebase still uses GET.

        This is a Phase 1 constraint: the dispatcher does not yet route
        by ``cm.method`` (Phase 4 work). When we start wiring modifiers to
        POST we will delete or update this test.

        DetailHandlers are checked too: their CUSTOM_METHODS list has
        the same shape but DetailHandlers do NOT generate OpenAPI entries
        for their customs today (see Phase 1 docstring). They are listed
        here anyway because they participate in the same routing path
        (the ``get()``-side custom-method match).
        """
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
        for cls_name, cls, cms in sources:
            for cm in cms:
                if cm.method != types.rest.CustomMethodMethod.GET:
                    offenders.append(f'{cls_name}.{cm.name} -> {cm.method!r}')

        self.assertEqual(
            offenders,
            [],
            'No ModelHandler/DetailHandler should declare a non-default method yet; '
            'Phase 4 will introduce POST/PUT/QUERY modifiers: ' + ', '.join(offenders),
        )

    # ------------------------------------------------------------------
    # T3 - genapi exposes every ModelHandler custom method as GET
    # ------------------------------------------------------------------
    def test_apigen_emits_custom_methods_as_get(self) -> None:
        """api_paths() still emits each custom method under ``get=`` (Phase 1).

        Phase 4 will extend PathItem / Operation so a cm.method=POST
        modifier is emitted under ``post=``. Until then, every custom
        method appears under ``get=`` in the OpenAPI spec.
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
            # Build paths the same way api_helpers / genapi does: pass the
            # base path WITH a leading slash to ``api_paths``; the method
            # then prepends its own segments to derive the spec keys.
            base = '/' + path_label.lstrip('/')
            paths = cls.api_paths(base, tags=[], security='')
            for cm in cls.CUSTOM_METHODS:
                # Reconstruct what api_helpers.py:154 builds under the hood.
                expected_path = (
                    f'{base}/{{uuid}}/{cm.name}'
                    if cm.needs_parent
                    else f'{base}/{cm.name}'
                )
                path_item = paths.get(expected_path)
                if path_item is None:
                    offenders.append(f'{cls.__name__}: missing path {expected_path!r}')
                    continue
                # Phase 1 invariant: every custom-method path has a GET operation.
                if path_item.get is None:
                    offenders.append(
                        f'{cls.__name__}.{cm.name}: path {expected_path!r} has no GET operation '
                        '(Phase 4 will change this when POST/PUT/QUERY is introduced).'
                    )
                # Defensive: future Phase 4 may add post/put/query/...; ensure we
                # don't accidentally regress (no other verb sneaks in until Phase 4).
                for verb in ('post', 'put', 'delete', 'query'):
                    if getattr(path_item, verb, None) is not None:
                        offenders.append(
                            f'{cls.__name__}.{cm.name}: unexpected {verb.upper()} '
                            f'on path {expected_path!r}; Phase 4 should introduce it explicitly.'
                        )

        self.assertEqual(
            offenders,
            [],
            'OpenAPI spec custom-method operations diverged from the Phase 1 contract: '
            + ' | '.join(offenders),
        )
