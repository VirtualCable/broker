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

import abc
import collections.abc
import logging
import typing

from django.db import models
from django.utils.translation import gettext as _

from uds.core import consts
from uds.core import exceptions
from uds.core import types
from uds.core.module import Module
from uds.core.util import permissions
from uds.REST.utils import sanitize_params

# from uds.models import ManagedObjectModel
from ..handlers import Handler

# Not imported at runtime, just for type checking

T = typing.TypeVar("T", bound=models.Model)
T_Item = typing.TypeVar("T_Item", bound=types.rest.BaseRestItem)

logger: logging.Logger = logging.getLogger(__name__)


# pylint: disable=unused-argument
class BaseModelHandler(Handler, abc.ABC, typing.Generic[T_Item]):
    """
    Base Handler for Master & Detail Handlers
    """

    # Fields that are going to be saved directly, as read by
    # ``fields_from_params`` (see ``parse_save_fields`` for the marker
    # syntax). Entry forms:
    # * "field"              -> required: missing in the request raises,
    #                           unless the optional ``defaults`` mapping
    #                           passed to ``fields_from_params`` provides
    #                           it; the raw param value is used.
    # * "field:default"      -> optional: when missing, the static
    #                           ``default`` is used (the literal "_" means
    #                           "skip the field entirely"); when present,
    #                           the value is coerced to ``str``.
    # Note that these fields have to be present in the model, and they
    # can be "edited" in the pre_save method. Master handlers populate
    # this; detail handlers may as well (it is interpreted through the
    # same machinery), and an empty list simply means "nothing is saved
    # straight from params".
    FIELDS_TO_SAVE: typing.ClassVar[list[str]] = []

    @classmethod
    def parse_save_fields(
        cls,
        fields_list: collections.abc.Sequence[str],
    ) -> collections.abc.Iterator[tuple[str, str | None]]:
        """Yield ``(name, modifier)`` for every declared save field.

        Single interpretation point for the ``"field:modifier"`` marker
        of :attr:`FIELDS_TO_SAVE`: the modifier is everything after the
        first ``:`` (``None`` when the entry is plain; historically
        ``split(":")[:2]`` silently dropped a second ``:``, no
        declaration in the tree relies on that). A consumer must never
        re-split the raw entries itself: that is what previously made the
        ETag hash ignore ``host:`` / ``port:0`` style fields.
        """
        for entry in fields_list:
            name, separator, modifier = entry.partition(":")
            yield name, modifier if separator else None

    def check_access(
        self,
        obj: models.Model,
        permission: "types.permissions.PermissionType",
        root: bool = False,
    ) -> None:
        if not permissions.has_access(self._user, obj, permission, root):
            raise exceptions.rest.AccessDenied("Access denied")

    def get_permissions(self, obj: models.Model, root: bool = False) -> int:
        return permissions.effective_permissions(self._user, obj, root)

    @classmethod
    def extra_type_info(cls: type[typing.Self], type_: type["Module"]) -> types.rest.ExtraTypeInfo | None:
        """
        Returns info about the type
        In fact, right now, it returns an empty dict, that will be extended by typeAsDict
        """
        return None

    @typing.final
    @classmethod
    def as_typeinfo(cls: type[typing.Self], type_: type["Module"]) -> types.rest.TypeInfo:
        """
        Returns a dictionary describing the type (the name, the icon, description, etc...)
        """
        return types.rest.TypeInfo(
            name=_(type_.mod_name()),
            type=type_.mod_type(),
            description=_(type_.description()),
            icon=type_.icon64().replace("\n", ""),
            extra=cls.extra_type_info(type_),
            group=getattr(type_, "group", None),
        )

    def fields_from_params(
        self, fields_list: list[str], *, defaults: dict[str, typing.Any] | None = None
    ) -> dict[str, typing.Any]:
        """
        Reads the indicated fields from the parameters received.

        Arguments:
            fields_list: List of fields to read, in the ``FIELDS_TO_SAVE``
                declaration language (plain or ``"field:default"`` entries;
                see the ``FIELDS_TO_SAVE`` comment and ``parse_save_fields``).
            defaults: Fallback values for plain fields missing from the
                request (never consulted for ``"field:default"`` entries,
                whose fallback is the embedded static default).

        Note:
            Marked fields present in the request are coerced to ``str``;
            plain fields keep the raw param value.
        """
        args: dict[str, typing.Any] = {}
        try:
            for name, modifier in self.parse_save_fields(fields_list):
                if modifier is None:  # Required field, with a possible default on defaults dict
                    if name not in self._params:
                        if defaults and name in defaults:
                            args[name] = defaults[name]
                        else:
                            raise exceptions.rest.RequestError(f"needed parameter not found in data {name}")
                    else:
                        # Set the value
                        args[name] = self._params[name]
                else:  # optional field? get default if not present
                    # Convert "None" to None
                    default = None if modifier == "None" else modifier
                    # If key is not present, and default = _, then it is not required: skip it
                    if default == "_" and name not in self._params:
                        continue
                    args[name] = str(self._params.get(name, default))
        except KeyError as e:
            raise exceptions.rest.RequestError(f"needed parameter not found in data {e}")

        return args

    def odata_filter(self, qs: models.QuerySet[T]) -> list[T]:
        """
        Invoked to filter the queryset according to parameters received
        Default implementation does not filter anything

        Args:
            qs: Queryset to filter

        Returns:
            Filtered queryset as a list

        Note:
            This is not final, so we can override it in subclasses if needed
        """
        return self.filter_odata_queryset(qs)

    # Success methods
    def success(self) -> str:
        """
        Utility method to be invoked for simple methods that returns a simple OK response
        """
        logger.debug("Returning success on %s %s", self.__class__, self._args)
        return consts.OK

    def test(self, type_: str) -> str:
        """
        Invokes a test for an item
        """
        logger.debug("Called base test for %s --> %s", self.__class__.__name__, sanitize_params(self._params))
        raise exceptions.rest.NotSupportedError(_("Testing not supported"))

    @classmethod
    @typing.override
    def api_components(cls: type[typing.Self]) -> types.rest.api.Components:
        """
        Default implementation does not have any component types. (for Api specification purposes)
        """
        return types.rest.api.Components()

    @classmethod
    @typing.override
    def api_paths(
        cls: type[typing.Self], path: str, tags: list[str], security: str
    ) -> dict[str, types.rest.api.PathItem]:
        """
        Returns the API operations that should be registered
        """
        return {}

    @typing.final
    @staticmethod
    def common_components() -> types.rest.api.Components:
        """
        Returns a list of common components for the API for ModelHandlers (Model and Detail)
        """
        from uds.core.util import api as api_utils

        return (
            api_utils.api_components(types.rest.TypeInfo)
            | api_utils.api_components(types.rest.TableInfo)
            | api_utils.api_components(types.rest.LogEntry)
            | api_utils.api_components(
                types.ui.GuiElement,
                removable_fields=["value", "gui.old_field_name", "gui.value", "gui.field_name"],
            )
        )

    @typing.final
    @staticmethod
    def common_paths() -> dict[str, types.rest.api.PathItem]:
        """
        Returns a dictionary of common paths for the API for ModelHandlers (Model and Detail)
        """
        return {}
