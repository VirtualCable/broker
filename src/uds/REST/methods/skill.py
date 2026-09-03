"""Serve the UDS MCP skill bundle as a downloadable artifact.

The skill bundle is regenerated on every request from the curated
catalog so the source of truth is the broker itself, not a manually
curated artifact. The bundle is **server-specific**: the absolute URL of
the MCP endpoint is derived from the live request, so the downloaded
configuration is ready to use. Only ``ROLE.STAFF`` and above may
download the bundle to limit exposure of the MCP endpoint metadata.
"""

import logging
import typing

from django.urls import reverse

from uds.core import consts, types
from uds.core.util.config import GlobalConfig
from uds.REST import Handler
from uds.core.exceptions import rest as rest_exceptions
from uds.mcp.skill import SkillBuilder


logger = logging.getLogger(__name__)


class Skill(Handler):
    """Provide the MCP skill bundle for an authenticated staff user."""

    # Default NAME (class name in lower case) plus no PATH means the
    # handler is mounted at the REST root and the URL becomes
    # ``/uds/rest/skill/<name>``. Custom methods provide the
    # ``<name>`` sub-path, so we add a single custom method below.
    PATH: typing.ClassVar[str | None] = None

    ROLE: typing.ClassVar[consts.Role] = consts.Role.STAFF

    CUSTOM_METHODS: typing.ClassVar[list[types.rest.ModelCustomMethod]] = [
        types.rest.ModelCustomMethod(
            "mcp",
            method=types.rest.CustomMethodMethod.GET,
            description="Download the UDS MCP skill bundle for external agents",
        ),
    ]

    def get(self) -> dict[str, typing.Any]:
        """Return the skill bundle as a JSON envelope.

        Custom methods on the Master carry the sub-path as ``self._args``.
        Only the canonical MCP skill is supported in phase 1, so any
        other ``<name>`` produces a 404-like error.
        """
        if not GlobalConfig.MCP_ENABLED.as_bool():
            # Same gate as the MCP endpoint; disabled is indistinguishable
            # from non-existent.
            raise rest_exceptions.NotFound("Not found")

        if self.is_ip_allowed() is False:
            # Same origin policy as the admin interface and the MCP endpoint.
            raise rest_exceptions.AccessDenied()

        args = self._args
        if len(args) != 1 or args[0] != "mcp":
            raise rest_exceptions.NotFound(f"Unknown skill: {args}")

        builder = SkillBuilder(mcp_url=self._mcp_endpoint_url())
        bundle = builder.build()
        logger.debug(
            "Built MCP skill bundle: %s (%d bytes, sha256=%s)",
            bundle.name,
            bundle.size,
            bundle.sha256,
        )
        return bundle.to_download_envelope()

    def _mcp_endpoint_url(self) -> str:
        """Return the absolute URL of the MCP endpoint for this broker.

        ``build_absolute_uri`` derives scheme and host from the request,
        honouring Django's proxy settings (``SECURE_PROXY_SSL_HEADER``,
        ``USE_X_FORWARDED_HOST``), so the bundled URL matches whatever
        the client actually used to reach this broker.
        """
        return self._request.build_absolute_uri(reverse("REST", args=("mcp",)))
