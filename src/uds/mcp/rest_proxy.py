"""In-process proxy from MCP capabilities to existing REST handlers."""

import dataclasses
import typing

from asgiref.sync import sync_to_async

from uds.REST.handlers import Handler
from uds.core import types


@dataclasses.dataclass(frozen=True, slots=True)
class RestTarget:
    """Identify an existing REST handler operation without an HTTP request."""

    handler: type[Handler]
    path: str
    method: types.rest.CustomMethodMethod = types.rest.CustomMethodMethod.GET
    args: tuple[str, ...] = ()


class RestProxy:
    """Invoke existing REST handlers while preserving REST authentication."""

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
