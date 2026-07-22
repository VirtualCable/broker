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

from django.db import models
from django.utils.translation import gettext as _

from uds.core import consts
from uds.core import types
from uds.core.util import api as api_utils

from uds.REST.utils import camel_and_snake_case_from

# Not imported at runtime, just for type checking
if typing.TYPE_CHECKING:
    from uds.REST.model.master import ModelHandler

logger = logging.getLogger(__name__)

T = typing.TypeVar("T", bound=models.Model)
T_Item = typing.TypeVar("T_Item", bound=types.rest.BaseRestItem)


def api_paths(
    cls: type["ModelHandler[T_Item]"], path: str, tags: list[str], security: str
) -> dict[str, types.rest.api.PathItem]:
    """
    Returns the API operations that should be registered
    """

    name = cls.REST_API_INFO.name if cls.REST_API_INFO.name else cls.MODEL.__name__
    get_tags = tags
    put_tags = tags  # + ['Create', 'Modify']
    post_tags = tags
    delete_tags = tags  # + ['Delete']

    base_type = next(iter(api_utils.get_generic_types(cls)), None)
    if base_type is None:
        logger.error("Base type not detected: %s", cls)
        return {}  # Skip
    else:
        base_type_name = base_type.__name__

    # POST create operation (preferred way to create items per Change G)
    post_create_op = types.rest.api.Operation(
        summary=f"Create a new {name} item",
        description=f"Create a new {name} item",
        parameters=[],
        requestBody=api_utils.gen_request_body(base_type_name, create=True),
        responses=api_utils.gen_response(base_type_name, single=True),
        tags=post_tags,
        security=security,
    )

    # PUT create operation (legacy — deprecated in favor of POST)
    put_create_op = types.rest.api.Operation(
        summary=f"Creates a new {name} item",
        description=(f"Creates a new {name} item. Deprecated: use POST /{path} instead."),
        deprecated=True,
        parameters=[],
        requestBody=api_utils.gen_request_body(base_type_name, create=True),
        responses=api_utils.gen_response(base_type_name, single=True),
        tags=put_tags,
        security=security,
    )

    # QUERY operation (RFC 10008 — safe GET with OData in body)
    query_op = types.rest.api.Operation(
        summary=f"Query {name} items with OData in body",
        description=(
            f"Query {name} items using OData parameters "
            f"($filter, $orderby, $top, $skip, $select) in the request body. "
            f"Equivalent to GET but allows complex queries beyond URL length limits."
        ),
        requestBody=api_utils.gen_odata_request_body(),
        responses=api_utils.gen_response(base_type_name, single=False),
        tags=get_tags,
        security=security,
    )

    api_desc = {
        path: types.rest.api.PathItem(
            get=types.rest.api.Operation(
                summary=f"Get all {name} items",
                description=f"Retrieve a list of all {name} items",
                parameters=api_utils.gen_odata_parameters(),
                responses=api_utils.gen_response(base_type_name, single=False),
                tags=get_tags,
                security=security,
            ),
            post=post_create_op,
            put=put_create_op,
            query=query_op,
        ),
        f"{path}/{{uuid}}": types.rest.api.PathItem(
            get=types.rest.api.Operation(
                summary=f"Get {name} item by UUID",
                description=f"Retrieve a {name} item by UUID",
                parameters=api_utils.gen_uuid_parameters(with_odata=True),
                responses=api_utils.gen_response(base_type_name, single=True),
                tags=get_tags,
                security=security,
            ),
            put=types.rest.api.Operation(
                summary=f"Update {name} item by UUID",
                description=f"Update an existing {name} item by UUID",
                parameters=api_utils.gen_uuid_parameters(with_odata=False),
                requestBody=api_utils.gen_request_body(base_type_name, create=False),
                responses=api_utils.gen_response(base_type_name, single=True),
                tags=put_tags,
                security=security,
            ),
            delete=types.rest.api.Operation(
                summary=f"Delete {name} item by UUID",
                description=f"Delete a {name} item by UUID",
                parameters=api_utils.gen_uuid_parameters(with_odata=False),
                responses=api_utils.gen_response(base_type_name, single=True),
                tags=delete_tags,
                security=security,
            ),
        ),
        f"{path}/{consts.rest.OVERVIEW}": types.rest.api.PathItem(
            get=types.rest.api.Operation(
                summary=f"Get overview of {name} items",
                description=f"Retrieve an overview of {name} items",
                parameters=api_utils.gen_odata_parameters(),
                responses=api_utils.gen_response(base_type_name, single=False),
                tags=get_tags,
                security=security,
            )
        ),
        f"{path}/{consts.rest.TABLEINFO}": types.rest.api.PathItem(
            get=types.rest.api.Operation(
                summary=f"Get table info of {name} items",
                description=f"Retrieve table info of {name} items",
                parameters=[],
                responses=api_utils.gen_response("TableInfo", single=True),
                tags=get_tags,
                security=security,
            )
        ),
        f"{path}/{{uuid}}/{consts.rest.LOG}": types.rest.api.PathItem(
            get=types.rest.api.Operation(
                summary=f"Get logs of {name} item by UUID",
                description=f"Retrieve logs of a {name} item by UUID",
                parameters=api_utils.gen_uuid_parameters(with_odata=False),
                responses=api_utils.gen_response("LogEntry", single=False),
                tags=get_tags,
                security=security,
            )
        ),
    }

    def emit_custom_method(cm: "types.rest.ModelCustomMethod", method_name: str | None, deprecated: bool) -> None:
        method_name = method_name or cm.name
        cm_path = f"{path}/{{uuid}}/{method_name}" if cm.needs_parent else f"{path}/{method_name}"
        # Emit the declared HTTP method in the OpenAPI spec.
        # POST custom methods are documented as POST; GET methods as GET.
        # Legacy COMPAT-mode GET access to POST methods is intentionally
        # undocumented.
        # Prefer cm.description when provided; fall back to generic text.
        cm_summary = cm.description if cm.description else f"{method_name}"
        cm_desc = cm.description if cm.description else f"Execute custom method {method_name} for {name}"
        op = types.rest.api.Operation(
            summary=f"{cm_summary} ({name})",
            description=cm_desc,
            parameters=api_utils.gen_uuid_parameters(with_odata=False) if cm.needs_parent else [],
            responses=api_utils.gen_response("object", single=True),
            tags=get_tags,
            security=security,
            deprecated=deprecated,
        )
        # Attach request body for POST methods with declared params
        if cm.params and cm.method == types.rest.CustomMethodMethod.POST:
            op.requestBody = types.rest.api.RequestBody(
                description=f"Parameters for {method_name}",
                required=True,
                content=types.rest.api.Content(
                    media_type="application/json",
                    schema=cm.params,
                ),
            )

        if cm.method == types.rest.CustomMethodMethod.POST:
            api_desc[cm_path] = types.rest.api.PathItem(post=op)
        else:
            api_desc[cm_path] = types.rest.api.PathItem(get=op)

    for cm in cls.CUSTOM_METHODS:
        emit_custom_method(cm, None, False)
        if "_" in cm.name:
            emit_custom_method(cm, camel_and_snake_case_from(cm.name)[0], True)

    if cls.REST_API_INFO.typed.is_single_type():
        api_desc[f"{path}/{consts.rest.GUI}"] = types.rest.api.PathItem(
            get=types.rest.api.Operation(
                summary=f"Get GUI representation of {name} items",
                description=f"Retrieve the GUI representation of {name} items",
                parameters=[],
                responses=api_utils.gen_response("GuiElement", single=False),
                tags=get_tags,
                security=security,
            )
        )

    if cls.REST_API_INFO.typed.supports_multiple_types():
        api_desc.update(
            {
                f"{path}/{consts.rest.GUI}/{{type}}": types.rest.api.PathItem(
                    get=types.rest.api.Operation(
                        summary=f"Get GUI representation of {name} type",
                        description=f"Retrieve a {name} GUI representation by type",
                        parameters=[
                            types.rest.api.Parameter(
                                name="type",
                                in_="path",
                                required=True,
                                description=f"The type of the {name} GUI representation",
                                schema=types.rest.api.Schema(type="string"),
                            )
                        ],
                        responses=api_utils.gen_response("GuiElement", single=True),
                        tags=get_tags,
                        security=security,
                    )
                ),
                f"{path}/{consts.rest.TYPES}": types.rest.api.PathItem(
                    get=types.rest.api.Operation(
                        summary=f"Get types of {name} items",
                        description=f"Retrieve types of {name} items",
                        parameters=[],
                        responses=api_utils.gen_response("TypeInfo", single=False),
                        tags=get_tags,
                        security=security,
                    )
                ),
                f"{path}/{consts.rest.TYPES}/{{type}}": types.rest.api.PathItem(
                    get=types.rest.api.Operation(
                        summary=f"Get {name} item by type",
                        description=f"Retrieve a {name} item by type",
                        parameters=[
                            types.rest.api.Parameter(
                                name="type",
                                in_="path",
                                required=True,
                                description="The type of the item",
                                schema=types.rest.api.Schema(type="string"),
                            )
                        ],
                        responses=api_utils.gen_response("TypeInfo", single=True),
                        tags=get_tags,
                        security=security,
                    )
                ),
            },
        )

    return api_desc
