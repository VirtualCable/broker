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
Tests for OpenAPI emission of deprecated camelCase aliases for snake_case
custom methods.

Every custom method whose declared name contains ``_`` (snake_case) must
produce, in the OpenAPI spec emitted by ``api_paths()``:

  * the canonical snake_case path -- NOT deprecated
  * a camelCase alias path -- marked ``deprecated: True``

``DetailHandler`` emits two operations per custom method: collection-level
(no uuid) and item-level (``{uuid}/``); both levels must follow the rule.
``ModelHandler`` emits a single item-level operation per custom method.
"""
# pyright: reportUnknownArgumentType=false, reportUnknownVariableType=false

import typing

import pytest

from uds.core import types
from uds.REST.model.base import BaseModelHandler
from uds.REST.model.detail import DetailHandler
from uds.REST.model.master import ModelHandler
from uds.REST.utils import camel_and_snake_case_from


SECURITY_NAME: typing.Final[str] = 'udsApiAuth'


def _handlers_with_snake_case() -> list[type[BaseModelHandler[typing.Any]]]:
    out: list[type[BaseModelHandler[typing.Any]]] = []
    for cls in list(ModelHandler.__subclasses__()) + list(DetailHandler.__subclasses__()):  
        cms = getattr(cls, 'CUSTOM_METHODS', None)
        if cms and any('_' in cm.name for cm in cms):
            out.append(cls)
    return out


def _op_for(
    item: types.rest.api.PathItem,
    method: types.rest.CustomMethodMethod,
) -> types.rest.api.Operation | None:
    return item.post if method is types.rest.CustomMethodMethod.POST else item.get


def _check_pair(
    paths: dict[str, types.rest.api.PathItem],
    *,
    canonical: str,
    alias: str,
    method: types.rest.CustomMethodMethod,
    handler_name: str,
) -> None:
    assert canonical in paths, f'missing canonical path {canonical!r} in {handler_name}'
    assert alias in paths, f'missing camelCase alias path {alias!r} in {handler_name}'

    canonical_op = _op_for(paths[canonical], method)
    assert canonical_op is not None, f'canonical {canonical!r} has no operation'
    assert (
        getattr(canonical_op, 'deprecated', False) is False
    ), f'canonical {canonical!r} should not be deprecated'

    alias_op = _op_for(paths[alias], method)
    assert alias_op is not None, f'alias {alias!r} has no operation'
    assert alias_op.deprecated is True, f'alias {alias!r} should be deprecated'


@pytest.mark.parametrize(
    'handler_cls',
    _handlers_with_snake_case(),
    ids=lambda c: c.__name__,
)
def test_snake_case_custom_methods_emit_deprecated_camelcase_aliases(
    handler_cls: type[ModelHandler[typing.Any]] | type[DetailHandler[typing.Any]],
) -> None:
    snake_methods = [cm for cm in handler_cls.CUSTOM_METHODS if '_' in cm.name]
    assert snake_methods, f'{handler_cls.__name__} has no snake_case methods to test'

    path = handler_cls.__name__.lower()
    paths = handler_cls.api_paths(path, [handler_cls.__name__], SECURITY_NAME)

    is_detail = issubclass(handler_cls, DetailHandler)

    for cm in snake_methods:
        camel_name, _ = camel_and_snake_case_from(cm.name)
        if is_detail:
            # Collection level (no uuid)
            _check_pair(
                paths,
                canonical=f'{path}/{cm.name}',
                alias=f'{path}/{camel_name}',
                method=cm.method,
                handler_name=handler_cls.__name__,
            )
        # Item level (with {uuid})
        _check_pair(
            paths,
            canonical=f'{path}/{{uuid}}/{cm.name}',
            alias=f'{path}/{{uuid}}/{camel_name}',
            method=cm.method,
            handler_name=handler_cls.__name__,
        )


@pytest.mark.parametrize(
    'handler_cls',
    _handlers_with_snake_case(),
    ids=lambda c: c.__name__,
)
def test_single_word_custom_methods_have_no_alias(
    handler_cls: type[ModelHandler[typing.Any]] | type[DetailHandler[typing.Any]],
) -> None:
    """Methods whose name has no '_' must not produce a duplicate alias entry."""
    single = [cm for cm in handler_cls.CUSTOM_METHODS if '_' not in cm.name]
    if not single:
        pytest.skip(f'{handler_cls.__name__} has no single-word methods')

    path = handler_cls.__name__.lower()
    paths = handler_cls.api_paths(path, [handler_cls.__name__], SECURITY_NAME)
    is_detail = issubclass(handler_cls, DetailHandler)
    for cm in single:
        suffixes: list[str] = [f'{path}/{{uuid}}/{cm.name}']
        if is_detail:
            suffixes.append(f'{path}/{cm.name}')
        for suffix in suffixes:
            # Each emitted level must appear at most once. CamelCase of a
            # single word equals the original, so a duplicate would mean
            # a stray alias slipped in.
            matches = [p for p in paths if p == suffix]
            assert len(matches) <= 1, (
                f'{handler_cls.__name__}: single-word method {cm.name!r} '
                f'produced {len(matches)} entries for {suffix!r} (expected <=1)'
            )
