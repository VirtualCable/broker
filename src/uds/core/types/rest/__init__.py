# -*- coding: utf-8 -*-
#
# Copyright (c) 2023 Virtual Cable S.L.
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
Author: Adolfo Gómez, dkmaster at dkmon dot com
"""
# pyright: reportUnusedImport=false

import abc
import enum
import typing
import hashlib
import dataclasses
import collections.abc

from . import stock
from . import actor
from . import api

if typing.TYPE_CHECKING:
    from uds.REST.handlers import Handler
    from uds.core.module import Module
    from uds.models.managed_object_model import ManagedObjectModel


T_Model = typing.TypeVar('T_Model', bound='ManagedObjectModel')
T_Item = typing.TypeVar("T_Item", bound='BaseRestItem')


class NotRequired:
    """
    This is a marker class to indicate that a field is not required.
    It is used to indicate that a field is optional in the REST API.
    """

    def __bool__(self) -> bool:
        return False

    def __str__(self) -> str:
        return 'NotRequired'

    # Field generator for dataclasses
    @staticmethod
    def field() -> typing.Any:
        """
        Returns a field that is not required.
        This is used to indicate that a field is optional in the REST API.
        """
        return dataclasses.field(default_factory=NotRequired, repr=False, compare=False)


# HTTP verb a custom method can declare. Modeled as an enum (rather than
# a typing.Literal) so editors can autocomplete, type-checkers can validate
# exhaustively, and the value compares to plain strings via standard
# comparison thanks to ``str`` mixin. Membership expansion (PATCH, HEAD, ...)
# goes here as new enum members when needed.
class CustomMethodMethod(str, enum.Enum):
    """
    HTTP verb to invoke a custom action with.

    The default for unmodified call sites is ``GET``, preserving 100%
    backward compatibility. Phase 4 of
    doc/plan/rest-standards-compliance.md will start using
    ``CustomMethodMethod.POST`` for state-mutating modifiers (publish,
    cancel, reset, maintenance, ...) so they can be deprecated from GET
    without breakage. ``CustomMethodMethod.QUERY`` is reserved for the
    RFC 10008 safe-with-body verb (Phase 2/3 of the plan).
    """

    GET = 'GET'
    POST = 'POST'
    PUT = 'PUT'
    QUERY = 'QUERY'


class ApiCompat(str, enum.Enum):
    """API compatibility mode for the REST layer.

    Controls whether legacy (non-standard) endpoints and behaviours are
    active.  The value is exposed by ``Handler.api_compat()``.

    Members
    -------
    COMPAT:
        Legacy endpoints active.  GET modifiers, PUT-create, and other
        non-standard paths still work; deprecation headers are emitted.
        This is the **default in v5 and v6**.
    NO_COMPAT:
        Standard API only.  Legacy paths return ``410 Gone`` with a
        ``Sunset`` header pointing to the successor endpoint.
        This becomes the only mode in **v7**, when the enum and all
        COMPAT-guarded code are removed.
    """

    COMPAT = 'COMPAT'
    NO_COMPAT = 'NO_COMPAT'


@dataclasses.dataclass
class ModelCustomMethod:
    """
    Declares a custom method exposed by a ``ModelHandler`` or
    ``DetailHandler`` in addition to the standard CRUD verbs.

    - ``name``: the URL segment that selects this method (last path component).
    - ``needs_parent``: ``True`` when the method operates on a specific
      parent instance and the URL therefore carries the parent's id
      (``/collection/{uuid}/{name}``). ``False`` (default) when the
      method is collection-scoped (``/collection/{name}``).
      Note: only meaningful for ``ModelHandler``; ``DetailHandler``
      custom methods always derive their parent from the URL
      automatically.
    - ``method``: HTTP verb the dispatcher should use to invoke this
      action. See :class:`CustomMethodMethod`. The default ``GET``
      preserves 100% backward compatibility today; Phase 4 of
      doc/plan/rest-standards-compliance.md will migrate state-mutating
      modifiers to ``POST`` and reserve ``QUERY`` for the RFC 10008
      safe-with-body verb.
    - ``description``: human-readable summary for the OpenAPI ``summary``
      field.  When ``None`` (default) the generic text
      ``"Custom method <name> for <resource>"`` is used instead.
    - ``params``: an optional :class:`api.SchemaProperty` describing the
      parameters this custom method accepts.  For ``POST`` methods the
      schema is emitted as a JSON ``requestBody``; for ``GET`` methods
      it is emitted as query ``parameters``.  ``None`` (default) means
      the method takes no parameters (or they are intentionally
      undocumented).
    """

    name: str
    needs_parent: bool = False
    method: CustomMethodMethod = CustomMethodMethod.GET
    description: str | None = None
    params: api.SchemaProperty | None = None


# Note that for this item to work with documentation
# no forward references can be used (that is, do not use quotes around the inner field types)
@dataclasses.dataclass
class BaseRestItem:

    def as_dict(self) -> dict[str, typing.Any]:
        """
        Returns a dictionary representation of the item.
        By default, it returns the dataclass fields as a dictionary.
        """
        return dataclasses.asdict(self)

        # NOTE: the json processor should take care of converting "sub-items" to valid dictionaries
        #       (as it already does)

    def inmutables(self, *fields: str) -> str:
        return ''.join(str(getattr(self, f, '')) for f in fields)

    @typing.final
    def etag(self, *fields: str) -> str:
        return hashlib.sha256(self.inmutables(*fields).encode('utf-8')).hexdigest()

    @classmethod
    def api_components(cls: type[typing.Self]) -> api.Components:
        from uds.core.util import api as api_util  # Avoid circular import

        return api_util.api_components(cls)


@dataclasses.dataclass
class ManagedObjectItem(BaseRestItem, typing.Generic[T_Model]):
    """
    Represents a managed object type, with its name and type.
    This is used to represent the type of a managed object in the REST API.
    """

    item: T_Model

    @typing.override
    def as_dict(self) -> dict[str, typing.Any]:
        """
        Returns a dictionary representation of the managed object item.
        """
        # Note: This should not be necessary, but on some python versions, dataclasses.asdict
        #       seems to recurse infinitely on generic types, or do weird things with them.
        #       So we avoid it by temporarily removing the item.
        tmp_item = self.item
        self.item = typing.cast(T_Model, None)  # Avoid recursion on data
        base = super().as_dict()
        self.item = tmp_item  # Restore

        # Remove the fields that are not needed in the dictionary
        base.pop('item')
        item = self.item.get_instance()
        # item.init_gui()  # Defaults & stuff
        fields = item.get_fields_as_dict()

        # TODO: This will be removed in future versions, as it will be overseed by "instance" key
        base.update(fields)  # Add fields to dict
        base.update(
            {
                'type': item.mod_type(),  # Add type
                'type_name': item.mod_name(),  # Add type name
                'instance': fields,  # Future implementation will insert instance fields into "instance" key
            }
        )

        return base

    @classmethod
    @typing.override
    def api_components(cls: type[typing.Self]) -> api.Components:
        component = super().api_components()
        # Add any additional components specific to this item, that are "type", "type_name" and "instance"
        # get reference
        schema = component.schemas.get(cls.__name__)
        if isinstance(schema, api.Schema):
            assert schema is not None, f'Schema for {cls.__name__} not found in components'
            # item is not an real field, remove it from components description and required
            schema.properties.pop('item', None)
            schema.required.remove('item')

            # Add the specific fields to the schema
            # Note that 'instance' is incomplete, must be completed with item fields
            # But as long as python has not "real" generics, we cannot estimate the type of item
            schema.properties.update(
                {
                    'type': api.SchemaProperty(type='string'),
                    'type_name': api.SchemaProperty(type='string'),
                    'instance': api.SchemaProperty(type='object'),
                }
            )
            schema.required.extend(['type', 'instance'])  # type_name is not required

        return component


# Alias for get_items return type
ItemsResult: typing.TypeAlias = list[T_Item] | collections.abc.Iterator[T_Item]


@dataclasses.dataclass
class LogEntry(BaseRestItem):
    date: str = dataclasses.field(metadata={'description': 'Date of the log entry'})
    level: int = dataclasses.field(metadata={'description': 'Level of the log entry'})
    source: str = dataclasses.field(metadata={'description': 'Source of the log entry'})
    message: str = dataclasses.field(metadata={'description': 'Message of the log entry'})


@dataclasses.dataclass
class TypeInfo:
    name: str = dataclasses.field(metadata={'description': 'Name of the type (Human readable)'})
    type: str = dataclasses.field(metadata={'description': 'Type name used to identify the type'})
    description: str = dataclasses.field(metadata={'description': 'Description for this type'})
    icon: str = dataclasses.field(metadata={'description': 'Icon of the type, in base64'})

    group: str | None = dataclasses.field(
        default=None, metadata={'description': 'Group name used for grouping "similar" types'}
    )

    extra: 'ExtraTypeInfo|None' = dataclasses.field(
        default=None, metadata={'description': 'Extra type info. Depends on specific type.'}
    )

    def as_dict(self) -> dict[str, typing.Any]:
        res: dict[str, typing.Any] = {
            'name': self.name,
            'type': self.type,
            'description': self.description,
            'icon': self.icon,
        }
        # Add optional fields
        if self.group:
            res['group'] = self.group

        if self.extra:
            res.update(self.extra.as_dict())

        return res

    @staticmethod
    def null() -> 'TypeInfo':
        return TypeInfo(name='', type='', description='', icon='', extra=None)


class ExtraTypeInfo(abc.ABC):
    def as_dict(self) -> dict[str, typing.Any]:
        return {}


class TableFieldType(enum.StrEnum):
    """
    Enum for table field types.
    This is used to define the type of a field in a table.
    """

    NUMERIC = 'numeric'
    ALPHANUMERIC = 'alphanumeric'
    BOOLEAN = 'boolean'
    DATETIME = 'datetime'
    DATETIMESEC = 'datetimesec'
    DATE = 'date'
    TIME = 'time'
    ICON = 'icon'
    DICTIONARY = 'dictionary'
    IMAGE = 'image'


@dataclasses.dataclass
class TableField:
    """
    Represents a field in a table, with its title and type.
    This is used to describe the fields of a table in the REST API.
    """

    name: str  # Name of the field, used as key in the table

    title: str  # Title of the field
    type: TableFieldType = TableFieldType.ALPHANUMERIC  # Type of the field, defaults to alphanumeric
    visible: bool = True
    width: str | None = None  # Width of the field, if applicable
    dct: dict[typing.Any, typing.Any] | None = None  # Dictionary for dictionary fields, if applicable

    def as_dict(self) -> dict[str, typing.Any]:
        # Only return the fields that are set

        res: dict[str | int, typing.Any] = {
            'title': self.title,
            'type': self.type.value,
            'visible': self.visible,
        }
        if self.dct:
            res['dict'] = self.dct
        if self.width:
            res['width'] = self.width
        return {self.name: res}  # Return as a dictionary with the field name as key


@dataclasses.dataclass
class RowStyleInfo:
    prefix: str
    field: str

    def as_dict(self) -> dict[str, typing.Any]:
        """Returns a dict with all fields that are not None"""
        return dataclasses.asdict(self)

    @staticmethod
    def null() -> 'RowStyleInfo':
        return RowStyleInfo('', '')


@dataclasses.dataclass
class TableInfo:
    """
    Represents the table info for a REST API endpoint.
    This is used to describe the table fields and row style.
    """

    title: str
    fields: list[TableField]  # List of fields in the table
    row_style: 'RowStyleInfo'
    subtitle: str | None = None
    filter_fields: list[str] = dataclasses.field(default_factory=list[str])
    field_mappings: dict[str, str] = dataclasses.field(default_factory=dict[str, str])

    def as_dict(self) -> dict[str, typing.Any]:
        return {
            'title': self.title,
            'fields': [field.as_dict() for field in self.fields],
            'row_style': self.row_style.as_dict(),
            'subtitle': self.subtitle or '',
            'filter_fields': self.filter_fields,
            'field_mappings': self.field_mappings,
        }

    @staticmethod
    def null() -> 'TableInfo':
        """
        Returns a null TableInfo instance, with no fields and an empty title.
        """
        return TableInfo(title='', fields=[], row_style=RowStyleInfo.null(), subtitle=None)


@dataclasses.dataclass(frozen=True)
class HandlerNode:
    """
    Represents a node on the handler tree for rest services
    """

    name: str
    handler: type['Handler'] | None  # Handler for this node, if any
    parent: 'HandlerNode | None'  # Parent node, if any
    children: dict[str, 'HandlerNode']

    def __str__(self) -> str:
        return f'HandlerNode({self.name}, {self.handler}, {self.children})'

    def __repr__(self) -> str:
        return str(self)

    # Visit all nodes recursively, invoking a callback for each node with the node and path
    def visit(
        self,
        callback: collections.abc.Callable[
            ['HandlerNode', str, typing.Literal['handler', 'custom_method', 'detail_method'], int], None
        ],
        path: str = '',
        level: int = 0,
    ) -> None:
        from uds.REST.model import ModelHandler

        if self.handler:
            callback(self, path, 'handler', level)

            if issubclass(self.handler, ModelHandler):
                handler = typing.cast(
                    type[ModelHandler[typing.Any]], self.handler  # pyright: ignore[reportUnknownMemberType]
                )
                for method in handler.CUSTOM_METHODS:
                    callback(self, f'{path}/{method.name}' if path else method.name, 'custom_method', level + 1)
                for detail_name in handler.DETAIL.keys() if handler.DETAIL else typing.cast(list[str], []):
                    callback(self, f'{path}/{detail_name}' if path else detail_name, 'detail_method', level + 1)

        for child in self.children.values():
            child.visit(callback, f'{path}/{child.name}' if path else child.name, level + 1)

    def tree(self) -> str:
        """
        Returns a string representation of the tree
        """
        ret = ''

        def _tree(
            node: HandlerNode,
            path: str,
            type_: typing.Literal['handler', 'custom_method', 'detail_method'],
            level: int,
        ) -> None:
            nonlocal ret

            if not node.handler:
                raise ValueError(f'Node {node.name} has no handler, cannot generate tree')

            ret += f'{"  " * level}* {path} {node.handler.__name__} ({type_})\n'

        self.visit(_tree)
        return ret

    def find_path(self, path: str | list[str]) -> 'HandlerNode | None':
        """
        Returns the node for a given path, or None if not found
        """
        if not path or not self.children:
            return self

        # Remove any trailing '/' to allow some "bogus" paths with trailing slashes
        path = path.lstrip('/').split('/') if isinstance(path, str) else path

        if path[0] not in self.children:
            return None

        return self.children[path[0]].find_path(path[1:])  # Recursive call

    def full_path(self) -> str:
        """
        Returns the full path of this node
        """
        if self.name == '' or self.parent is None:
            return ''

        parent_full_path = self.parent.full_path()

        if parent_full_path == '':
            return self.name

        return f'{parent_full_path}/{self.name}'
