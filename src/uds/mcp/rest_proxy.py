"""In-process proxy from MCP capabilities to existing REST handlers."""

import dataclasses
import typing

from asgiref.sync import sync_to_async

from uds.REST.handlers import Handler
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.core.util import permissions


# ``$``-prefixed keys forwarded verbatim to the REST handler. Keep this
# in sync with what ``ODataParams.from_dict`` actually consumes; unsupported
# keys would be silently ignored downstream.
_ODATA_KEYS: typing.Final[tuple[str, ...]] = (
    "$filter",
    "$orderby",
    "$top",
    "$skip",
    "$select",
)

# MCP-side page caps. The REST API itself is unlimited by default; MCP
# responses are consumed by LLM clients, so a missing ``$top`` gets a
# default page size and absurd values are clamped to a maximum.
_DEFAULT_TOP: typing.Final[int] = 100
_MAX_TOP: typing.Final[int] = 500


@dataclasses.dataclass(frozen=True, slots=True)
class ODataArgs:
    """Structured arguments the MCP client sends to a list tool."""

    filter: str | None = None
    orderby: str | None = None
    top: int | None = None
    skip: int | None = None
    select: tuple[str, ...] = ()

    def to_odata_params(self) -> dict[str, typing.Any]:
        """Translate the structured fields to the OData ``$...`` parameter names."""
        params: dict[str, typing.Any] = {}
        if self.filter is not None:
            params["$filter"] = self.filter
        if self.orderby is not None:
            params["$orderby"] = self.orderby
        if self.top is not None:
            params["$top"] = self.top
        if self.skip is not None:
            params["$skip"] = self.skip
        if self.select:
            params["$select"] = ",".join(self.select)
        return params


def odata_params_from(raw: typing.Any) -> dict[str, typing.Any]:
    """Normalise an MCP arguments payload into OData ``$...`` parameters.

    Accepts either a flat dict with ``$filter`` keys, a dict with the
    structured ODataArgs fields (``filter``, ``orderby``, ``top`` ...),
    or an ``ODataArgs`` instance. Anything else returns an empty dict.
    """
    if isinstance(raw, ODataArgs):
        return raw.to_odata_params()
    if not isinstance(raw, dict):
        return {}

    raw_dict = typing.cast("dict[str, typing.Any]", raw)
    params: dict[str, typing.Any] = {}
    for key in _ODATA_KEYS:
        value = raw_dict.get(key)
        if value is not None:
            params[key] = value

    if "filter" in raw_dict and "$filter" not in params:
        params["$filter"] = raw_dict["filter"]
    if "orderby" in raw_dict and "$orderby" not in params:
        params["$orderby"] = raw_dict["orderby"]
    if "top" in raw_dict and "$top" not in params:
        params["$top"] = raw_dict["top"]
    if "skip" in raw_dict and "$skip" not in params:
        params["$skip"] = raw_dict["skip"]
    select = raw_dict.get("select")
    if select and "$select" not in params:
        if isinstance(select, (list, tuple)):
            params["$select"] = ",".join(typing.cast("list[str]", select))
        else:
            params["$select"] = str(select)
    return params


@dataclasses.dataclass(frozen=True, slots=True)
class RestTarget:
    """Identify an existing REST handler operation without an HTTP request.

    When ``parent`` is set, ``handler`` is a ``DetailHandler`` and the
    collection is scoped under the parent collection instance identified by
    its ``{uuid}`` argument. The parent target is used to resolve the parent
    model object and to preserve the same permission checks the REST
    dispatcher performs.
    """

    handler: type[Handler]
    path: str
    method: types.rest.CustomMethodMethod = types.rest.CustomMethodMethod.QUERY
    args: tuple[str, ...] = ()
    parent: "RestTarget | None" = None


