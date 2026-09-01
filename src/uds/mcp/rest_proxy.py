"""In-process proxy from MCP capabilities to existing REST handlers."""

import dataclasses
import typing

from asgiref.sync import sync_to_async

from uds.REST.handlers import Handler
from uds.core import types


_ODATA_KEYS: typing.Final[tuple[str, ...]] = (
    "$filter",
    "$orderby",
    "$top",
    "$skip",
    "$select",
    "$expand",
    "$count",
)


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
    """Identify an existing REST handler operation without an HTTP request."""

    handler: type[Handler]
    path: str
    method: types.rest.CustomMethodMethod = types.rest.CustomMethodMethod.GET
    args: tuple[str, ...] = ()


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
        return await sync_to_async(self._execute_sync, thread_sensitive=True)(target, request, params)

    async def execute_collection(
        self,
        target: RestTarget,
        request: typing.Any,
        arguments: typing.Any,
    ) -> typing.Any:
        """Execute a list tool backed by a REST collection.

        Translates the structured MCP arguments into OData ``$...``
        parameters and dispatches ``Handler.query()`` so the existing
        OData validation, permissions and filters are reused.
        """
        params = odata_params_from(arguments)
        return await sync_to_async(self._execute_sync, thread_sensitive=True)(target, request, params)

    @staticmethod
    def _execute_sync(
        target: RestTarget,
        request: typing.Any,
        params: dict[str, typing.Any],
    ) -> typing.Any:
        """Instantiate and invoke one existing REST handler operation."""
        method = target.method.value.lower()
        handler = target.handler(request, target.path, method, params, *target.args)
        operation = getattr(handler, method)
        return operation()
