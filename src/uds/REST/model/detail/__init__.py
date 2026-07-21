# -*- coding: utf-8 -*-
#
# Copyright (c) 2014-2023 Virtual Cable S.L.
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
# pylint: disable=too-many-public-methods

import logging
import typing
import collections.abc
import abc

from django.db import models

from uds.core import consts, exceptions, types, module
from uds.core.types.rest import T_Item
from uds.core.util.model import process_uuid
from uds.core.util import api as api_utils, model as model_utils
from uds.REST.utils import rest_result

from uds.REST.model.base import BaseModelHandler
from uds.REST.utils import camel_and_snake_case_from, is_camel_case, sanitize_params

T = typing.TypeVar("T", bound=models.Model)

# Not imported at runtime, just for type checking
if typing.TYPE_CHECKING:
    from django.db.models.query import QuerySet

    from uds.models import User
    from uds.REST.model.master import ModelHandler


logger = logging.getLogger(__name__)

# Details do not have types at all
# so, right now, we only process details petitions for Handling & tables info
# noinspection PyMissingConstructor


class DetailHandler(BaseModelHandler[T_Item], abc.ABC):
    """
    Detail handler (for relations such as provider-->services, authenticators-->users,groups, deployed services-->cache,assigned, groups, transports
    Urls recognized for GET are:
    [path] --> get Items (all items, this call is delegated to get_items)
    [path]/overview
    [path]/ID
    [path]/gui
    [path]/gui/TYPE
    [path]/types
    [path]/types/TYPE
    [path]/tableinfo
    ....?filter=[filter],[filter]..., filters are simple unix files filters, with ^ and $ supported
    For PUT:
    [path] --> create NEW item
    [path]/ID --> Modify existing item
    For DELETE:
    [path]/ID

    Also accepts GET methods for "custom" methods
    """

    CUSTOM_METHODS: typing.ClassVar[list[types.rest.ModelCustomMethod]] = []
    _parent: 'ModelHandler[T_Item] | None'  # Parent handler, that is the ModelHandler that contains this detail
    _path: str
    _params: typing.Any  # _params is deserialized object from request
    _args: list[str]
    _parent_item: models.Model  # Parent item, that is the parent model element
    _user: 'User'

    def __init__(
        self,
        parent_handler: 'ModelHandler[T_Item]',
        path: str,
        params: typing.Any,
        *args: str,
        user: 'User',
        parent_item: models.Model,
    ) -> None:
        """
        Detail Handlers in fact "disabled" handler most initialization, that is no needed because
        parent modelhandler has already done it (so we must access through parent handler)
        """
        # Parent init not invoked because their methos are not used on detail handlers (only on parent handlers..)
        self._parent = parent_handler
        self._request = parent_handler._request
        self._path = path
        self._params = params
        self._args = list(args)
        self._parent_item = parent_item
        self._user = user
        self._odata = parent_handler._odata  # Ref to parent OData
        self._headers = parent_handler._headers  # "link" headers

    def _check_is_custom_method(
        self,
        check: str,
        parent: models.Model,
        arg: typing.Any = None,
        *,
        http_method: types.rest.CustomMethodMethod = types.rest.CustomMethodMethod.GET,
    ) -> typing.Any:
        """
        Checks current custom methods for a matching name.

        :param check: Method name to check (camel or snake case)
        :param parent: Parent Model element
        :param arg: Optional argument to pass to the custom method
        :param http_method: The HTTP method of the incoming request (GET or POST).
            In COMPAT mode, POST custom methods also match via GET (legacy).
            In NO_COMPAT mode, only the declared method matches.
        """
        is_compat = self.api_compat() == types.rest.ApiCompat.COMPAT

        for to_check in self.CUSTOM_METHODS:
            camel_case_name, snake_case_name = camel_and_snake_case_from(to_check.name)
            if check not in (camel_case_name, snake_case_name):
                continue

            # Detect camelCase URL segment usage: dispatch still works but
            # the camelCase form is legacy (v5/v6) and will be removed in v7.
            if is_camel_case(check) and check != snake_case_name:
                if is_compat:
                    self.add_deprecation_headers(
                        successor_hint=f'use snake_case form: {snake_case_name} (instead of {check})'
                    )
                else:
                    raise exceptions.rest.GoneError(
                        f'camelCase form "{check}" is removed; use snake_case "{snake_case_name}"'
                    )

            # HTTP-method check after name match
            if to_check.method != http_method:
                if (
                    to_check.method == types.rest.CustomMethodMethod.POST
                    and http_method == types.rest.CustomMethodMethod.GET
                ):
                    if is_compat:
                        # COMPAT: allow GET on POST method with deprecation headers
                        self.add_deprecation_headers(f'use POST {self._path}/{check}')
                    else:
                        # NO_COMPAT: this endpoint is gone
                        raise exceptions.rest.GoneError(
                            f'This endpoint is deprecated. Use POST {self._path}/{check}'
                        )
                else:
                    continue

            operation = getattr(self, snake_case_name, None) or getattr(self, camel_case_name, None)
            if operation:
                if not arg:
                    return operation(parent)
                return operation(parent, arg)

        return consts.rest.NOT_FOUND

    def _get_fields_from_gui(self, parent: models.Model, for_type: str) -> list[str]:
        gui = self.get_gui(parent, for_type)
        return [i.name for i in gui]

    def _item_with_etag_from_uuuid(self, parent: models.Model, uuid: str) -> tuple[T_Item, str]:
        response = self.get_item(parent, process_uuid(uuid))
        etag = ''
        if isinstance(response, types.rest.ManagedObjectItem):
            fields: list[str] = self._get_fields_from_gui(
                parent,
                response.item.data_type,  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
            )
            # Append etag header
            etag = response.etag(*fields)
        return response, etag  # pyright: ignore[reportUnknownVariableType]

    # pylint: disable=too-many-branches,too-many-return-statements
    def get(self) -> typing.Any:
        """
        Processes GET method for a detail Handler
        """
        # Process args
        logger.debug('Detail args for GET: %s', self._args)

        parent: models.Model = self._parent_item

        # if has custom methods, look for if this request matches any of them
        if len(self._args) >= 1:
            r = self._check_is_custom_method(self._args[0], parent)
            if r is not consts.rest.NOT_FOUND:
                return r

        match self._args:
            case []:  # same as overview
                return self.get_items(parent)
            case [consts.rest.OVERVIEW]:
                return self.get_items(parent)
            case [consts.rest.OVERVIEW, *_fails]:
                raise exceptions.rest.RequestError('Invalid overview request') from None
            case [consts.rest.TYPES]:
                known_types = self.enum_types(parent, None)
                logger.debug('Types: %s', known_types)
                return [i.as_dict() for i in known_types]
            case [consts.rest.TYPES, for_type]:
                return [i.as_dict() for i in self.enum_types(parent, for_type)]
            case [consts.rest.TYPES, for_type, *_fails]:
                raise exceptions.rest.RequestError('Invalid types request') from None
            case [consts.rest.TABLEINFO]:
                return self.get_table(parent).as_dict()
            case [consts.rest.TABLEINFO, *_fails]:
                raise exceptions.rest.RequestError('Invalid table info request') from None
            case [consts.rest.GUI]:
                return sorted(self.get_processed_gui(parent, ''), key=lambda f: f.gui.order)
            case [consts.rest.GUI, for_type]:
                return sorted(self.get_processed_gui(parent, for_type), key=lambda f: f.gui.order)
            case [consts.rest.GUI, for_type, *_fails]:
                raise exceptions.rest.RequestError('Invalid GUI request') from None
            case [item_id, consts.rest.LOG]:
                return self.get_logs(parent, process_uuid(item_id))
            case [consts.rest.LOG, *_fails]:
                raise exceptions.rest.RequestError('Invalid log request') from None
            case [consts.rest.POSITION, item_uuid]:
                return self.get_item_position(parent, item_uuid)
            case [one_arg]:
                response, etag = self._item_with_etag_from_uuuid(parent, one_arg)
                if etag:
                    self.add_header('ETag', etag)

                return response  # pyright: ignore[reportUnknownVariableType]

            case _:
                # Maybe a custom method of an specific item?
                r = self._check_is_custom_method(self._args[1], parent, self._args[0])
                if r is not None:
                    return r

        # Not understood, fallback, maybe the derived class can understand it
        return self.fallback_get()

    def put(self) -> typing.Any:
        """
        Process the "PUT" operation, making the correspondent checks.
        Evaluates if it is a new element or a "modify" operation (based on if it has parameter),
        and invokes "save_item" with parent & item (that can be None for a new Item)
        """
        logger.debug('Detail args for PUT: %s, %s', self._args, sanitize_params(self._params))

        parent: models.Model = self._parent_item

        # if has custom methods, look for if this request matches any of them
        if len(self._args) > 1:
            r = self._check_is_custom_method(self._args[1], parent)
            if r is not consts.rest.NOT_FOUND:
                return r

        # Create new item unless 1 param received (the id of the item to modify)
        item = None
        if len(self._args) == 1:
            item = self._args[0]
        elif len(self._args) > 1:  # PUT expects 0 or 1 parameters. 0 == NEW, 1 = EDIT
            raise exceptions.rest.RequestError('Invalid PUT request') from None

        # PUT create (0 args) → delegate to POST create with deprecation header
        if item is None:
            self.add_deprecation_headers(successor_hint=f'use POST /{self._path} to create items')
            return self._perform_create(parent)

        logger.debug('Invoking proper saving detail item %s', item)
        # Try to get the etag from item.
        _not_used, etag = self._item_with_etag_from_uuuid(parent, item)
        self.check_if_match_header(etag)

        return rest_result(self.save_item(parent, item))

    def post(self) -> typing.Any:
        """
        Process the POST operation.

        POST on collection (no args) creates a new item (Change G — preferred over PUT).
        Dispatches to POST custom methods when the path matches.
        """
        logger.debug('Detail args for POST: %s, %s', self._args, sanitize_params(self._params))

        parent: models.Model = self._parent_item

        # Check for custom methods at _args[0] (e.g. POST /collection/{id}/detail/method)
        if len(self._args) >= 1:
            r = self._check_is_custom_method(
                self._args[0], parent, http_method=types.rest.CustomMethodMethod.POST
            )
            if r is not consts.rest.NOT_FOUND:
                return r

        # Check for custom methods at _args[1] with _args[0] as arg
        # (e.g. POST /collection/{id}/detail/{item_id}/reset)
        if len(self._args) > 1:
            r = self._check_is_custom_method(
                self._args[1], parent, self._args[0], http_method=types.rest.CustomMethodMethod.POST
            )
            if r is not consts.rest.NOT_FOUND:
                return r

        # POST on collection (no args) → create new item (Change G)
        if len(self._args) == 0:
            return self._perform_create(parent)

        raise exceptions.rest.RequestError('Invalid POST request') from None

    def _perform_create(self, parent: models.Model) -> typing.Any:
        """
        Common create logic used by both POST (preferred) and PUT (legacy).
        """
        logger.debug('Creating detail item under parent %s', parent)
        return rest_result(self.save_item(parent, None))

    def delete(self) -> typing.Any:
        """
        Process the "DELETE" operation, making the correspondent checks.
        Extracts the item id and invokes delete_item with parent item and item id (uuid)
        """
        logger.debug('Detail args for DELETE: %s', self._args)

        parent = self._parent_item

        if len(self._args) != 1:
            raise exceptions.rest.RequestError('Invalid DELETE request') from None

        self.delete_item(parent, process_uuid(self._args[0]))

        return consts.OK

    def fallback_get(self) -> typing.Any:
        """
        Invoked if default get can't process request.
        Here derived classes can process "non default" (and so, not understood) GET constructions
        """
        raise exceptions.rest.RequestError('Invalid GET request') from None

    # Override this to provide functionality
    # Default (as sample) get_items
    @abc.abstractmethod
    def get_items(self, parent: models.Model) -> types.rest.ItemsResult[T_Item]:
        """
        This MUST be overridden by derived classes
        Excepts to return a list of dictionaries or a single dictionary, depending on "item" param
        If "item" param is None, ALL items are expected to be returned as a list of dictionaries
        If "Item" param has an id (normally an uuid), one item is expected to be returned as dictionary
        """
        # if item is None:  # Returns ALL detail items
        #     return []
        # return {}  # Returns one item
        raise NotImplementedError(f'Must provide an get_items method for {self.__class__} class')

    @abc.abstractmethod
    def get_item(self, parent: models.Model, item: str) -> T_Item:
        """
        Utility method to get a single item by uuid
        :param parent: Parent model
        :param item: Item uuid
        :return: Item as dictionary
        """
        raise NotImplementedError(f'Must provide an get_item method for {self.__class__} class')

    # Default save
    def save_item(self, parent: models.Model, item: str | None) -> T_Item:
        """
        Invoked for a valid "put" operation
        If this method is not overridden, the detail class will not have "Save/modify" operations.
        Parameters (probably object fields) must be retrieved from "_params" member variable
        :param parent: Parent of this detail (parent DB Object)
        :param item: Item id (uuid)
        :return: Normally "success" is expected, but can throw any "exception"
        """
        logger.debug('Default save_item handler caller for %s', self._path)
        raise exceptions.rest.RequestError('Invalid PUT request') from None

    # Default delete
    def delete_item(self, parent: models.Model, item: str) -> None:
        """
        Invoked for a valid "delete" operation.
        If this method is not overriden, the detail class will not have "delete" operation.
        :param parent: Parent of this detail (parent DB Object)
        :param item: Item id (uuid)
        :return: Normally "success" is expected, but can throw any "exception"
        """
        raise exceptions.rest.InvalidMethodError('Object does not support delete')

    def get_table(self, parent: models.Model) -> types.rest.TableInfo:
        """
        Returns the table info for this detail, that is the title, fields and row style
        :param parent: Parent object
        :return: TableInfo object with title, fields and row style
        """
        return types.rest.TableInfo.null()

    def get_gui(self, parent: models.Model, for_type: str) -> list[types.ui.GuiElement]:
        """
        Gets the gui that is needed in order to "edit/add" new items on this detail
        If not overriden, means that the detail has no edit/new Gui

        Args:
            parent (models.Model): Parent object
            for_type (str): Type of object needing gui

        Return:
            list[types.ui.GuiElement]: A list of gui fields
        """
        # raise RequestError('Gui not provided for this type of object')
        return []

    def get_processed_gui(self, parent: models.Model, for_type: str) -> list[types.ui.GuiElement]:
        return sorted(self.get_gui(parent, for_type), key=lambda f: f.gui.order)

    def enum_types(
        self, parent: models.Model, for_type: str | None
    ) -> collections.abc.Iterable[types.rest.TypeInfo]:
        """
        The default is that detail element will not have any types (they are "homogeneous")
        but we provided this method, that can be overridden, in case one detail needs it
        (for example, on services)

        Args:
            parent (models.Model): Parent object
            for_type (str | None): Request argument in fact

        Return:
            collections.abc.Iterable[types.rest.TypeInfoDict]: A list of dictionaries describing type/types
        """
        return []  # Default is that details do not have types

    def get_logs(self, parent: 'models.Model', item: str) -> list[typing.Any]:
        """
        If the detail has any log associated with it items, provide it overriding this method

        Args:
            parent: Parent model
            item: Item id (uuid)

        Returns:
            A list of log elements (normally got using "uds.core.util.log.get_logs" method)
        """
        raise exceptions.rest.InvalidMethodError('Object does not support logs')

    def calc_item_position(self, item_uuid: str, qs: 'QuerySet[T]') -> int:
        """
        Helper method to get the position of an item in a queryset

        Args:
            item_uuid (str): UUID of the item to find
            qs (QuerySet[T]): Queryset to search into

        Returns:
            int: Position of the item in the default ordering, -1 if not found
        """
        # Find item in qs, may be none, then return -1
        obj = qs.filter(uuid__iexact=process_uuid(item_uuid)).first()
        if obj:
            return model_utils.get_position_in_queryset(obj, qs)
        return -1

    def get_item_position(self, parent: models.Model, item_uuid: str) -> int:
        """
        Tries to get the position of an item in the default ordering of the detail items

        Args:
            item_uuid (str): UUID of the item to find
        Returns:
            int: Position of the item in the default ordering, -1 if not found

        Note:
            Override this method if the detail can provide item position
        """
        return -1

    @classmethod
    def possible_types(cls: type[typing.Self]) -> collections.abc.Iterable[type[module.Module]]:
        """
        Note: This method returns ALL POSSIBLE TYPES for the specific model, not just those
              related to the father. Is used for api composition.
              enum_types, hear, is the one to filter types by parent, etc..
        """
        return []

    @classmethod
    @typing.override
    def api_components(cls: type[typing.Self]) -> types.rest.api.Components:
        """
        Default implementation does not have any component types. (for Api specification purposes)
        """
        # If no get_items, has no components (if custom components is needed, override this classmethod)
        return api_utils.get_component_from_type(cls)

    @classmethod
    @typing.override
    def api_paths(
        cls: type[typing.Self], path: str, tags: list[str], security: str
    ) -> dict[str, types.rest.api.PathItem]:
        """
        Returns the API operations that should be registered
        """
        from .api_helpers import api_paths  # Avoid circular import

        return api_paths(cls, path, tags=tags, security=security)
