"""Serve the UDS MCP skill bundle as a downloadable artifact.

The skill bundle is regenerated on every request from the curated
catalog so the source of truth is the broker itself, not a manually
curated artifact. Only ``ROLE.STAFF`` and above may download the bundle
to limit exposure of the MCP endpoint metadata.
"""

import logging
import typing

from uds.core import consts, types
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
        args = self._args
        if len(args) != 1 or args[0] != "mcp":
            raise rest_exceptions.NotFound(f"Unknown skill: {args}")

        builder = SkillBuilder()
        bundle = builder.build()
        logger.debug(
            "Built MCP skill bundle: %s (%d bytes, sha256=%s)",
            bundle.name,
            bundle.size,
            bundle.sha256,
        )
        return bundle.to_download_envelope()