class RestProxy:
    """Invoke existing REST handlers while preserving REST authentication."""

    def __init__(self, request: typing.Any = None) -> None:
        self.request: typing.Any = request

    async def execute(
        self,
        target: RestTarget,
        request: typing.Any,
        params: dict[str, typing.Any],
    ) -> typing.Any:
        """Execute a REST target outside the async event loop.

        The handler receives the original request, so its normal
        ``AuthenticationResolver`` and permission checks remain active. The
        complete handler lifecycle is kept in one thread-sensitive sync
        boundary because Django ORM access is synchronous.
        """
        return await sync_to_async(self._execute_sync, thread_sensitive=True)(target, request, params, None)

    async def execute_collection(
        self,
        target: RestTarget,
        request: typing.Any,
        arguments: typing.Any,
    ) -> typing.Any:
        """Execute a list tool backed by a REST collection.

        Translates the structured MCP arguments into OData ``$...``
        parameters and dispatches ``Handler.query()`` so the existing
        OData validation, permissions and filters are reused. Detail
        collections additionally require the parent ``uuid`` in the
        arguments (``parent_uuid``).

        A missing ``$top`` defaults to ``_DEFAULT_TOP`` and larger values
        are clamped to ``_MAX_TOP``: MCP responses go to LLM clients, so
        every page is bounded even when the REST collection is not.
        """
        params = odata_params_from(arguments)
        params.setdefault("$top", _DEFAULT_TOP)
        try:
            params["$top"] = min(int(typing.cast(int, params["$top"])), _MAX_TOP)
        except (TypeError, ValueError):
            # Non-numeric ``$top``: let ODataParams reject it with a clean
            # ``invalid params`` error instead of failing here.
            pass
        parent_uuid: str | None = None
        if isinstance(arguments, dict):
            raw = typing.cast("dict[str, typing.Any]", arguments)
            parent_uuid = raw.get("parent_uuid")
            if parent_uuid is not None:
                parent_uuid = str(parent_uuid)
        return await sync_to_async(self._execute_sync, thread_sensitive=True)(target, request, params, parent_uuid)

    @classmethod
    def _execute_sync(
        cls,
        target: RestTarget,
        request: typing.Any,
        params: dict[str, typing.Any],
        parent_uuid: str | None,
    ) -> typing.Any:
        """Instantiate and invoke one existing REST handler operation."""
        if target.parent is not None:
            return cls._execute_detail_sync(target, request, params, parent_uuid)

        method = target.method.value.lower()
        handler = target.handler(request, target.path, method, params, *target.args)
        operation = getattr(handler, method)
        return operation()

    @staticmethod
    def _execute_detail_sync(
        target: RestTarget,
        request: typing.Any,
        params: dict[str, typing.Any],
        parent_uuid: str | None,
    ) -> typing.Any:
        """Execute a ``DetailHandler`` collection scoped to a parent item.

        Mirrors ``ModelHandler.process_detail``: resolve the parent model
        object from its ``{uuid}``, check the parent access level, then
        instantiate the detail handler with that parent so its
        ``get_items``/``query`` run against the parent's queryset.
        """
        from uds.REST.model.master import ModelHandler

        parent_target = typing.cast("RestTarget", target.parent)
        parent_handler: ModelHandler[typing.Any] = typing.cast("type[ModelHandler[typing.Any]]", parent_target.handler)(
            request, parent_target.path, "get", {}, parent_uuid or ""
        )

        try:
            parent_item = parent_handler.MODEL.objects.get(uuid__iexact=parent_uuid or "")
        except Exception as e:
            raise rest_exceptions.NotFound("Parent item not found") from e

        if permissions.has_access(parent_handler._user, parent_item, types.permissions.PermissionType.READ) is False:
            raise rest_exceptions.AccessDenied()

        method = target.method.value.lower()
        path = target.path.replace("{uuid}", parent_uuid or "")
        detail_cls: type[typing.Any] = typing.cast("type[typing.Any]", target.handler)
        detail = detail_cls(
            parent_handler,
            path,
            params,
            *target.args,
            user=parent_handler._user,
            parent_item=parent_item,
        )
        detail._operation = method
        operation = getattr(detail, method)
        return operation()
