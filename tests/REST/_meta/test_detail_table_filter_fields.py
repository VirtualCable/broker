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
The tables of the detail tabs must declare their filter fields too.

Same contract as ``test_table_filter_fields`` for the master tables: the admin
builds the ``$filter`` expression from ``TableInfo.filter_fields`` and falls
back to every displayed column when a table declares none, which raises
``FieldError`` on computed and relation columns and leaves the tab empty.

Detail handlers build their table from the parent, so a mock parent stands in,
and they expose no model, so the queryset model of each tab is listed here. The
tabs that merge items in memory declare item fields instead of columns, so their
item class is listed instead.

Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""

import collections.abc
import dataclasses
import importlib
import typing
from unittest import mock

import pytest
from django.core.exceptions import FieldError
from django.db.models import Model

from uds import models
from uds.core.types.rest import TableInfo
from uds.core.util.query_db_filter import exec_query
from uds.REST.methods.user_services import UserServiceItem
from uds.REST.model.detail import DetailHandler
from uds.REST.model.master import ModelHandler

importlib.import_module('uds.REST.dispatcher')

_MasterHandler: typing.TypeAlias = type[ModelHandler[typing.Any]]

# Queryset model behind each detail tab, keyed by "<master handler>/<tab>".
DETAIL_MODELS: typing.Final[dict[str, type[Model]]] = {
    'Accounts/usage': models.AccountUsage,
    'Authenticators/users': models.User,
    'Authenticators/groups': models.Group,
    'Calendars/rules': models.CalendarRule,
    'FlowsApproval/actions': models.FlowAction,
    'FlowsArchive/actions': models.FlowAction,
    'FlowsOwn/actions': models.FlowAction,
    'MetaPools/access': models.CalendarAccess,
    'MetaPools/groups': models.Group,
    'MetaPools/pools': models.MetaPoolMember,
    'Providers/services': models.Service,
    'Providers/usage': models.UserService,
    'ServersGroups/servers': models.Server,
    'ServicesPools/access': models.CalendarAccess,
    'ServicesPools/actions': models.CalendarAction,
    'ServicesPools/cache': models.UserService,
    'ServicesPools/changelog': models.ServicePoolPublicationChangelog,
    'ServicesPools/groups': models.Group,
    'ServicesPools/publications': models.ServicePoolPublication,
    'ServicesPools/services': models.UserService,
    'ServicesPools/transports': models.Transport,
    'Tunnels/servers': models.Server,
}


# Item class behind each detail tab that filters in memory instead of querying.
IN_MEMORY_DETAILS: typing.Final[dict[str, type[typing.Any]]] = {
    'MetaPools/services': UserServiceItem,
}


def _descendants(cls: _MasterHandler) -> collections.abc.Iterator[_MasterHandler]:
    for subclass in cls.__subclasses__():
        yield subclass
        yield from _descendants(subclass)


def _detail_table(detail: type[DetailHandler[typing.Any]], master: _MasterHandler) -> TableInfo:
    # Detail handlers only need the parent to build their table, and building one
    # requires a request, so an uninitialized handler with a mock parent is enough.
    handler = detail.__new__(detail)
    return handler.get_table(mock.MagicMock(spec=master.MODEL))


def _detail_tables() -> list[tuple[str, TableInfo]]:
    tables: list[tuple[str, TableInfo]] = []
    for master in _descendants(ModelHandler):
        if not master.DETAIL or getattr(master, 'MODEL', None) is None:
            continue
        for tab, detail in master.DETAIL.items():
            tables.append((f'{master.__qualname__}/{tab}', _detail_table(detail, master)))
    return sorted(tables)


_TABLES = _detail_tables()


def test_detail_tables_are_registered() -> None:
    assert _TABLES, 'no detail handler table found, registration is broken'


def test_every_detail_table_has_a_known_model() -> None:
    assert sorted(DETAIL_MODELS | IN_MEMORY_DETAILS) == [name for name, _ in _TABLES]


@pytest.mark.parametrize(('name', 'table'), _TABLES, ids=[name for name, _ in _TABLES])
def test_detail_table_declares_filter_fields(name: str, table: TableInfo) -> None:
    assert table.filter_fields, f'{name} does not declare filter fields'


@pytest.mark.parametrize(('name', 'table'), _TABLES, ids=[name for name, _ in _TABLES])
def test_declared_detail_filter_fields_are_queryable(name: str, table: TableInfo) -> None:
    if item_class := IN_MEMORY_DETAILS.get(name):
        item_fields = {field.name for field in dataclasses.fields(item_class)}
        assert set(table.filter_fields) <= item_fields, f'{name} declares unknown item fields'
        return

    model = DETAIL_MODELS[name]
    for field in table.filter_fields:
        try:
            exec_query(f"contains({field}, 'x')", model.objects.all())
        except FieldError as e:
            pytest.fail(f'{name} declares a non queryable filter field {field!r}: {e}')
