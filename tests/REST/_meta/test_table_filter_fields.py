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
Every admin table must declare the fields its filter box may query.

The admin builds the ``$filter`` expression from ``TableInfo.filter_fields``
and, when a table declares none, falls back to every displayed column. Computed
and relation columns are not database fields, so that fallback raises
``FieldError``, the list endpoint answers ``400`` and the table renders empty.

Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""

import collections.abc
import importlib
import typing

import pytest
from django.core.exceptions import FieldError

from uds.core.types.rest import TableInfo
from uds.core.util.query_db_filter import exec_query
from uds.REST.model.master import ModelHandler

from tests.utils import rest

importlib.import_module('uds.REST.dispatcher')

_MasterHandler: typing.TypeAlias = type[ModelHandler[typing.Any]]


def _descendants(cls: _MasterHandler) -> collections.abc.Iterator[_MasterHandler]:
    for subclass in cls.__subclasses__():
        yield subclass
        yield from _descendants(subclass)


def _listable_tables() -> list[tuple[str, _MasterHandler, TableInfo]]:
    tables: list[tuple[str, _MasterHandler, TableInfo]] = []
    for handler in _descendants(ModelHandler):
        table = getattr(handler, 'TABLE', None)
        if not isinstance(table, TableInfo) or not table.fields or getattr(handler, 'MODEL', None) is None:
            continue
        tables.append((f'{handler.__module__}.{handler.__qualname__}', handler, table))
    return tables


_TABLES = _listable_tables()


def test_tables_are_registered() -> None:
    assert _TABLES, 'no model handler table found, registration is broken'


@pytest.mark.parametrize(('name', 'handler', 'table'), _TABLES, ids=[name for name, _, _ in _TABLES])
def test_table_declares_filter_fields(name: str, handler: _MasterHandler, table: TableInfo) -> None:
    assert table.filter_fields, f'{name} does not declare filter fields'


@pytest.mark.parametrize(('name', 'handler', 'table'), _TABLES, ids=[name for name, _, _ in _TABLES])
def test_declared_filter_fields_are_queryable(
    name: str, handler: _MasterHandler, table: TableInfo
) -> None:
    for field in table.filter_fields:
        try:
            exec_query(f"contains({field}, 'x')", handler.MODEL.objects.all())
        except FieldError as e:
            pytest.fail(f'{name} declares a non queryable filter field {field!r}: {e}')


class NetworksFilterTest(rest.test.RESTTestCase):
    """The admin path end to end: read the table info, filter with what it declares."""

    BASE: typing.ClassVar[str] = 'networks'

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    def test_list_answers_the_filter_built_from_table_info(self) -> None:
        self.client.rest_post(
            self.BASE, {'name': 'filtered-network', 'net_string': '192.168.200.0/24', 'tags': []}
        )
        self.client.rest_post(
            self.BASE, {'name': 'other-network', 'net_string': '192.168.201.0/24', 'tags': []}
        )

        declared = self.client.rest_get(f'{self.BASE}/tableinfo').json()['filter_fields']
        self.assertTrue(declared)
        query = ' or '.join(f"contains({field}, 'filtered-network')" for field in declared)

        response = self.client.rest_get(self.BASE, {'$filter': query})
        self.assertEqual(response.status_code, 200, response.content.decode(errors='replace'))
        self.assertEqual([item['name'] for item in response.json()], ['filtered-network'])
